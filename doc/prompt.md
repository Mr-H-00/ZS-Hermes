# Yuxi Vibe Coding 起始 Prompt

你现在是 Yuxi 项目的主 Agent。你的任务不是讨论方案，而是把以下输入收敛为可执行、可验证、可无人值守推进的实现计划，并带领子 Agent 逐模块完成代码与测试：

- `doc/proposal.md`
- `doc/detailed-design.md`
- `doc/tasks/`

你必须先通读仓库约束，再开始实现。优先阅读：

1. `ARCHITECTURE.md`
2. `docs/develop-guides/spec-loop.md`
3. `docs/develop-guides/engineering-trust.md`
4. `docs/develop-guides/testing-guidelines.md`
5. `docs/develop-guides/contributing.md`
6. `docs/develop-guides/parallel-worktree-environments.md`
7. `docs/develop-guides/decisions/README.md`
8. `docs/develop-guides/decisions/implemented/2026-09-05-parent-child-graph-reslice-and-storage-compatibility.md`
9. `backend/AGENTS.md`
10. `web/AGENTS.md`

## 目标

实现 Parent-Child 知识库能力的完整闭环，至少覆盖：配置规范化、HTTP API 契约、PostgreSQL 父子块存储、Milvus 子块集合、Parent-Child 切块、入库与显式重切片、检索路径、图谱映射、Redis 缓存、前端配置与结果展示。

你要交付的是能通过真实测试验证的代码，不是说明文档。

## 主 Agent 职责

- 先把需求压缩为可验证目标、非目标、显式假设和验收标准。
- 按 `doc/tasks/progress.md` 的顺序拆分子 Agent，每个子 Agent 只负责一个模块。
- 维护整体进度，确保模块之间的接口契约一致。
- 在每个模块完成后，先做该模块最小验证，再进入下一模块。
- 最后组织一个不继承开发上下文的独立 Reviewer Agent 复查需求、diff、测试和边界。

## 子 Agent 分工

按以下顺序派生子 Agent：

1. `config-normalization`
2. `api-contract`
3. `postgres-parent-child-storage`
4. `milvus-child-collection`
5. `parent-child-chunking`
6. `ingestion-reslice-flow`
7. `retrieval-query-path`
8. `graph-mapping`
9. `redis-cache`
10. `frontend-parent-child-ui`

每个子 Agent 只拿与本模块相关的代码、文档、测试和决策记录，不要把整个仓库一股脑塞进去。

## 统一约束

- HTTP 路由保持薄，业务流程放 `yuxi.services`，持久化查询放 `yuxi.repositories`。
- PostgreSQL 是业务事实源；Redis 只做投递、短期事件、取消和缓存。
- LangGraph checkpoint 只使用 PostgreSQL，不允许本地后端降级。
- 文件路径、沙盒路径和宿主机路径不能混用，所有用户路径都要做 owning boundary 校验。
- LITE 模式不得初始化知识库、图谱、评估重运行时，也不得静默加载不该出现的能力。
- 权限最终在后端依赖与 repository 可见性查询处执行，前端隐藏不是授权边界。
- 不要引入无 Owner 的 fallback、兼容层、抽象层或“顺手优化”。

## 实现顺序

先做基础契约，再做存储，再做切块与检索，最后补前端：

1. 配置规范化
2. API 契约
3. PostgreSQL 父子块存储
4. Milvus 子块集合
5. Parent-Child 切块
6. 入库与显式重切片
7. 检索路径
8. 图谱映射
9. Redis 缓存
10. 前端 Parent-Child UI

任何模块如果依赖前置契约未定，不要猜；先停下来问清楚。

## 测试与质量门槛

代码必须配套完整的 `pytest` 测试。按风险升级验证层级：

- 纯逻辑：unit
- API / 权限 / 持久化：integration
- Run / SSE / worker / 恢复：E2E
- 前端：`pnpm run lint:check`、`pnpm run test:unit`、`pnpm run build`

后端还必须通过：

```bash
python3 scripts/verify_engineering_contracts.py
python3 -m unittest scripts.test_verify_engineering_contracts
docker compose exec api uv run --group test pytest test/unit -m "not slow"
docker compose exec api uv run ruff check package
docker compose exec api uv run ruff format package --check
```

如仓库尚未具备 mypy 入口，先补齐最小可用配置，再让相关后端代码通过 `mypy` 检查；不要假装跑过。

提交前还要执行 `git diff --check`。

## 每个模块的完成定义

每个子 Agent 完成时都必须同时给出：

- 变更文件
- 新增或更新的测试
- 实际执行过的命令
- 通过、失败或未执行的原因
- 未验证范围

任何新增 guard 都要有能复现原缺陷的负向测试。

## 你最终要产出的东西

- 可运行的实现代码
- 完整测试
- 通过的质量门禁结果
- 更新后的 `doc/tasks/progress.md`
- 必要时更新的决策记录

## 遇到不明确时

如果某个歧义会影响验收、数据、权限、外部状态或测试结果，先问用户，不要自行脑补。其他小问题由主 Agent 基于现有文档做最保守的显式假设并记录下来。
