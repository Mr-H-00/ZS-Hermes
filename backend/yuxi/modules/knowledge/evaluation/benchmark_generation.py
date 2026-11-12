"""评估基准数据集自动生成：并发 worker 逐题调 LLM，按 attempt 顺序流式产出。

本模块只负责生成编排：chunk 读取经 repositories，向量邻居检索经 kb_manager，
图增强扩展经 KnowledgeGraphService；数据集持久化与作业进度由 evaluation/service 负责。
"""

import asyncio
import json
import random
from collections.abc import AsyncIterator, Callable
from typing import Any

import json_repair

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.runtime import knowledge_base as kb_manager
from yuxi.modules.models import select_model

DEFAULT_BENCHMARK_GENERATION_CONCURRENCY = 10
MAX_BENCHMARK_GENERATION_CONCURRENCY = 20
DEFAULT_GRAPH_EXPAND_TOP_K = 1
MAX_GRAPH_EXPAND_TOP_K = 3
GRAPH_SEED_DECAY = 0.9
GRAPH_PPR_DAMPING = 0.85
GRAPH_PPR_MAX_NODES = 10000

# worker 结束哨兵：与 (attempt_no, item) 报告、异常对象共用 results_queue，不占 attempt 编号
_WORKER_DONE = object()


class EmptyKnowledgeBaseError(ValueError):
    """知识库没有任何可用 chunk，无法生成基准题目。"""


# ---------- 公开入口：批量生成与结果序列化 ----------


