"""后台动作的 worker 执行、租约与领域终态适配。"""

from __future__ import annotations

import asyncio
import uuid
from functools import partial
from typing import Any

from yuxi.bootstrap.capabilities import knowledge_enabled
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.background_jobs.dispatch import (
    BACKGROUND_JOB_DEFAULT_TIMEOUT_SECONDS,
    publish_job,
)
from yuxi.modules.background_jobs.registry import get_job_definition
from yuxi.modules.background_jobs.repository import TERMINAL_JOB_STATUSES, BackgroundJobRepository
from yuxi.workers.background_job_context import BackgroundJobContext

JOB_LEASE_SECONDS = 30.0
JOB_HEARTBEAT_SECONDS = 10.0
BACKGROUND_JOB_MAX_RUNNING = 4
KNOWLEDGE_JOB_MODULE_PREFIX = "yuxi.modules.knowledge."


async def process_background_job(ctx: dict[str, Any], job_id: str) -> None:
    """从 PG 作业 意图重建并执行一个注册 Handler。"""
    repo = BackgroundJobRepository()
    record = await repo.get_by_id(job_id)
    if record is None or record.status in TERMINAL_JOB_STATUSES:
        return

    handler_version = 1 if record.handler_version is None else int(record.handler_version)
    try:
        definition = get_job_definition(record.type, handler_version)
    except ValueError as exc:
        logger.error("后台作业 has unknown Handler metadata: job_id={}, type={}", job_id, record.type)
        if record.status == "pending":
            await _fail_unreconstructable_job(repo, record, str(exc))
        return
    if record.cancel_requested:
        failure_handler = definition.load_failure_handler()
        before_cancel = partial(failure_handler, error="任务已取消") if failure_handler is not None else None
        await repo.confirm_pending_cancel(job_id, before_cancel=before_cancel)
        return

    if not knowledge_enabled() and definition.module.startswith(KNOWLEDGE_JOB_MODULE_PREFIX):
        return

    process_identity = str(ctx.get("worker_id") or "job-worker")
    owner = f"{process_identity}:{uuid.uuid4().hex}"
    record, claimed = await repo.claim(
        job_id,
        worker_id=owner,
        lease_seconds=JOB_LEASE_SECONDS,
        max_running=BACKGROUND_JOB_MAX_RUNNING,
    )
    if not claimed or record is None:
        return

    context = BackgroundJobContext(job_id, owner, record.payload or {})
    execution_holder: list[asyncio.Task[Any] | None] = [None]
    shutdown_requested = asyncio.Event()
    attempt = asyncio.create_task(
        _run_claimed_job(
            repo,
            job_id=job_id,
            record=record,
            definition=definition,
            owner=owner,
            context=context,
            execution_holder=execution_holder,
            shutdown_requested=shutdown_requested,
        ),
        name=f"durable-job-attempt:{job_id}",
    )
    parent_cancelled = False
    while not attempt.done():
        try:
            await asyncio.shield(attempt)
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if current_task is None or current_task.cancelling() == 0:
                raise
            if not parent_cancelled:
                parent_cancelled = True
                shutdown_requested.set()
                execution = execution_holder[0]
                if execution is not None and not execution.done() and execution.cancelling() == 0:
                    execution.cancel()

    attempt.result()
    if parent_cancelled:
        raise asyncio.CancelledError


