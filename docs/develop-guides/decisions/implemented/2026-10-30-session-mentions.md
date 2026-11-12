# 按标题引用对话

状态：implemented
类型：feature
Owner：backend/yuxi/modules/agents/services/mentions.py

## 问题

用户需要在当前消息中选择另一段对话作为参考，并让 Agent 读取其真实历史。现有标题与正文联合搜索不能直接作为只匹配标题的候选来源，引用也需要在同名、改名和刷新后保持明确身份。

## 决策

### 实现方案

Web mention 路由仅接受产品登录 JWT，绑定或未绑定 APP 的 API Key 均返回 403；路由复用 SessionRepository 搜索入口的标题模式，只返回当前用户、Web APP 与活跃状态下的有界标题结果，不读取正文；空查询显示最近十条。前端复用现有选择器，新增对话分组，提供标题高亮、更新时间、加载、空结果与失败重试。异步返回的候选按对象身份保持键盘选择，不能让新到达的对话替代已选文件或技能。

引用保存为 `@session:"thread_id 标题"`，消息保留稳定身份与标题快照，编辑器、历史正文与候选列表显示标题及描边对话气泡，不生成会话头像。标题仅作展示。Agent 直接把 ID 传给已有 `read_thread_messages`，不增加 middleware、工具、历史服务或配置；已有 Memory 开关、用户授权和读取预算继续生效。Input、Receipt、Turn、Run 接收与消费链路保留原语义。

当前用户操作与失败条件由[Agent 运行机制](../../../mechanisms/agent-runtime.md#文件和-memory)说明。标题搜索由 mention service 与 Session repository 拥有，身份与历史读取继续由现有 Memory service/repository 拥有，显示与异步交互由前端 mention 模型及输入组件拥有。

## 替代方案

- 全文搜索后由前端筛标题：先检索正文再截断会遗漏标题匹配，且返回不需要的消息片段。
- 增加专用读取中间件或工具：重复已有历史能力，扩大装配和权限维护面。
- 以标题寻址：同名和改名会改变结果；保存稳定 ID 与标题快照可以直接复用现有消息文本协议。
- 自动注入完整历史：增加不受控输入；现有工具允许按需有界读取。

## 后果

标题是选择时的快照，改名后仍按稳定 ID 读取。用户关闭 Memory 时历史工具继续不可用；归档或不可见会话明确失败，不编造内容。历史数据保持低信任参考。产品不增加跨用户共享、正文候选搜索或目标会话写入。

## 验证

验证在独立 `main` Compose project 完成，使用独立数据库、端口和文件目录。实际容器命令通过本地隔离环境的 Compose 包装入口执行，测试源码及 CI selector 是长期可复现入口。

- `pytest test/integration/services/test_session_mentions.py test/integration/services/test_session_search.py -q`：5 passed。真实 TCP HTTP 与 PostgreSQL 证明仅标题、字面通配符、同名 ID、用户/APP/归档隔离和最近十条；真实 JWT 允许本人读取，匿名及绑定/未绑定 APP 的 full Key 均拒绝。去除 Web 凭据限制时，两组 Key 负控因返回 200 而失败；实际历史工具回读正文，并拒绝其他用户。
- `pytest test/e2e/test_session_mentions_e2e.py -q`：1 passed。仅模型供应商重放；真实 API、PostgreSQL、队列与 worker 将引用 ID 交给已有历史工具，最终 Run 输出与消息、工具审计均从数据库回读。该测试由 `backend/test/run_tests.sh e2e` 选择，标题隔离负控由 `scripts/ci/run_service_integration.sh` 选择。
- 后端 `pytest test/unit -m "not slow" -q`：3495 passed、125 skipped；容器未挂载仓库根目录的配置检查另在宿主执行 `pytest backend/test/unit/config -q`，207 passed。skip 不作为通过证据。
- 前端 `pnpm run test:unit`：990 passed；`pnpm run lint:check`、`pnpm run build`（含 typecheck）通过。引用转义、同名身份与整体删除由相关 unit 断言。
- `frontend/test/browser/sessionMentions.js`：真实完整页面覆盖标题查询、键盘选择、整体删除、加载、失败重试、独立历史渲染，以及浅/深色和 1440/768 px 截图。受控延迟证明新增候选不会改变已选 Skill；定向把异步到达后的选中索引归零，测试因“异步对话结果改变了键盘选中的资源”失败，随后恢复修复并通过正向验证。
- 工程契约及其 56 项测试、后端 Ruff/import/format、文档构建与 `git diff --check` 通过。真实外部模型供应商未执行；确定性 worker 链路与浏览器不替代外部供应商探针。
