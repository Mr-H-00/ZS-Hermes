# 知识读取与页面状态归属

状态：implemented
类型：simplification
Owner：frontend/src/modules/session/ui/ReferenceAnnotatedAnswer.vue

## 问题

知识引用复用管理员文件详情，使有知识读取权限的普通用户无法打开原文。连接器的文档管理能力与原文阅读能力混用，飞书正文可读但引用入口不可读。检索页持有与持久配置重复的全局参数，切换知识库后旧值覆盖新库配置；评估工作台的在途请求可以在卸载后重启轮询。

## 决策

### 实现方案

引用通过既有 Public `open_kb_document` 契约读取行窗口，复用 `ExternalDocumentReader.vue`；管理文件详情保持管理员权限。知识类型实现拥有 `supports_documents` 与 `supports_document_read`，HTTP 摘要、MCP 摘要和引用分别传播管理与阅读能力，UI 不按连接器名称猜测阅读能力。`modules/agents/services/references.py` 在工具消息 ID、内容 hash、文件/网页与分块行窗口身份一致且仍可见时，用当前能力刷新历史来源投影，保留持久引用 ID；权限收回或内容改变后不恢复来源。

检索参数由后端持久配置拥有，配置面板只保存未提交草稿。面板以知识库 ID 为组件 key，切库销毁旧草稿；保存成功只确认实际提交的快照，等待期间继续编辑的值仍属未保存草稿。检索测试不再把全局 store 参数当作临时覆盖。`KnowledgeEvaluationWorkspace.vue` 等本轮两个读取全部结束后才安排下一轮，任一失败均不发布部分结果，卸载使在途结果失效；操作完成时若旧读取仍在途，合并成一次后续刷新，避免旧空快照吞掉新任务。

知识库配置提交在表单校验前固定目标库、名称、权限和连接器参数快照；切库关闭并清空父级草稿，校验或检索保存结束时已经切库则停止后续写入。已发出的基础信息请求保持原库归属，迟到响应只能在原库和当前账号仍匹配时发布通知、刷新或解锁页面。

无运行消费者的前端 API 包装、旧方法、固定开关和内部服务注入参数删除。MCP 保留公开稳定错误，并在服务端记录工具名、异常类型与 traceback 供排障。

## 替代方案

放宽管理文件 API 会改变授权边界；为每个库维护前端配置缓存会增加第二份配置真相；保留固定间隔并为每次响应增加序号仍不能避免请求堆积，因此不采用。

## 后果

普通用户与管理员共享只读引用正文契约，文件管理继续拥有独立权限。飞书允许原文阅读，Dify/Notion 仅检索来源仍显示摘录。引用原文使用实时内容，旧引用行号可能随文档重新解析变化，页面明确说明此限制。既有 `supports_documents` 字段保留管理语义，新增阅读字段不会重定义已发布字段。

## 验证

- `node --test test/unit/knowledgeReadingState.test.js test/unit/knowledge_detail_layout.test.js`：16 passed。加载完整 SFC 与真实 Pinia store，覆盖跨库参数、保存期间继续编辑、旧配置实例的迟到读写、飞书/只检索来源、引用行窗口、在途卸载、单边读取失败、串行轮询、操作后合并刷新及父配置提交归属；静态装配检查保证配置组件以知识库 ID 为 key。保存快照与单边读取失败用例均先观察到正确原因失败再修复；渲染宿主替代 DOM，DOM 行为另用浏览器验证。
- `node --test --test-name-pattern='父配置' test/unit/knowledgeReadingState.test.js`：4 passed。真实详情 SFC、检索配置 SFC、Pinia 与 Router 覆盖校验、检索保存和基础信息保存中切库，以及名称/权限提交快照；独立审查探针先复现 A 库草稿写入 B 库。新库读取锁和权限载荷均独立断言，不以请求次数代替结果。
- `node --test test/unit/api_boundary.test.js test/unit/fileGraphFilter.test.js test/unit/background_jobs_polling.test.js`：55 passed。删除无消费者包装后显式更新对应测试调用与模块 mock 导出。
- `pytest backend/test/unit/services/test_knowledge_tool_queries.py backend/test/unit/services/test_turn_references.py backend/test/unit/routers/test_knowledge_mcp.py backend/test/unit/toolkits/test_knowledge_mcp_client.py -q`：100 passed。覆盖能力矩阵、历史 ID 稳定、撤权、内容 hash 变化、同正文不同文件/窗口/分块的来源隔离和 MCP 诊断。
- 隔离 Compose API、PostgreSQL、MinIO 上执行 `pytest backend/test/integration/api/test_knowledge_mcp_delegation.py -k 'standard_user_reads or delegation_reads_persisted' -q`：2 passed。普通用户读持久正文行窗口、管理入口 403、撤权后读取 404、MCP 选择交集均验证最终协议结果。
- `playwright-cli -s=knowledge-simplify run-code --filename=frontend/test/browser/knowledgeReferenceReading.js`：通过。真实 Vite 页面挂载完整引用组件，普通用户权限上下文下只请求 Public 正文接口；验证来源卡片入口、引用行高亮、缩进、分页、错误重试、loading、empty、浅/深色及 1440/1024/768 宽度，生成八张截图（含加载和错误状态）。HTTP 夹具证明前端协议和 DOM，不替代上述真实权限 integration。
- 同一隔离浏览器用确定性 HTTP 夹具验证配置与评估异步路径：提交 5 后继续编辑为 20 仍显示未保存；切库后旧配置读取与保存响应不能覆盖 B 库草稿 21；评估单边 503、另一读取挂起时跨下一轮间隔不重叠，全部结束后恢复轮询。完整详情页还验证 A 检索保存挂起后切到 B 会关闭弹窗、停止后续基础信息写入，重新打开初始化 B 的配置。两张截图分别记录 B 库未保存草稿和 B 库配置弹窗。
- `pnpm run lint:check`、`pnpm run build`：通过；`pnpm run test:unit`：976 passed（228.27 秒）。构建保留既有大 chunk 提示。

旧能力不存在：检索 store 不再维护参数副本；`unifiedApi` 别名、无消费者的 API 包装和固定开关不再存在；引用不再挂载管理员文件详情。

重新引入条件：实际出现独立临时查询参数或第二种组件展示需求，且调用者、状态寿命与验证边界明确。
