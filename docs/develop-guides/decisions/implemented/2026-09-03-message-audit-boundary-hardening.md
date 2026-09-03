# 收紧 Message 审计边界

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/repositories/conversation_repository.py

## 问题

Model/Tool 增量审计已经拥有 lifecycle 事实和调试时间线，但普通 History 曾仅凭 Model 行存在 ToolCall 就返回该行，无法证明 Run 已终态且 State 已确认该 operation；返回时还会携带 lifecycle metadata。Model start 重放可能清空同进程已收集的 content，repository 也没有拒绝 sequence 冲突。interrupt 终态还可能沿用 State 中非最后一条已对账 Model 行。

Tool lifecycle 的原始 output envelope 需要用于管理员审计，但 ToolCall 兼容读模型只需要用户内容。线程审计接口和活跃 Run 轮询原先没有数量上限，前端插入未匹配审计行时还会重复扫描和移动数组。

## 决策

- 终态 State 对账时在 Model 行记录显式 `state_reconciled` 证明；普通 History 与显式包含工具的 Memory 只返回终态 Run 中已证明且存在 ToolCall 的兼容行，普通 History 对 Model operation metadata 使用公开字段 allowlist。
- Model start 重放保留同进程聚合状态，持久化层拒绝 sequence 冲突；terminal 只能绑定 State 最后一条 AIMessage 对应的当前 Run 审计行。
- ToolMessage 保留原始 output，ToolCall 只投影 output `content`；缺少 `content` 的 envelope 不进入普通读模型。
- 审计 API 返回最新 500 条时间线并以 `truncated` 声明截断；前端用预分组和单次遍历合并。
- 真实 HTTP 契约测试接入 system-tests workflow；未发布能力不写入已发布 beta changelog。

## 替代方案

- 仅按 `execution_status=completed` 放行 History：Model finish 早于 Run 终态 State，不能证明普通历史归属。
- 清理所有 Model 兼容行：会让 interrupt/resume 后 ToolCall 刷新展示消失。
- 把原始 Tool output 全部写入 ToolCall：会扩大普通 History、Memory 和 Dashboard 的内部协议暴露面。
- 轮询时返回全量时间线：数据量随线程生命期无界增长。

## 后果

- 普通读模型不再把运行中、未完成 State 对账或内部 lifecycle metadata 当作用户历史；工具刷新兼容只保留有终态因果证明的行。
- 管理员调试接口仍能读取原始 Tool output，但单次只返回最新 500 条审计；调用方可用 `truncated` 告知更早事实未包含在响应中。
- 重复 lifecycle 事件保持幂等，但来源键相同而 sequence 冲突会显式失败。
- 前端合并复杂度随消息与审计数量线性增长，不改变 sequence 排序或未匹配审计展示规则。

## 验证

- repository/service unit 覆盖 running Run、终态未证明 Model 行、History metadata allowlist、显式 Memory 工具读取和终态已证明兼容行。
- Model lifecycle/sync unit 覆盖重复 start 保留聚合内容与 monotonic 起点、同 lifecycle 切换 operation ID，以及 interrupt 最后一条 AIMessage 未匹配时不绑定旧审计。
- 真实 PostgreSQL lease integration 覆盖 Model start 的 sequence 冲突和 Tool lifecycle fencing；Tool lifecycle unit 覆盖原始 envelope 仅留在审计 metadata、ToolCall 只投影 `content`，以及缺少 `content` 时不泄露内部字段。
- 审计 repository/service/Web unit 覆盖最新 500 条、`truncated` 和大量未匹配行顺序；真实 HTTP integration 覆盖持久化审计、History 隔离和旧 URL 404，并由 system-tests workflow 的工程契约负向测试守护。
