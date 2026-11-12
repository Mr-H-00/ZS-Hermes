# 排队图片消息的前端归属

状态：implemented
类型：simplification
Owner：frontend/src/modules/session/ui/SessionWorkspace.vue

## 问题

多图输入提交后，持久 Input 可能排队；服务端队列快照只提供轻量文本内容。若用快照替换本地乐观消息，图片会在领取 Run 前从页面消失。

## 决策

### 实现方案

发送时构造一次本地用户消息，包含有序 `image_contents`。Web 将它与 Input ID 一起保存在当前 Thread 的 `queuedInputs`；[队列模块](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/session/model/useAgentInputQueue.js)同步持久快照时保留同一条本地消息。Input 被领取后，消息转交当前 Run 的 `msgChunks`；取消或失败时按 Input ID 清理。页面刷新重新读取 PostgreSQL 历史投影，浏览器内引用不承担持久化职责。多图格式和上限由[输入多图决定](./2026-09-25-chat-multi-image.md)拥有。

## 替代方案

| 方案 | 取舍 |
|---|---|
| 每次队列同步重建本地图片消息 | 轻量快照缺少图片，无法可靠重建。 |
| 按请求再建独立图片缓存 | 增加与 Input/消息并行的清理生命周期。 |
| 队列项保留原乐观消息 | 使用现有 Thread 状态和 Input ID，领取时可直接交接。 |

## 后果

同一浏览器会话中，排队与执行交接不丢图片；其他设备和刷新后的显示以持久历史为准。取消排队 Input 不会把本地图片错误交给下一 Turn。

## 验证

Web 单元测试覆盖队列快照同步、Input 领取、图片顺序、取消清理和历史回显；真实浏览器通过 Public Thread 连续发送并刷新回读。旧能力不存在：按 Request ID 维护的图片缓存和派发交接。重新引入条件：出现独立于 Input/Message 生命周期的图片业务需求。