async def _run_claimed_job(
    repo: BackgroundJobRepository,
    *,
    job_id: str,
    record: Any,
    definition: Any,
    owner: str,
    context: BackgroundJobContext,
    execution_holder: list[asyncio.Task[Any] | None],
    shutdown_requested: asyncio.Event,
) -> None:
    """执行并收敛一个已取得 lease 的 作业 attempt。"""

    attempt_finished = asyncio.Event()
    heartbeat = asyncio.create_task(
        _heartbeat_job(context, execution_holder, attempt_finished),
        name=f"durable-job-heartbeat:{job_id}",
    )
    failure_handler = None
    try:
        try:
            failure_handler = definition.load_failure_handler()
            success_handler = definition.load_success_handler()
            handler = definition.load_handler()
        except Exception as exc:
            finished = await _finish_job_failure(
                repo,
                job_id=job_id,
                owner=owner,
                status="failed",
                message="任务 Handler 无法加载",
                error=str(exc),
                failure_handler=failure_handler,
            )
            if not finished:
                context._request_cancel("lease_lost")
                logger.warning("作业 Handler load failure could not settle after ownership changed: job_id={}", job_id)
            return

        execution = asyncio.create_task(handler(context), name=f"durable-job:{job_id}")
        execution_holder[0] = execution
        if shutdown_requested.is_set() or context.cancellation_reason is not None:
            execution.cancel()

        try:
            timeout_seconds = float(record.timeout_seconds or BACKGROUND_JOB_DEFAULT_TIMEOUT_SECONDS)
            done, _ = await asyncio.wait({execution}, timeout=timeout_seconds)
            if execution not in done:
                context._request_cancel("timeout")
                if execution.cancelling() == 0:
                    execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                finished = await _finish_job_failure(
                    repo,
                    job_id=job_id,
                    owner=owner,
                    status="failed",
                    message="任务执行超时",
                    error=f"作业 exceeded the {timeout_seconds:g}-second execution timeout",
                    failure_handler=failure_handler,
                )
                if not finished:
                    context._request_cancel("lease_lost")
                    logger.warning("Timed-out 作业 could not settle after ownership changed: job_id={}", job_id)
                return

            result = await execution
            before_finish = partial(success_handler, result=result) if success_handler is not None else None
            before_cancel = partial(failure_handler, error="任务已取消") if failure_handler is not None else None
            finished = await repo.finish_owned(
                job_id,
                worker_id=owner,
                status="success",
                message="任务已完成",
                result=result,
                before_finish=before_finish,
                before_cancel=before_cancel,
            )
            if not finished:
                context._request_cancel("lease_lost")
                logger.warning("Successful 作业 could not settle after ownership changed: job_id={}", job_id)
        except asyncio.CancelledError:
            if not execution.done() and execution.cancelling() == 0:
                execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            if context.cancellation_reason == "cancelled":
                finished = await _finish_job_failure(
                    repo,
                    job_id=job_id,
                    owner=owner,
                    status="cancelled",
                    message="任务已取消",
                    error="任务已取消",
                    failure_handler=failure_handler,
                )
                if not finished:
                    context._request_cancel("lease_lost")
                    logger.warning("Cancelled 作业 could not settle after ownership changed: job_id={}", job_id)
            elif context.cancellation_reason != "lease_lost":
                shutdown_error = "worker_shutdown: worker 停止时任务中断"
                before_fail = partial(failure_handler, error=shutdown_error) if failure_handler is not None else None
                released_status = await repo.release_interrupted_owner(
                    job_id,
                    worker_id=owner,
                    error=shutdown_error,
                    before_fail=before_fail,
                )
                if released_status is None:
                    context._request_cancel("lease_lost")
                    logger.warning("Interrupted 作业 could not settle after ownership changed: job_id={}", job_id)
        except Exception as exc:
            logger.exception("后台作业 failed: job_id={}, type={}", job_id, record.type)
            finished = await _finish_job_failure(
                repo,
                job_id=job_id,
                owner=owner,
                status="failed",
                message="任务执行失败",
                error=str(exc),
                failure_handler=failure_handler,
            )
            if not finished:
                context._request_cancel("lease_lost")
                logger.warning("Failed 作业 could not settle after ownership changed: job_id={}", job_id)
    finally:
        attempt_finished.set()
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        await _publish_pending_after_slot_release()


async def _fail_unreconstructable_job(repo, record, error: str) -> None:
    """未知 Handler 的持久意图显式失败，保留匹配版本的领域失败收敛。"""
    failure_handler = _failure_handler_for(record)
    before_fail = partial(failure_handler, error=error) if failure_handler is not None else None
    await repo.fail_pending(record.id, error=error, before_fail=before_fail)


def _failure_handler_for(record):
    """评估作业的未知版本仍按稳定的领域身份收敛失败。"""
    try:
        definition = get_job_definition(record.type, int(record.handler_version))
    except ValueError:
        if record.type not in {"dataset_generation", "rag_evaluation"}:
            return None
        definition = get_job_definition(record.type)
    return definition.load_failure_handler()


