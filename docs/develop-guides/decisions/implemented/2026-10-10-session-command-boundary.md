# 会话命令与上下文面板职责

状态：implemented
类型：architecture
Owner：frontend/src/modules/session/model/sessionCommands.js

## 问题

SessionWorkspace 同时装配页面、管理草稿、解释 Input Receipt 与等待点协议，以及计算和展示完整上下文用量面板。现有 sessionRuntime 已共享历史、SSE 和队列，重复建设运行状态会增加双重事实。

## 决策

### 实现方案

会话命令模块拥有 Input 提交重试与 Receipt 恢复、等待点的 Turn/Run 校验及回答或审批载荷。Workspace 继续装配草稿、附件、滚动和弹窗，调用明确命令并把返回结果交给已有 sessionRuntime 观察。API 封装继续拥有 wire 转换，命令不建立新缓存或运行状态。

保留发送时冻结配置、幂等键、4xx 拒绝恢复、未知接收结果重试，以及等待点过期拒绝。本变更是现有前端重构提案的命令边界阶段；独立嵌入阅读器、完整输入组件和全量 strict TypeScript 是该提案的其他阶段。

## 替代方案

把整个大组件平移进一个接收数十个回调的 composable 会隐藏依赖。重建历史/SSE store 与既有 runtime 重复。按样式或行数拆文件不能明确用户动作的协议 Owner。

## 后果

页面通过显式命令获得接收回执，协议分支可独立验证。后端仍拥有最终授权和持久状态。独立阅读器、完整输入组件和 strict TypeScript 迁移继续保留在原提案中。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| Input 重试保持同一接收事实 | 已接收仍重复发送、查询失败盲目重发 | sessionCommands 与 agentApi | 真实命令unit与浏览器确定性流程 | Receipt成功、404、503、无input_id | Passed |
| 回答和审批绑定当前等待点 | 错Turn/Run或不完整回答被发出 | sessionCommands | 独立协议预期unit与浏览器等待/恢复 | stale run、缺问题、审批数量变化、无run_id | Passed |
| 页面状态与共享订阅保持 | 拆分丢失草稿或关闭队列SSE | Workspace与sessionRuntime | 既有runtime unit、完整frontend lint/unit/build与浏览器 | 协作等待追加、拒绝恢复 | Passed |

验证命令与结果：

- `node --test test/unit/sessionCommands.test.js test/unit/agentRunEvents.test.js`：22 passed。复制命令源码移除 current_run_id 归属判断后，different-run 负向测试因 Missing expected rejection 失败；实际源码保持完整校验。
- `pnpm --dir frontend run lint:check`、`pnpm --dir frontend run test:unit`（537 passed）、`pnpm --dir frontend run build` 通过。
- 一次性 Compose API 容器挂载本分支源码和测试，运行 `pytest test/unit -m "not slow" -q -p no:cacheprovider`：2628 passed、55 skipped。工程 verifier 与 64 项脚本测试通过。
- 独立 Compose 槽中的真实浏览器 `sessionPublicStatus.js` 探针通过问答与工具审批两条链路，包含发送、requires_action、带持久 waitpoint/Turn ID 的恢复、完成后 result_run_id 和 output 归属回读；等待与完成截图已检查。探针等待导航加载和持久终态，DOM 输出提前出现不作为提交完成的证据。
- 外部模型使用确定性 replay。旧夹具要求 13000 字节工具结果被截断，而当前 filesystem 中间件默认不驱逐，曾返回 tool_execution_result_missing；本命令验证改用临时夹具执行 `printf SESSION_APPROVAL_TOOL_OK`，只接受同 call_id 的精确成功输出，保留原认证、模型、技能与流式协议检查。不修改应用或仓库 replay，不据此宣称旧大结果驱逐 E2E 通过。
- `pnpm --dir docs run build`、工程 verifier 与 `git diff --check` 通过。

未执行真实外部模型 provider；浏览器网络故障注入和并发切换等待点未执行，由命令模块负例覆盖。55 项 backend skip 不计通过。独立测试 PostgreSQL 曾进入恢复，恢复且 ready 后执行浏览器，环境异常根因未证实。

## 上下文面板职责收敛

将 Token 用量面板的模板、展开状态、展示计算与样式收敛到 ContextUsagePanel；输入环和面板共用用量计算。组件只接收 usage、compressing、compressionDisabled，并发出 compress，网络操作和运行状态仍由 SessionWorkspace 拥有。保留 1024 展示单位、未知上限和缺失遥测的语义。验证包含前端 unit、lint/build 与真实页面的展开、禁用和用量展示。

最新 main bf9130f7 上重新运行完整前后端测试、工程 verifier/64 unit 和 docs build。真实浏览器再次完成问答及审批恢复；contextUsagePanel.js 验证真实回复用量展示，并用显式组件夹具验证模型累计/缓存、禁用/压缩中、compress事件、缺失遥测及展开状态保持。浅色、深色与375px响应式截图见 PR 交付材料。Workspace 从5496行减少到4587行；总代码量并非等量减少，收益是完整展示职责及其依赖从页面移出。
