# 内部恢复输入的聊天展示边界

状态：implemented
类型：bug-fix
Owner：frontend/src/modules/session/model/agentItems.js

## 问题

协作等待完成后，服务保存 role=user、message_type=resume 的内部恢复记录。历史消息展示已过滤该类型，实时 item 投影却把结果 JSON 转成用户气泡，造成流式和历史展示不一致。

## 决策

### 实现方案

itemsToMessages 在展示投影中跳过 message 类型、role=user 且 message_type=resume 的 item。协议状态继续保留原始 item，协作工具结果继续关联原调用并在工具面板展示。普通用户提交的 JSON 保持可见。

修复沿用已有历史展示语义，范围限于前端消息投影，直接记录已实现决定。PostgreSQL 持久化、等待点恢复和模型输入保持原有语义。

## 替代方案

- 按正文是否包含 results 等 JSON 字段隐藏：会误隐藏用户主动提交的 JSON。
- 从协议状态删除 resume item：把展示判断混入事件恢复状态，超过气泡展示的职责。
- 隐藏协作工具结果：影响用户在工具面板核对任务结果。

## 后果

内部恢复输入不形成用户发言；实际用户输入、工具执行状态和协作结果继续可见。过滤使用已有消息类型，不增加配置、协议字段或依赖。

## 验证

- `docker compose exec -T frontend node --test test/unit/agentItems.test.js` 覆盖事件接收与快照合并两条路径，并核对协议状态保留、普通 JSON 输入可见、工具结果内容及成功状态。
- 未添加过滤时，该测试因额外出现 input-resume 消息失败；添加过滤后通过。
- 浏览器在运行中的 ThreadMessageList 组件中注入构造的公开协议 item：核对内部输入气泡隐藏、普通 JSON 输入与主 Agent 回答可见，并展开工具结果核对正文。浅色桌面与深色窄屏完成截图检查；未重新调用真实模型协作链路。
- 前端 lint、495 个 unit、typecheck/build、文档 build、工程契约检查及其 64 个 unit 通过。
