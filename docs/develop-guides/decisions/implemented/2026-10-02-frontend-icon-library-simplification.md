# 前端图标库收敛

状态：implemented
类型：simplification
Owner：frontend/src/app/layouts/AppLayout.vue

## 问题

前端同时依赖 `@lucide/vue` 和 `@ant-design/icons-vue`。后者仅由少量文件使用，图标能力与已有 Lucide 图标重叠，增加依赖、注册和视觉维护面。

## 决策

使用已有 Lucide 图标替换 8 个文件中的 Ant Design 图标，保留局部别名以减少模板行为变化；删除 `@ant-design/icons-vue` 依赖及锁文件入口。Ant Design Vue 组件库本身继续保留，不在本决策中改变组件库注册方式。

### 实现方案

图标组件在各自的 SFC 中从 `@lucide/vue` 引入，保留现有模板名称或 icon map 结构。品牌 GitHub 图标使用现有 `GitFork` 作为轻量导航指示，不新增品牌图标依赖。lint、build 和关键页面截图验证组件仍能装配。

## 替代方案

- 保留双图标库：不改变 UI，但继续维护冗余依赖，不选择。
- 新增专门品牌图标库：只为 GitHub 增加依赖，维护面更大，不选择。
- 一次性重做全部图标视觉：超出依赖收敛范围，会改变大量页面视觉，不选择。

## 验收结果

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 源码和依赖中不存在 `@ant-design/icons-vue` | 图标库依赖残留或重新引入 | frontend package and SFC imports | `rg`、`pnpm run build` | 恢复一个 import，源码 gate 应失败 | Passed |
| 图标组件在 Vue 模板和 `h()` 动态入口可装配 | 别名组件导入错误或 export 缺失 | 受影响 SFC | `pnpm run lint:check`、`pnpm run build` | 使用不存在的 Lucide export，build 应失败 | Passed |
| 关键导航、知识库和会话页面仍显示图标 | 视觉或尺寸回归 | 页面 SFC 与 Lucide provider | Playwright 桌面截图 | 恢复旧依赖或移除替代组件，截图探针应变化 | Passed |

## 后果

前端只保留 Lucide 图标依赖，删除 `@ant-design/icons-vue` 及其锁文件入口；受影响的导航、知识库、会话和工具状态图标继续由现有组件别名装配。

## 风险

Lucide 与 Ant Design 图标的线宽、填充和默认尺寸不同，状态图标可能有轻微视觉差异；本变更不改变图标语义、交互或 Ant Design 组件注册。GitHub 品牌图标改为通用 Git 分支符号，若产品要求严格品牌识别，应单独提供 SVG 资产决策。

## 验证

- `cd frontend && pnpm run lint:check`：Passed。
- `cd frontend && pnpm run build`：Passed。
- Playwright 访问 `/extensions`：桌面截图检查通过；导航 GitHub、扩展卡片和操作图标可见。预览环境 API 未提供，错误 toast 属于环境噪声。

旧能力不存在：源码和 package manifest 不再引用 `@ant-design/icons-vue`。

重新引入条件：只有产品需要严格品牌图标或 Lucide 缺少语义等价物时，才允许新增图标资产/依赖，并须单独记录体积与视觉验证。
