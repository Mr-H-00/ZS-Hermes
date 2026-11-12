# Memory 默认启用与工作区查看入口

状态：implemented
类型：feature
Owner：backend/yuxi/modules/identity/preferences.py

## 问题

Memory 的用户配置默认关闭，账户设置缺少直接查看记忆文件的入口。

## 决策

### 实现方案

无已保存配置的用户默认启用 Memory，新配置记录使用相同默认值。已保存的关闭状态继续生效，不批量改写持久数据。账户设置在开关标签旁增加“查看 Memory”胶囊按钮，导航到 `/workspace?open=/agents/MEMORY.md` 并关闭设置弹窗，复用工作区预览与文件授权。

UserConfigSettingsCard 拥有查看按钮，AppLayout 提供设置关闭方法。WorkspaceView 打开文件后消费当前 open 参数并保留其他 query，使用户收起预览后可再次打开同一文件；异步完成时不消费已更新的其他打开请求。

## 替代方案

- 强制开启所有已有用户：覆盖用户明确选择，需要额外的数据变更。
- 只改变前端开关初值：后端实际运行配置仍关闭，展示与执行不一致。
- 在设置页增加独立文件预览：重复工作区能力，增加维护范围。

## 后果

未配置用户的后续 Agent 执行可读取已有 Memory 文件；显式关闭继续约束 Memory 能力。查看入口允许关闭状态下检查文件，文件缺失沿用工作区行为。

## 验证

- 后端相关 unit 验证无配置默认启用、新记录实际持久化默认值、显式关闭与用户隔离。旧实现因默认关闭失败，修改默认值后 14 个相关测试通过。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/api/test_user_config_api.py -q --disable-warnings`：真实 HTTP 验证默认启用、保存关闭后重新读取仍关闭及未认证请求拒绝，2 个测试通过。
- 前端 unit 验证查看按钮不改写偏好、导航后关闭设置，以及 open 参数消费、重复打开和保留更新后的其他请求。缺少消费逻辑时，测试因保留 open 参数失败。
- 真实浏览器验证账户按钮导航、设置关闭、Memory 正文可见、收起后重复打开、从 Agent 页返回缓存工作区，以及 375px 窄屏弹窗预览；浅色桌面与深色窄屏的按钮完成截图检查。
- 前端 lint、497 个 unit、typecheck/build、文档 build、工程契约检查及其 64 个 unit 通过。后端使用 `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q --disable-warnings`，2519 个通过、55 个跳过；使用已有依赖环境避免容器中 egg-info 构建权限问题。
