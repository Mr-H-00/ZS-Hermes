# 知识库领域命名统一

状态：implemented
类型：simplification
Owner：backend/yuxi/api/routers/knowledge/management.py

## 问题

知识库在产品、ORM 和部分服务中使用 KnowledgeBase，在管理接口、前端状态与调用中使用 database。同一对象的两套名称增加导航成本，并让知识库与持久化数据库混淆。

## 决策

### 实现方案

用户确认系统与消费者均为全新部署。知识库业务对象统一使用 KnowledgeBase、knowledge_base、knowledgeBase；集合使用相应复数。管理资源路径为 `/api/knowledge/knowledge-bases`，创建与更新使用 `name`，列表响应使用 `knowledge_bases`，更新响应使用 `knowledge_base`。前端 API、Store、文件名、组件、状态、事件与引用同步统一。Dashboard 的知识库统计与知识投影删除事件使用同一领域名称。图片代理 URL 的生成和浏览器允许规则同步使用新路径。

不保留旧路径、旧字段或导出别名。`kb_id`、知识库权限、文件状态、查询结果、任务发布顺序与 `knowledge_bases` 表保持现有语义。Milvus 数据库 SDK、Notion database_id、PostgreSQL 连接与时钟、Lucide 图标及 MySQL 工具属于技术或外部契约，保留其名称。历史归档不改写。

## 替代方案

只改内部符号可以降低风险，但保留了两套公开词汇。增加旧接口兼容会引入当前没有消费者的维护路径。按用户确认直接切换全部自有消费者，避免兼容层。

## 后果

前后端、worker、测试与部署需要使用同一版本。已有旧接口消费者、持久化投影删除事件或保存的旧图片链接需要单独迁移；本决定依据全新部署假设，不提供该迁移。没有业务表字段改动，不修改 Schema 版本。

## 验证

旧能力不存在：自有源码不再导出 databaseApi/useDatabaseStore，不注册旧管理与评估路径，不接受 database_name 替代 name。

重新引入条件：出现经确认需要维护的旧消费者时，另立迁移决定，不在当前实现添加预留兼容路径。

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 管理与可见列表使用统一契约，CRUD 与授权结果正确 | 前后端字段不同或权限遗漏 | management 路由、响应装配与 knowledge API | 真实 HTTP knowledge router integration、前端 unit/lint/build | 旧路由不存在；旧创建字段不能替代 name；普通用户不能管理 | Passed |
| 文件操作、图片代理与查询保持内容和归属 | 遗漏嵌套路由、URL 白名单或 worker 调用 | knowledge 文件/投影 services、图片 URL Owner、markdown preview | knowledge integration、相关 E2E 与浏览器操作 | 删除/查询越权、非法图片路径沿用现有拒绝案例 | Passed |
| 自有业务命名收敛，技术数据库契约保留 | 盲目替换第三方 SDK 或遗漏调用方 | Manager、前端 Store 与真实技术连接 | 符号搜索、相关 unit、工程契约、独立 Review | 搜索旧业务 API/Store/路径无命中；Milvus/Notion 既有测试仍通过 | Passed |
| 文档与当前实现一致 | 旧路径或文件引用失效 | 当前知识库文档与决策引用 | 工程契约、相对链接检查、docs build | 构建拒绝失效链接 | Passed |

验证命令与结果（基于 main 的独立整改分支）：

- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts` 通过，后者 64 项。
- 一次性 Compose API 容器挂载本分支源码与测试，执行 `uv run --group test pytest test/unit -m "not slow" -q -p no:cacheprovider`：2622 passed、55 skipped。
- 本分支 `pnpm --dir frontend run lint:check`、`pnpm --dir frontend run test:unit`、`pnpm --dir frontend run build` 通过；完整前端单测 521 项。
- 独立 Compose 运行槽中，管理、评估、权限、文件预览、公开知识工具与 Public key 边界六个真实 HTTP integration 文件：59 passed。首次执行时 PostgreSQL 正在异常重启后的恢复，认证 setup 失败；ready 恢复后重新执行通过，异常重启根因未证实。
- 同槽串行执行 `test/e2e/test_document_parsing_artifacts_e2e.py` 与 `test/e2e/test_milvus_text_retrieval_e2e.py`：3 passed，验证真实 API、worker、PostgreSQL、MinIO、Milvus 和 Neo4j。外部解析与 embedding 协议使用确定性 replay。
- `backend/.venv/bin/ruff check backend/yuxi` 与修改源码的 `ruff format --check` 通过。测试文件原有 lint 问题未扩大处理；名称变长导致的三处新长行已修正。
- `playwright-cli -s=modules-naming --raw run-code --filename=frontend/test/browser/knowledgeBaseNaming.js` 通过：真实 UI 创建、name 请求字段、详情路由、文件夹持久化回读和评估空态，浅色/深色/375px 截图已生成，删除后回读确认探针知识库不存在。探针使用当前页面 origin，并等待创建弹窗关闭后选择列表项。
- `pnpm --dir docs run build` 与 `git diff --check` 通过。

55 项 backend 单测跳过沿用已有测试标记，不代表相关可选能力均已验证。第三方外部知识库的真实服务联调未执行；其已有单测通过。旧消费者、旧持久化投影事件和旧图片 URL 的迁移未执行。
