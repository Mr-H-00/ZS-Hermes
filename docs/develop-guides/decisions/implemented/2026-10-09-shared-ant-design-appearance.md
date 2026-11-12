# Ant Design 基础外观与重复样式收敛

状态：implemented
类型：simplification
Owner：frontend/src/shared/model/theme.js

## 问题

通用控件、空状态、行内操作和弹窗皮肤分散在页面中。同一外观存在重复定义，主题切换与键盘焦点容易产生差异。追加全局覆盖而保留局部皮肤会形成两个维护入口。

## 决策

### 实现方案

CSS 色板拥有颜色、字体、圆角和浮层阴影。theme Store 在根节点完成主题切换后读取实际 CSS 色值，装配 Ant Design token 与组件 token。当前 Ant Design Vue 4.2.6 的全局设计 token 同时服务静态 message、notification 和确认弹窗；静态调用保留原接口。全局样式维护固定字重、说明文字、弹窗间距和底部按钮排列，不重复定义反馈皮肤。基础外观的使用方式由[设计规范](../../design.md#共享基础外观)维护。

按钮的文字与普通反色文字使用不同语义色。主按钮和危险按钮的 hover、active 使用现有色阶；带文字的 Switch 使用自己的组件 token，避免按钮文字色影响其他组件。

Alert 的四种状态通过组件 token 共用中性背景、柔和边框与圆角，以语义色图标区分状态。说明文字在共享样式中使用次级文字色；banner、隐藏图标、关闭按钮及内容和操作 slot 继续遵循原组件契约。

共享 AntEmpty 只替换默认图像，并转发原 Empty 的 props、attrs、slots、自定义图像和显式隐藏配置。应用入口集中注册现有 a-empty 标签，根 ConfigProvider 的 renderEmpty 使用同一组件，覆盖表格、列表和菜单的默认空状态；业务引导继续使用 ResourceEmptyState。管理页按钮、表单说明和刷新动画使用共享样式，原组件保留尺寸、业务布局、自绘标题、定高面板和透明标题选择器。

## 替代方案

- 保留局部样式：外观修改继续需要逐页同步。
- 改写所有控件与反馈 consumer：增加调用迁移和长期接口维护，超过当前视觉目标。
- 给静态反馈增加独立 CSS 皮肤：当前版本已有全局 token 支持，重复覆盖会分叉颜色和破坏 ghost 等显式配置。

## 后果

基础外观由主题入口修改即可传播到现有控件和静态反馈。局部样式只表达真实布局与业务差异。组件库升级时需要复核静态反馈的 token 继承及已打开实例的主题切换。Vue 全局标签注册不替换业务代码直接导入的原 Empty；显式导入的业务定制继续拥有自身语义。

## 验证

- `docker compose exec -T frontend pnpm run lint:check`、`pnpm run test:unit`、`pnpm run build` 通过，完整前端单测 508 项通过。Empty 的真实 SSR 测试覆盖默认图像、自定义图像、隐藏图像与说明、操作 slots、业务 emptyText 优先和全局标签注册。
- `playwright-cli -s=ui-consolidation --raw run-code --filename=frontend/test/browser/antDesignAppearance.js` 在初始浅色与初始深色页面通过，验证实际 DOM、三档尺寸、焦点、错误与禁用、Table/Select、已打开静态反馈的浅深色往返、ghost 透明、减少动态效果与 375px 布局。普通、危险和静态主按钮的 normal/hover/active 以及文字 Switch 的最低实际文字对比度均大于 4.5；对比度断言拒绝低于阈值的实际 DOM，ghost 断言检查透明背景。浏览器异常为零，并复读确认临时组件、反馈及主题状态已清理或恢复。
- Alert 浏览器验证覆盖四种状态的浅深色背景、边框、图标和文字，正文与说明的最低实际对比度为 4.83；banner 的无边框、无圆角，隐藏图标、自定义内容与操作 slot，以及关闭后 DOM 隐藏和单次关闭事件均通过。
- 浏览器检查公开登录页，并以合成 API 数据挂载真实用户与部门管理组件，检查行内按钮和添加弹窗；没有创建用户、部门或其他持久数据。未逐页验收全部业务页面，特殊弹窗的布局保留由 diff 与独立审查覆盖。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`、`cd docs && pnpm run build` 与 `git diff --check` 通过。
- 标准后端 unit 命令在 uv 同步阶段因容器 `yuxi.egg-info` 权限失败；使用现有依赖执行 `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow'` 得到 2539 passed、55 skipped。该证据不声称依赖同步成功。

旧能力不存在：被共享规则接管的管理页行内按钮皮肤、刷新动画、表单说明、普通弹窗标题及搜索输入基础皮肤不再逐页定义；全局 LESS 中无消费者或无效的 deep 覆盖已删除。重新引入条件：存在通用 token 或共享类无法表达的真实业务布局或状态，并以明确局部作用域实现。
