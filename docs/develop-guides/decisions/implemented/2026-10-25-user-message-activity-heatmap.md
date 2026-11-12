# 账户设置的个人消息活动热力图

状态：implemented
类型：feature
Owner：backend/yuxi/modules/agents/repositories/messages.py

展示口径的次要 Owner 是 frontend/src/modules/dashboard/ui/DashboardActivityHeatmap.vue 与账户设置挂载点。

## 问题

账户设置页缺少类似 GitHub Token 活动的个人使用概览。Token 用量依赖 Provider 回报，部分模型缺失 `usage_metadata`，直接统计会得到不可靠的总量；消息行（用户消息、模型输出与调用记录、工具执行记录）是本系统自己持久化的完整事实，按日计数即可稳定反映活动强度。

## 决策

- 活动口径：统计当前用户非删除会话内 `role ∈ (user, assistant, tool)` 的消息行，按上海日历日分组，返回固定 365 天（近一年）窗口并补零，与参考的 GitHub 年度视图一致；Dashboard 活跃度的 120 天口径不随之改变。`role='system'` 行与已删除会话不计入；model/tool 审计行属于模型与工具活动，计入。
- 数据边界：`GET /api/user/message-activity` 仅依赖登录态，服务端用当前认证用户的 uid 过滤，不接收 uid 参数；普通用户无需管理员权限。
- 聚合查询放在 agents 域的 `MessageRepository.count_daily_activity`；端点只是单次读查询，不设独立用例 service，窗口常量与响应组装直接在路由文件内，避免纯转发层。
- 展示复用 Dashboard 的 `DashboardActivityHeatmap`，新增 `valueKey`/`valueLabel`/`totalUnit`/`ariaLabel`/`emptyText` props，默认值保持 Dashboard 活跃用户口径不变。

### 实现方案

| 模块 | 职责 |
|---|---|
| `MessageRepository.count_daily_activity` | 按 dialect 兼容的上海日历日表达式聚合计数并补零（sqlite 单测 / PostgreSQL 生产） |
| `identity/users.py` 路由 | 登录态依赖、365 天窗口常量与响应组装，仅返回本人数据 |
| `DashboardActivityHeatmap` | 通用日热力图渲染，值字段与文案由 props 决定 |
| `AccountSettingsComponent` | 挂载时拉取活动数据，刷新按钮一并刷新 |

## 替代方案

- 统计 Token：数据源不可靠（部分消息缺 usage），放弃。
- 复用 DashboardRepository 的活跃用户查询：那是管理员视角的跨用户聚合，口径是"开启会话的活跃用户数"，与个人消息数语义不同，且会把用户态端点挂在 superadmin 读模型上。
- 为账户设置新写一个热力图组件：与现有组件仅差文案与值字段，重复约 280 行，选择参数化复用。

## 后果

- 消息总量会大于"排除审计行"的会话消息口径（每次模型调用与工具执行各计一行），这是有意包含工具活动；页面文案已说明包含三类消息。
- 窗口固定 365 天；更早的消息不出现在热力图，也不出现在总数。Dashboard 活跃度仍是 120 天，两个窗口各自独立。
- 热力图组件成为跨模块复用组件（settings 引用 dashboard 的 ui 组件，符合模块导入约定）。

## 验证

- `backend/test/unit/repositories/test_message_daily_activity.py`：sqlite 上验证角色过滤、会话归属、uid 隔离、删除会话排除与窗口补零；负向案例断言其他用户消息不计入。
- `backend/test/integration/api/test_user_message_activity_router.py`：真实 HTTP 验证 401 与新建用户 365 天窗口契约（其余用户消息不泄漏）。
- `frontend/test/unit/activityHeatmap.test.js`：消息口径与 Dashboard 默认口径的双向渲染断言。
- 对真实 PostgreSQL 数据以独立 SQL 复核 2026-10-24 计数（37）与仓储结果一致；页面实测账户设置渲染近一年热力图，年度总量与 `sessions.uid` 全量计数一致。
