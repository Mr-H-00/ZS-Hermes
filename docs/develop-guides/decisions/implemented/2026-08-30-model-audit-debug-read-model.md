# Model 审计调试读模型

状态：implemented
类型：feature
Owner：backend/package/yuxi/repositories/model_message_audit_repository.py

## 问题

阶段二已将每次 Model 调用的 operation、顺序、状态、起止时间、monotonic 耗时和 usage 持久化到 PostgreSQL，但普通 History API 有意隐藏中间 `model_audit` 行，且其响应未暴露这些字段。调试面板因此只能展示普通历史和临时流式投影，不能读取运行中的审计事实，也只能从 Run wall-clock 起止时间推导整轮耗时。

## 决策

- 保持普通 History、Memory 和消息计数契约不变，新增线程级 Model audit 只读接口；后端只允许超级管理员读取其自身线程，前端调试开关不作为授权边界。
- 接口返回显式 DTO，按 Run 创建顺序和 ProtocolEvent `sequence` 排序；不直接暴露可任意扩张的 ORM metadata。
- 调试面板打开时读取该接口；存在 active Run 时低频刷新，关闭面板后停止。
- PostgreSQL 审计与 SSE 投影按数据库 Message ID 或 `(run_id, operation_id)` 合并，禁止按内容或相邻位置猜测。
- 单次 Model 终态耗时只展示后端 `duration_ms`；前端只负责格式化。`duration_ms` 缺失时显示未知，不以 wall-clock 差值伪造 monotonic 耗时。
- Run 总耗时继续使用 Run 自身起止事实，不以最终 Model 的 `duration_ms` 替代。
- 本变更只展示 Model 审计；ToolMessage 生命周期继续属于任务 #58 阶段三。

## 替代方案

### 让普通 History API 默认返回全部审计行

拒绝。它会改变普通聊天、Memory、计数和现有消息渲染的边界，并把调试事实泄漏到普通读模型。

### 只把新字段加到现有可见 AIMessage

拒绝作为完整方案。运行中的审计和没有 ToolCall 的中间 Model 行仍不可见，不能形成可查询时间线。

### 继续由前端从起止时间计算所有耗时

拒绝。单次 Model 已有 monotonic `duration_ms` Owner；wall-clock 差值不能替代该事实，尤其不能为中断或恢复路径猜测耗时。

## 后果

- 增加一个小型只读 API 和面板刷新请求；刷新只在调试面板可见且 Run 活跃时发生。
- 前端可以显示真实 Model 状态、顺序、时间和 usage，但阶段三前 Tool 生命周期仍不完整。
- `running`、恢复补偿或异常关闭的审计可能没有 `duration_ms`，界面必须诚实展示为未知。

## 验证

- Repository/service unit 覆盖排序、显式字段和 metadata 收敛。
- 真实 HTTP + PostgreSQL integration 覆盖完整返回、跨用户 404 和普通 History 仍隐藏纯审计行。
- 前端 unit 覆盖精确合并、无猜测和 duration 字段映射。
- 前端 lint、159 个 unit、production build 通过；真实页面验证浅色、暗色、375px 无横向溢出、error 保留既有消息和 running 无虚构耗时。
- 相关 backend unit 7 passed；真实 PostgreSQL + HTTP integration 2 passed，覆盖未认证拒绝、普通用户 403、超级管理员跨用户 404、显式字段、顺序和普通 History 隔离。
