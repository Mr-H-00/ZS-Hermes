# 用量统计统一读取 Run 快照并保留缺报

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/system/repositories/dashboard.py

## 问题

Token 趋势读取 Message 的旧 usage_metadata，会话审计读取 Run.token_usage，同一批执行可能得到不同结果。部分汇总将没有上报的用量转换成零，无法区分未知消耗与真实零消耗。

## 决策

### 实现方案

Dashboard repository 统一从 Run.token_usage 聚合已上报值，不回退 Message 元数据。会话审计按会话汇总各执行段；趋势按 Run 创建时间归入上海时区的十四个日历区间，包含恢复段与子会话，排除删除会话和已删除用户。空区间表示没有运行；存在未上报运行时保留未知或已知部分，并输出完整性。会话汇总与 Agent 分组保留相同的未知语义。

调用统计组件展示完整、部分缺报或未上报，只有确实上报的零才能显示为零消耗。统计说明解释时间归属与运行中的未结算快照，不把平台已记录用量作为供应商账单。采集、模型配置、价格和计费不在本决定范围内。

## 替代方案

继续读取 Message 无法覆盖 Run 的全部执行段；从 Message 回补历史会混入重复/不完整事实。补估算值会把未知伪装成观测值。引入费用结算或新用量表超出当前修复需要。

## 验证

- `test/integration/api/test_dashboard_usage.py` 在隔离 PostgreSQL 和真实 TCP HTTP 上通过三个范围的用例，固定旧 Message 用量与 Run 不同，断言恢复段累加、首桶边界、未来/删除排除、完整性、全缺报和真实零。旧查询在同一回归中返回错误总量，三个范围均失败。
- `test/unit/services/test_dashboard_service.py` 通过会话汇总与审计逻辑回归。`frontend/test/unit/observabilityViews.test.js` 使用真实 Vue setup、API 模块与 watcher，保留未知汇总与明确零。
- `frontend/test/browser/observabilityViews.js` 在真实页面使用合成 API fixture 验证部分缺报、全缺报、零及图表 null 间隙，并保存深浅色截图；它不替代上述真实数据库/HTTP 证据。
- 两项新增 HTTP 回归由 `scripts/ci/run_service_integration.sh` 选入实际 workflow。完整工程契约、unit、前端 lint/build、文档构建和独立 Review 由交付 PR 记录实际结果。

## 后果

历史 Run 缺少快照时无法恢复真实消耗；正在运行的快照可能尚未结算。返回 null 的汇总需要调用方保留未知语义。不同时间范围与单会话范围本来不同，必须在界面和机制说明中明确。
