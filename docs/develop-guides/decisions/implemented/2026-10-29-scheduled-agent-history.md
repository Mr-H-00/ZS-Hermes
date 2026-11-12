# 定时任务执行历史分页

状态：implemented
类型：feature
Owner：backend/yuxi/modules/schedules/repository.py

## 问题

任务列表只携带最近三次执行，用户无法追查较早的间歇性失败。数据库已持久化 occurrence、错误和会话关联，无需新增执行模型。

## 决策

### 实现方案

产品 HTTP 入口按任务读取执行历史，service 复用现有执行投影，repository 在当前用户拥有的任务范围内按 scheduled_for、ID 倒序分页。使用现有 limit/offset 习惯并返回总数；未知任务与其他用户任务都返回 404，参数限量。记录保留提交失败、跳过、队列及当前顶层执行状态；会话不存在时不生成可点击入口。

任务详情独立加载历史，使用固定页大小、上一页/下一页和总数；切换任务、刷新、立即执行后重载，并拒绝迟到响应覆盖新选择。加载、空和错误状态可识别，错误可重试。现有任务列表的最近三次摘要保持兼容。不修改调度、投递、重试、删除或 Run 终态机制，也不新增 Public API 或 Agent 工具。

## 替代方案

直接移除三条限制会导致列表随历史无限增长；新表或新执行引擎重复现有 occurrence 事实。游标分页更适合高频流，但当前维护页采用有界 offset 查询更简单；新记录到来时用户刷新以获得最新页。

## 验证

- `test/integration/api/test_scheduled_agent_history.py` 在隔离 PostgreSQL 与真实 TCP HTTP 上保存 25 条 occurrence，回读所有页、同时间排序、失败内容、排队状态与会话关联，验证未知/其他用户/软删除任务、未登录及非法分页参数；仓储直接查询也拒绝其他用户。
- 在独立测试进程恢复缺少历史端点的旧行为，同一回归在预期历史请求处因 404 失败。
- `frontend/test/unit/observabilityViews.test.js` 执行实际 Vue 组件与 API 模块，验证分页错误重试、刷新、切换任务和卸载后的迟到响应；`frontend/test/browser/observabilityViews.js` 在实际页面用合成 API fixture 验证 10/10/5 条分页、无会话禁用、错误重试、空状态、深浅色与 768–1920px 布局。
- 新增 HTTP 回归由 `scripts/ci/run_service_integration.sh` 选入实际 workflow。完整验证命令、独立 Review 与远端结果由交付 PR 记录。

## 后果

offset 分页期间新记录可能使相邻页移动，刷新后从第一页重新查看；不承诺跨请求快照。软删除后定义不在当前任务页展示，历史持久保留机制不变。
