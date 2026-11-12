# 会话历史自动加载与读取窗口

状态：implemented
类型：feature
Owner：frontend/src/modules/session/model/sessionRuntime.js

## 问题

长会话的公开记录包含工具调用，100 条记录可能只覆盖少量用户轮次。用户向上阅读时需要频繁点击加载更早消息，追加内容还会改变阅读位置。

## 决策

### 实现方案

Session runtime 的普通历史读取与追加各消费最多三个 100 条的公开内容页，维持已有游标、去重与恢复语义。目标 Turn 恢复读取全部目标内容；刷新遇到断线缺口时继续读取到已加载区域，两者可以超过三个页面。读取窗口不影响模型上下文或后端公开分页契约。

SessionWorkspace 在可见页面向上滚动到距顶部 600px 内时发起追加；追加后以当前可见 display item 的稳定 key 恢复阅读位置，保留请求期间用户的滚动。continuation 合并重挂载时重新查找同 key 消息；找不到时使用总高度差补偿。原生滚动锚定仅在追加期间禁用。正在读取、初次加载、隐藏页面和没有更多历史时不触发追加；失败后停止自动重试，由原按钮提供重试与加载状态。

## 替代方案

- 仅扩大窗口：仍需用户主动点击。
- 修改公开 API 上限：扩大所有消费者的响应预算，当前需求由前端有界读取满足。
- 一次读取全部历史：长会话产生不可控的首屏等待与 DOM 开销。
- 以消息组顶部锚定：同一 Run 内追加旧内容时组顶部无法表示当前消息的位置。

## 后果

窗口扩大提高前端记录和 DOM 数量，保留有界批次并按用户阅读追加。每次普通读取最多产生三个 API 分页请求。读完历史后移除加载入口，失败后由用户决定重试。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 普通读取最多 300 条，保持排序、游标与完整恢复 | 跳页或窗口未扩大 | sessionRuntime.js | sessionRuntime 单测 | 短历史、并发追加、断线缺口 | Passed |
| 向上接近顶部自动加载且保持阅读位置 | 重复请求、组内跳动 | SessionWorkspace.vue | sessionHistoryLoading 单测与真实 Vite 页面 replay | 在途读取、失败、隐藏、向下滚动、切换 Thread、同 Run 与 continuation 重挂载 | Passed |

前端全量 unit、lint、typecheck/build、工程契约检查及其 64 个单测通过。浏览器命令为 `playwright-cli -s=history-loading run-code --filename=frontend/test/browser/sessionHistoryLoading.js`：使用 950 条确定性公开 API 记录核对 DOM、300 条窗口、在途去重、失败手动重试、末页停止及浅色桌面与暗色 375px 页面。普通追加锚点偏移 -0.46875px，同 Run 组内追加偏移 0.125px。该 replay 验证前端真实装配与浏览器布局，不替代后端 HTTP 持久化证据；后端 API 契约保持原样。

`docker compose exec api uv run --group test pytest test/unit -m "not slow"` 因容器用户无法更新时间戳至 root 拥有的 `yuxi.egg-info` 而未运行测试；使用现有环境的 `docker compose exec api uv run --no-sync --group test pytest test/unit -m "not slow"` 完成 2628 passed、55 skipped、7 subtests passed。55 个既有跳过项未计为通过。
