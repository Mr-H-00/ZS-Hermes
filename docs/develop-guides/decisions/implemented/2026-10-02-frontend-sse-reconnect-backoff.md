# 前端 SSE 重连退避

状态：implemented
类型：bug-fix
Owner：frontend/src/modules/session/model/reconnectBackoff.js

## 问题

主 Thread SSE 断开后固定 1 秒重连，子 Run SSE 固定 2 秒重连。服务端故障或网络中断时，多线程会以固定频率持续请求，造成无必要的连接压力；重连仍需保留现有 cursor 和快照恢复语义。

## 决策

为主 Thread 和子 Run 使用共享的有界指数退避函数。每次收到有效事件后重置尝试次数；断流或连接失败递增尝试次数，延迟从当前基线开始按指数增长并封顶。停止观察、终态和组件清理仍取消 timer，不再安排新连接。

### 实现方案

`model/reconnectBackoff.js` 只计算确定性延迟，不拥有 timer。Thread state 与 child subscription 分别保存自己的尝试计数；SSE owner 在创建 timer 时读取并递增计数。默认基线 1 秒、上限 30 秒，子 Run 仍共享同一策略而不再有独立固定常数。

## 替代方案

- 继续固定间隔：实现简单，但故障期间形成持续重压，不选择。
- 无上限指数退避：最终恢复延迟不可控，用户体验差，不选择。
- 引入第三方重连库：当前只需要一个纯函数，新增依赖和生命周期抽象没有消费者，不选择。

## 验收结果

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 主、子 SSE 使用共享有界退避 | 任一流恢复固定轮询 | `model/reconnectBackoff.js` 与两个 SSE owner | reconnect unit + 现有 SSE 生命周期 unit | 恢复固定 delay，退避断言应失败 | Passed |
| 有效事件会重置重连尝试 | 短暂断流后仍使用历史长延迟 | Thread/child subscription state | SSE lifecycle unit | 禁止重置，连续会话断言应失败 | Passed |
| stop/终态不会留下重连 timer | 组件销毁后后台连接复活 | `useAgentRunStream.js`、`useSubagentRuns.js` | 现有生命周期 unit | 恢复无条件 timer，清理断言应失败 | Passed |

## 后果

服务端故障期间的重连请求从固定 1/2 秒变为共享的有界指数退避，正常收到有效事件后恢复基线；cursor、快照回读、终态收敛和停止观察语义保持不变。

## 验证

- `cd frontend && pnpm exec node --test --test-concurrency=1 test/unit/reconnectBackoff.test.js test/unit/subagentObservation.test.js test/unit/subagentThreadLifecycle.test.js test/unit/agentRunEvents.test.js test/unit/agentInputQueue.test.js`：Passed，32/32。
- `cd frontend && pnpm run lint:check`：Passed。
- `cd frontend && pnpm run build`：Passed。