async def iter_generated_benchmark_items(
    *,
    kb_id: str,
    count: int,
    neighbors_count: int,
    llm_model_spec: str | None,
    concurrency_count: int = DEFAULT_BENCHMARK_GENERATION_CONCURRENCY,
    generation_mode: str = "vector",
    graph_expand_top_k: int = DEFAULT_GRAPH_EXPAND_TOP_K,
    progress_base: int = 0,
    total_progress: int | None = None,
    progress_cb: Callable[[int, str], Any] | None = None,
    cancel_cb: Callable[[], Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """并发生成 count 条基准题目，严格按 attempt 派发顺序流式 yield。

    固定 worker 池从 attempt 队列领号，各执行一次“选锚点—组上下文—调 LLM”；
    报告先入 reorder 缓冲，游标只在编号连续时前移，乱序完成不改变产出顺序。
    单次尝试失败只消耗尝试预算；worker 异常或取消时终止其余 worker，缓冲中
    已产出的题目按序补发后原样上抛。取消经 cancel_cb 在每次领号前检查，
    CancelledError 与业务异常走同一条 worker 报告路径。
    """
    if progress_cb:
        await progress_cb(5, "加载chunks")

    all_chunks = await collect_kb_chunks(kb_id)
    if not all_chunks:
        raise EmptyKnowledgeBaseError("No chunks found in knowledge base")
    chunks_by_id = {str(chunk["id"]): chunk for chunk in all_chunks if chunk.get("id") is not None}

    if generation_mode not in {"vector", "graph_enhanced"}:
        raise ValueError("Unsupported benchmark generation mode")
    graph_expand_top_k = normalize_graph_expand_top_k(graph_expand_top_k)

    if progress_cb:
        await progress_cb(15, "准备生成样本")

    if not llm_model_spec:
        raise ValueError("llm_model_spec 不能为空")

    llm = select_model(model_spec=llm_model_spec)
    context_count = max(clamp_neighbors_count(neighbors_count), 1)
    # 尝试预算：每条题目 5 次机会且总量不低于 50，失败尝试只消耗预算不产出
    max_attempts = max(count * 5, 50)
    worker_count = normalize_generation_concurrency_count(concurrency_count)
    actual_worker_count = min(worker_count, max(count, 1), max_attempts)
    generated = 0
    # attempt_queue 只分发编号；workers 的三类报告（元组/异常/结束哨兵）统一走 results_queue
    results_queue: asyncio.Queue = asyncio.Queue()
    state_lock = asyncio.Lock()
    attempt_queue: asyncio.Queue[int] = asyncio.Queue()

    for attempt_no in range(max_attempts):
        attempt_queue.put_nowait(attempt_no)

    # worker 闭包集中读写上方共享状态（队列、锁、生成计数），拆到模块级需透传全部上下文，得不偿失
    async def worker() -> None:
        """单 worker 循环：领号—生成—回报；任何退出路径都先落报告再结束。"""
        nonlocal generated
        try:
            while True:
                if cancel_cb:
                    # 取消检查放在领号前，保证每次新尝试开始前都能响应取消
                    await cancel_cb()
                async with state_lock:
                    if generated >= count:
                        return
                try:
                    attempt_no = attempt_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    item = await _generate_benchmark_item_once(
                        kb_id=kb_id,
                        all_chunks=all_chunks,
                        llm=llm,
                        context_count=context_count,
                        generation_mode=generation_mode,
                        graph_expand_top_k=graph_expand_top_k,
                        chunks_by_id=chunks_by_id,
                    )
                    progress = None
                    message = None
                    if item is not None:
                        async with state_lock:
                            if generated >= count:
                                # 并发超额产出：该 attempt 计为 None，仍需回报才能推进重排游标
                                item = None
                            else:
                                generated += 1
                                if progress_cb:
                                    # 进度封顶 99，100 由调用方在全部生成并落库后设置
                                    total_val = total_progress if total_progress is not None else (progress_base + count)
                                    progress = int(99 * (progress_base + generated) / max(total_val, 1))
                                    message = f"已生成 {progress_base + generated}/{total_val}"
                    # 先入队再上报进度：慢回调不阻塞结果分发，回调异常仍作为 worker 异常传播
                    await results_queue.put((attempt_no, item))
                    if progress_cb and progress is not None:
                        await progress_cb(progress, message)
                finally:
                    attempt_queue.task_done()
        except asyncio.CancelledError as exc:
            await results_queue.put(exc)
            raise
        except Exception as exc:
            await results_queue.put(exc)
        finally:
            await results_queue.put(_WORKER_DONE)

    workers = [asyncio.create_task(worker()) for _ in range(actual_worker_count)]
    done_workers = 0
    received = 0
    next_attempt = 0
    reorder: dict[int, dict[str, Any] | None] = {}
    pending_error: BaseException | None = None
    completed = False
    try:
        while done_workers < actual_worker_count and received < count:
            report = await results_queue.get()
            if report is _WORKER_DONE:
                done_workers += 1
                continue
            if isinstance(report, BaseException):
                pending_error = report
                break
            attempt_no, item = report
            reorder[attempt_no] = item
            # 只在编号连续时前移游标，乱序完成的报告滞留缓冲，保证产出顺序与派发顺序一致
            while next_attempt in reorder:
                resolved = reorder.pop(next_attempt)
                next_attempt += 1
                if resolved is None:
                    continue
                received += 1
                yield resolved
        completed = pending_error is None
    finally:
        # 正常结束等 worker 自然退出；提前退出（异常或消费方中断）取消全部 worker 再回收
        if not completed:
            for task in workers:
                task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    if pending_error is not None:
        # worker 已回收，残余报告中已产出的题目按 attempt 序补发，最后原样上抛首个异常
        while not results_queue.empty():
            pending = results_queue.get_nowait()
            if pending is _WORKER_DONE or isinstance(pending, BaseException):
                continue
            attempt_no, item = pending
            reorder[attempt_no] = item
        for attempt_no in sorted(reorder):
            if reorder[attempt_no] is not None:
                yield reorder[attempt_no]
        raise pending_error


def dump_benchmark_item(item: dict[str, Any]) -> str:
    """序列化为 JSONL 单行：紧凑 JSON 加换行符。"""
    return json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"


# ---------- 输入准备：chunk 加载与参数归一化 ----------


async def collect_kb_chunks(kb_id: str) -> list[dict[str, Any]]:
    """读取知识库全部 chunk，投影为生成流程使用的字段字典。"""
    from yuxi.modules.knowledge.repositories.chunks import KnowledgeChunkRepository

    return [
        {
            "id": chunk.chunk_id,
            "content": chunk.content or "",
            "file_id": chunk.file_id,
            "chunk_index": chunk.chunk_index,
            "graph_indexed": bool(chunk.graph_indexed),
            "ent_ids": chunk.ent_ids or [],
            "tags": chunk.tags or [],
            "extraction_result": chunk.extraction_result,
        }
        for chunk in await KnowledgeChunkRepository().list_by_kb_id(kb_id)
    ]


def clamp_neighbors_count(neighbors_count: int) -> int:
    """邻居片段数收敛到 [0, 10]。"""
    return min(max(neighbors_count, 0), 10)


def normalize_generation_concurrency_count(value: Any) -> int:
    """并发 worker 数归一化：空值取默认，其余收敛到 [1, 上限]。"""
    if value in (None, ""):
        return DEFAULT_BENCHMARK_GENERATION_CONCURRENCY
    return min(max(1, int(value)), MAX_BENCHMARK_GENERATION_CONCURRENCY)


def normalize_graph_expand_top_k(value: Any) -> int:
    """图增强每轮扩展片段数归一化：空值取默认，其余收敛到 [1, 上限]。"""
    if value in (None, ""):
        return DEFAULT_GRAPH_EXPAND_TOP_K
    return min(max(1, int(value)), MAX_GRAPH_EXPAND_TOP_K)


# ---------- 单次尝试：选锚点、组上下文、调 LLM ----------


async def _generate_benchmark_item_once(
    *,
    kb_id: str,
    all_chunks: list[dict[str, Any]],
    llm: Any,
    context_count: int,
    generation_mode: str,
    graph_expand_top_k: int,
    chunks_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """执行一次生成尝试，产出不合法返回 None 交由尝试预算重试。

    graph_enhanced 模式缺少可用图锚点时抛 ValueError，直接终止整体生成。
    """
    if generation_mode == "graph_enhanced":
        # 锚点必须已建图索引且抽取到实体，否则 PPR 无种子可用
        graph_anchor_chunks = [chunk for chunk in all_chunks if chunk.get("graph_indexed") is True and _chunk_entity_ids(chunk)]
        if not graph_anchor_chunks:
            raise ValueError("No graph indexed chunks with entities found in knowledge base")
        anchor_chunk = random.choice(graph_anchor_chunks)
        ctx_chunks = await select_graph_enhanced_chunks(
            kb_id=kb_id,
            anchor_chunk=anchor_chunk,
            chunks_by_id=chunks_by_id,
            context_count=context_count,
            graph_expand_top_k=graph_expand_top_k,
        )
        if ctx_chunks is None:
            return None
    else:
        anchor_chunk = random.choice(all_chunks)
        neighbor_chunks = await select_neighbor_chunks_by_kb_query(
            kb_id=kb_id,
            anchor_chunk=anchor_chunk,
            neighbors_count=context_count - 1,
        )
        ctx_chunks = [anchor_chunk] + neighbor_chunks
    ctx_items = [(chunk["id"], chunk["content"]) for chunk in ctx_chunks]
    allowed_ids = {cid for cid, _ in ctx_items}

    try:
        resp = await llm.call(build_benchmark_generation_prompt(ctx_items), False)
        obj = json_repair.loads(resp.content if resp else "")
        query = obj.get("query")
        answer = obj.get("gold_answer")
        gold_ids = obj.get("gold_chunk_ids")
        if not query or not answer or not isinstance(gold_ids, list):
            logger.warning(f"Generated JSON missing fields or invalid format: {obj}")
            return None

        gold_ids = [str(item) for item in gold_ids if str(item) in allowed_ids]
        if not gold_ids:
            logger.warning("Generated gold_chunk_ids not found in allowed context")
            return None

        return {"query": query, "gold_chunk_ids": gold_ids, "gold_answer": answer}
    except Exception as e:
        # 单次 LLM 调用或解析失败只消耗一次尝试，不终止整体生成
        logger.warning(f"Benchmark generation failed for one item: {e}")
        return None


def build_benchmark_generation_prompt(ctx_items: list[tuple[str, str]]) -> str:
    """构造“基于上下文生成问题与标准答案”的 JSON 输出提示词。"""
    context_text = "\n\n".join([f"片段ID={cid}\n{content}" for cid, content in ctx_items])
    return (
        "你将基于以下上下文生成一个可由上下文准确回答的问题与标准答案。"
        "仅返回一个JSON对象，不要包含其他文字。"
        "键为 query、gold_answer、gold_chunk_ids。gold_chunk_ids 必须是上述上下文片段的ID子集。\n\n"
        "上下文：\n" + context_text + "\n"
    )


# ---------- 上下文片段选择：向量邻居与图增强扩展 ----------


async def select_neighbor_chunks_by_kb_query(
    *,
    kb_id: str,
    anchor_chunk: dict[str, Any],
    neighbors_count: int,
) -> list[dict[str, Any]]:
    """以锚点内容做向量检索，取排除锚点后的前 neighbors_count 个邻居片段。"""
    if neighbors_count <= 0:
        return []

    anchor_content = anchor_chunk.get("content", "")
    if not anchor_content:
        return []

    candidates = await kb_manager.aquery(
        anchor_content,
        kb_id,
        search_mode="vector",
        final_top_k=neighbors_count + 3,  # 过量检索，弥补锚点自身与无效候选被过滤后的缺口
        use_reranker=False,
        similarity_threshold=0.0,
    )

    chunks = []
    for candidate in candidates:
        if _is_anchor_chunk(candidate, anchor_chunk):
            continue

        metadata = candidate.get("metadata") or {}
        chunk_id = metadata.get("chunk_id")
        content = candidate.get("content", "")
        if not chunk_id or not content:
            continue

        chunks.append(
            {
                "id": str(chunk_id),
                "content": content,
                "file_id": metadata.get("file_id"),
                "chunk_index": metadata.get("chunk_index"),
            }
        )
        if len(chunks) >= neighbors_count:
            break

    return chunks


async def select_graph_enhanced_chunks(
    *,
    kb_id: str,
    anchor_chunk: dict[str, Any],
    chunks_by_id: dict[str, dict[str, Any]],
    context_count: int,
    graph_expand_top_k: int,
) -> list[dict[str, Any]] | None:
    """以锚点实体为种子做 PPR 迭代扩展，凑齐 context_count 个片段。

    无实体种子或扩展无进展时返回 None，本次尝试按失败计入预算重试。
    """
    if context_count <= 1:
        return [anchor_chunk]

    from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService

    anchor_entity_ids = _chunk_entity_ids(anchor_chunk)
    if not anchor_entity_ids:
        return None

    graph_service = KnowledgeGraphService()
    selected = [anchor_chunk]
    selected_ids = {str(anchor_chunk.get("id"))}
    seed_weights = {entity_id: 1.0 for entity_id in anchor_entity_ids}
    round_index = 1

    while len(selected) < context_count:
        # 锚点实体每轮重置满权重，扩展实体按轮次衰减，使扩散始终围绕锚点
        for entity_id in anchor_entity_ids:
            seed_weights[entity_id] = 1.0

        ranked_chunks = await graph_service.query_and_rank_chunks_by_ppr(
            kb_id,
            seed_weights,
            max_nodes=GRAPH_PPR_MAX_NODES,
            top_k=max(context_count * 5, 20),  # 过量候选保证每轮有足够未选片段可引入
            damping=GRAPH_PPR_DAMPING,
        )
        if not ranked_chunks:
            return None

        new_chunks = []
        for chunk_id, _ in ranked_chunks:
            chunk_id = str(chunk_id)
            if chunk_id in selected_ids:
                continue
            chunk = chunks_by_id.get(chunk_id)
            if chunk is None:
                continue
            new_chunks.append(chunk)
            # 每轮最多引入 graph_expand_top_k 个新片段，且不超过剩余名额
            if len(new_chunks) >= min(graph_expand_top_k, context_count - len(selected)):
                break

        if not new_chunks:
            return None

        new_weight = GRAPH_SEED_DECAY**round_index
        for chunk in new_chunks:
            selected.append(chunk)
            selected_ids.add(str(chunk.get("id")))
            # 同一实体保留历史最高权重，后续轮次不覆盖更早的强信号
            for entity_id in _chunk_entity_ids(chunk):
                seed_weights[entity_id] = max(seed_weights.get(entity_id, 0.0), new_weight)
        round_index += 1

    return selected


def _chunk_entity_ids(chunk: dict[str, Any]) -> list[str]:
    """读取 chunk 的非空实体 ID 列表。"""
    return [str(entity_id) for entity_id in chunk.get("ent_ids") or [] if entity_id]


def _is_anchor_chunk(candidate: dict[str, Any], anchor_chunk: dict[str, Any]) -> bool:
    """判断检索候选是否为锚点自身：先按 chunk_id，缺失时回退 (file_id, chunk_index)。"""
    metadata = candidate.get("metadata") or {}
    candidate_id = metadata.get("chunk_id")
    if candidate_id is not None and str(candidate_id) == str(anchor_chunk.get("id")):
        return True

    candidate_file_id = metadata.get("file_id")
    candidate_chunk_index = metadata.get("chunk_index")
    return candidate_file_id == anchor_chunk.get("file_id") and candidate_chunk_index == anchor_chunk.get("chunk_index")