async def finalize_job_failure(session, record, error: str) -> None:
    """在 BackgroundJob 失败事务内执行已注册的领域收敛。"""
    handler = _failure_handler_for(record)
    if handler is not None:
        await handler(session, record, error)


async def reconcile_and_publish_jobs() -> list[tuple[str, str, int]]:
    """收敛失联 owner，并发布所有当前 pending BackgroundJob。"""
    repository = BackgroundJobRepository()
    reconciled = await repository.reconcile_expired_leases(before_fail=finalize_job_failure)
    await publish_pending_jobs()
    await repository.prune_terminal()
    return reconciled


async def publish_pending_jobs(*, limit: int = 200) -> list[str]:
    """重发 PG 中待执行的任务；重复 ARQ 消息由 job_id/attempt 去重。"""
    published: list[str] = []
    repository = BackgroundJobRepository()
    for record in await repository.list_pending(limit=limit):
        handler_version = int(record.handler_version)
        if record.cancel_requested:
            failure_handler = _failure_handler_for(record)
            before_cancel = partial(failure_handler, error="任务已取消") if failure_handler is not None else None
            await repository.confirm_pending_cancel(record.id, before_cancel=before_cancel)
            continue
        try:
            get_job_definition(record.type)
        except ValueError as exc:
            logger.error("Cannot publish unknown durable job: job_id={}, type={}", record.id, record.type)
            await repository.fail_pending(record.id, error=str(exc))
            continue
        try:
            definition = get_job_definition(record.type, handler_version)
        except ValueError as exc:
            error = str(exc)
            logger.error("Cannot rebuild durable job: job_id={}, type={}", record.id, record.type)
            failure_handler = _failure_handler_for(record)
            before_fail = partial(failure_handler, error=error) if failure_handler is not None else None
            await repository.fail_pending(record.id, error=error, before_fail=before_fail)
            continue
        if not knowledge_enabled() and definition.module.startswith(KNOWLEDGE_JOB_MODULE_PREFIX):
            continue
        await publish_job(record.id)
        published.append(record.id)
    return published


async def _heartbeat_job(
    context: BackgroundJobContext,
    execution_holder: list[asyncio.Task[Any] | None],
    attempt_finished: asyncio.Event,
) -> None:
    """在 claimed attempt 完整收敛前续租，并只向 Handler 传递取消。"""

    repo = BackgroundJobRepository()
    while not attempt_finished.is_set():
        try:
            await asyncio.wait_for(attempt_finished.wait(), timeout=JOB_HEARTBEAT_SECONDS)
        except TimeoutError:
            pass
        else:
            return
        try:
            renewed, cancel_requested = await repo.renew_lease(
                context.job_id,
                worker_id=context.worker_id,
                lease_seconds=JOB_LEASE_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("作业 heartbeat failed; cancelling owner: job_id={}", context.job_id)
            context._request_cancel("lease_lost")
            execution = execution_holder[0]
            if execution is not None and not execution.done() and execution.cancelling() == 0:
                execution.cancel()
            return
        if not renewed:
            context._request_cancel("lease_lost")
            execution = execution_holder[0]
            if execution is not None and not execution.done() and execution.cancelling() == 0:
                execution.cancel()
            return
        if cancel_requested:
            context._request_cancel("cancelled")
            # 用户取消在领域安全点响应；续租持续到终态和清理完成。

            # 取消由 Handler 的安全检查点响应，heartbeat 继续维持有效执行权。


async def _finish_job_failure(
    repo: BackgroundJobRepository,
    *,
    job_id: str,
    owner: str,
    status: str,
    message: str,
    error: str,
    failure_handler,
) -> bool:
    """在有效租约内收敛领域失败并返回最终提交是否成功。"""
    before_finish = partial(failure_handler, error=error) if failure_handler is not None else None
    return await repo.finish_owned(
        job_id,
        worker_id=owner,
        status=status,
        message=message,
        error=error,
        before_finish=before_finish,
    )


async def _publish_pending_after_slot_release() -> None:
    """释放 后台作业 槽后接力唤醒 pending intent。"""
    try:
        await publish_pending_jobs(limit=BACKGROUND_JOB_MAX_RUNNING)
    except Exception:
        logger.opt(exception=True).error("Failed to publish pending jobs after slot release")
