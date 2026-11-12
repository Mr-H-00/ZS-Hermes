# 知识库模块按文件行为等价优化

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/knowledge/base.py

## 问题

knowledge 模块长期增量演化后存在普遍的可读性债务：主入口沉在文件底部、内部辅助穿插在公开方法之间、少量死代码与双源常量、部分文件缺中文 docstring 与设计约束注释。逐文件审查的成本高，且人工单遍扫过容易遗漏。

## 决策

对模块全部 78 个 Python 文件做一次逐文件、行为等价的代码质量整理：大文件（≥200 行）每文件派一个独立子智能体按统一准则处理，短文件由协调会话亲自处理，跨文件联动统一由协调会话落地。准则优先级：自顶向下组织（公开入口/核心入口在前、按业务流排列、内部辅助成组在后）、认知复杂度优先、克制抽象与防御、保持行为与最小修改范围。

### 实现方案

- 类内方法按业务流重排并加中文分节注释：base.py（40 方法）、manager.py（九阶段）、implementations/milvus.py 与 notion.py 对齐基类业务流、graphs/service.py、evaluation/service.py（六段）、repositories 的 files/graphs/chunks/evaluation/bases。
- 分块器（general/qa/laws/book/separator/semantic）与工具模块（url_fetcher、url_validator、sample_question_utils、evaluator）主入口前置、辅助后置成组。
- 死代码删除（均经全仓库引用核查）：repositories/files.py 的 `lock_file_tree`/`_iter_batches`/本文件 `SQL_IN_BATCH_SIZE`、repositories/evaluation.py 的 `delete_all`、background_jobs.py 的不可达 `not_processed` 分支、milvus.py 的死三元与死默认值等。
- 双源收敛：directories.py 的 512 名称预算改绑定参数引用 `names.MAX_NAME_LENGTH`；imports.py 错误文案改 f-string 引用 `MAX_IMPORT_ITEMS`；feishu/dify 的 top_k 上下限常量化。
- 异常类型化：`benchmark_generation.EmptyKnowledgeBaseError(ValueError)` 替代 service.py 对空库错误的消息文本匹配（子类化保持既有 `except ValueError` 兼容）。
- 删除运行时不可达的能力声明：NotionKB 的 `open_file_content`/`find_file_content` 覆写带必填 `additional_params` 而所有运行时调用方均不传参，仅被单测直连调用；连同仅为它们存在的页面缓存三件套、`retrieve_page` 端点、`allow_unknown_parent` 死参数与 3 个直测一并删除，`description` 同步收敛为"支持检索"。
- 补齐缺失的中文 docstring、设计约束注释与若干失真注解；nlp/semantic_utils/qa/kb_utils/names 等以新旧实现差分对拍（数百至两万例输入）证明输出逐字节一致。

## 替代方案

- 按目录/主题批量处理（第一轮的实际做法）：单遍扫过会遗漏大量文件与自顶向下问题，已被用户否决。
- 顺手修复审查中发现的既有行为问题（NotionKB 打开/查找覆写运行时不可达、RAGFlow 移植的 `make_colon_as_title` 不可达分支、cache/manager 的重复防御收敛）：涉及行为变更与产品决策，本轮明确不做，仅在报告中记录。

## 后果

- 模块内读者从各文件顶部即可按业务流理解职责，方法定位不再需要跨文件跳转。
- 测试 monkeypatch 接缝（函数内延迟导入、被 patch 的类/模块属性）全部原位保留，是后续重构必须继续遵守的约束。
- 新增 `EmptyKnowledgeBaseError` 为评估域公开异常；空库判定从消息文本耦合改为类型判定。
- 删除的方法（`lock_file_tree`、`_iter_batches`、`delete_all`）无任何调用方，不构成兼容性承诺变化。

## 验证

- `uv run --no-sync --group test ruff check/format --check yuxi/modules/knowledge/`：78 文件全通过。
- `pytest test/unit -m "not slow"`：3395 passed / 0 failed（较本轮开始前基线 3398 少 3 个，即删除的 Notion 死覆写直测）。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts`：通过（288 decisions / 453 docs / 41 routers，66 tests OK）。
- 行为等价：nlp/qa/semantic/kb_utils/names/base/graphs-repo/extractors-base 等以新旧差分对拍证明输出、异常类型与消息逐字节一致；models.py 以 DDL 逐行比对证明数据库契约不变；schemas.py 以 `model_json_schema()` 全量对比证明 API 契约不变。
- 旧能力不存在：`lock_file_tree`/`_iter_batches`/`delete_all`/`not_processed` 分支、空库错误的消息文本匹配、NotionKB 的打开/页内查找覆写及其页面缓存与 `retrieve_page` 已从代码中删除，无替代物并存；`_unescape_delimiter` 已公开命名为 `unescape_delimiter`，旧私有名不存在。
- 重新引入条件：若需要恢复并发目录树锁（advisory lock 之前的实现）、评估数据全量清空、按消息文本区分空库错误或 Notion 文档打开/页内查找能力（应仿照 FeishuKB 提供 `open_document` 覆写并打开 `supports_document_read`），须重新设计并以新的决策记录说明。
