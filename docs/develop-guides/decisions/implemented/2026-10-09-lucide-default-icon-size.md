# Lucide 默认尺寸随文字字号缩放

状态：implemented
类型：bug-fix
Owner：frontend/src/app/App.vue

## 问题

从 Ant Design 图标迁移到 Lucide 后，未指定尺寸的状态、表单帮助与按钮图标采用 24px 默认值，在 12–16px 文字旁显得过大。

## 决策

### 实现方案

根组件通过 Lucide 的 `setLucideProps` 提供 `size: '1em'`，所有后代中未指定尺寸的图标继承文字字号。图标自身的 `size` 优先于全局默认值，已有 CSS 尺寸继续生效。全局 `.lucide` 样式禁止 flex 收缩，避免窄屏将图标横向压扁；根组件拥有默认尺寸，`frontend/src/assets/css/main.css` 拥有收缩规则。状态与操作逻辑不变。

## 替代方案

逐个组件补充尺寸容易遗漏后续迁移位置。全局 CSS 强制覆盖 SVG 尺寸会压过已有 `size`，影响空状态和展示图标。库原生的默认值注入可同时覆盖遗漏位置和保留显式尺寸。

## 后果

未指定尺寸且无 CSS 覆盖的图标统一随字号缩放；需要固定尺寸的展示图标由调用方显式设置。该修复使用现有库接口，无新增依赖或需要先裁决的业务边界变更。

## 验证

`frontend/test/unit/iconDefaults.test.js` 执行实际根组件 setup 并渲染 Lucide 图标，检查状态、帮助与按钮图标的默认尺寸，以及显式 `size` 与 CSS 尺寸的保留。修复前测试因 SVG 输出 24px 而失败。真实页面回读确认状态图标为 12×12px，帮助图标在浅色、深色和 1440、1024、768、375px 宽度下为 14×14px；缺少禁止收缩样式时，375px 下宽度为约 11.4px 和 12.2px，尺寸断言失败。

`docker compose exec frontend pnpm run lint:check`、`docker compose exec frontend pnpm run test:unit`（498 项）、`docker compose exec frontend pnpm run build`、`pnpm --dir docs run build`、`python3 -m unittest scripts.test_verify_engineering_contracts`（64 项）和 `git diff --check` 通过。独立 Reviewer 检查默认值注入、现有消费者、收缩规则和测试，未发现需修复的问题。

`python3 scripts/verify_engineering_contracts.py` 被已有外部知识接口移除提案中的非法证据结果词阻塞；`docker compose exec api uv run --group test pytest test/unit -m "not slow"` 因容器内 `yuxi.egg-info` 时间戳更新权限错误未启动测试。这两项不计为通过。
