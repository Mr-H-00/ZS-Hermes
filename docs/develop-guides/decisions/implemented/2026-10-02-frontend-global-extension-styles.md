# 前端扩展样式单次全局装配

状态：implemented
类型：simplification
Owner：frontend/src/assets/css/main.css

## 问题

`extensions.less` 被多个 Vue scoped style 块重复导入。每次导入都会被 SFC 编译成带不同 scope 的规则，构建产物重复包含同一套扩展页面样式；其中只有少量 LESS 变量被局部组件使用。

## 决策

由 `assets/css/main.css` 单次全局导入 `extensions.less`，删除页面和组件中的重复 scoped import。局部组件仅保留需要的 CSS 值，不再依赖扩展样式文件提供 LESS 变量。`ConversationWorkspace` 删除重复导入全局 `main.css`。

### 实现方案

全局入口拥有扩展页面公共 class；页面和组件 style 只拥有本地布局与覆盖。`McpDetailView` 的三个字体声明直接使用原变量展开值，其余扩展样式由 `main.css` 装配。构建产物、lint 和扩展页面浏览器截图共同验证删除没有造成编译或可观察样式回归。

## 替代方案

- 保留每个 scoped import：无需改动，但继续产生重复 CSS，不选择。
- 为每个组件复制变量定义并保留 scoped import：只能修复编译依赖，不能消除规则重复，不选择。
- 把所有组件样式也合并到全局文件：会扩大全局选择器污染面，不选择。

## 验收结果

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| `extensions.less` 只在全局入口导入一次 | 重复 scoped CSS 回归 | `frontend/src/assets/css/main.css` | 源码搜索与 build 产物大小 | 恢复任一组件 import，搜索/产物检查应失败 | Passed |
| 前端可以完成 typecheck、lint 和 build | LESS 变量缺失或模板语法被样式改动破坏 | frontend build composition | `pnpm run lint:check`、`pnpm run build` | 删除全局 import 或局部变量展开，build 应失败 | Passed |
| 扩展页面公共样式和会话页面全局样式保持可见 | 全局化后页面外观或样式作用域改变 | 页面组件与 `main.css` | Playwright 桌面、窄屏、深色截图 | 恢复 scoped import 或移除全局入口，页面样式探针应变化 | Passed |

## 后果

扩展公共 CSS 只在 `main.css` 装配一次，构建 CSS 从约 773.6 kB 降至 592.4 kB，减少约 181.2 kB（23.4%）。`McpDetailView` 的字体声明不再依赖全局 LESS 变量；`ConversationWorkspace` 不再重复导入 `main.css`。

## 风险

扩展公共 class 从 scoped 规则变为全局规则，可能与未来同名 class 冲突；现有 class 命名属于扩展页面公共命名，继续由该模块维护。该变更不处理 Ant Design 覆盖、`:deep` 数量或全局 reset。

## 验证

- `cd frontend && pnpm run lint:check`：Passed。
- `cd frontend && pnpm run build`：Passed；构建产物 CSS 总量约 592.4 kB。
- Playwright 访问 `/extensions`：桌面、390×844 窄屏和深色模式截图已检查；扩展卡片、导航和错误态可见。预览环境 API 未提供，网络错误 toast 属于环境噪声，不据此宣称业务链路通过。

旧能力不存在：组件和页面不再重复导入 `extensions.less`，会话工作区不再导入 `main.css`。

重新引入条件：只有新增独立 CSS 作用域或构建测量证明全局规则产生冲突时，才允许恢复局部导入，并须补充对应页面视觉验证。
