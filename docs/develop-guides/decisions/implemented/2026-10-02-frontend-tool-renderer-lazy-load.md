# 前端工具 Renderer 按需加载

状态：implemented
类型：simplification
Owner：frontend/src/modules/session/ui/tools/ToolCallRenderer.vue

## 问题

工具 renderer 在入口文件静态导入，聊天首屏会把低频知识库、文件、子任务和图片工具的 UI 一起放入主 chunk。未知工具仍应使用基础工具卡片，低频 renderer 的加载失败不能阻断消息列表。

## 决策

将已知工具 renderer 改为 Vue 异步组件，首屏只保留基础工具卡片与 registry。异步组件设置局部 loading/error component；加载失败显示“工具详情暂时不可用”的局部卡片，不改变工具调用事实或消息投影。renderer registry 仍是 UI 层 Owner，不下沉到 model。

## 替代方案

- 保持全部静态导入：行为风险最低，但继续扩大首屏主 chunk，不选择。
- 全面引入通用插件系统：当前没有第三方 renderer consumer，增加版本、权限和生命周期复杂度，不选择。
- 只做 Vite manualChunks：只能改变产物分组，不能让运行时按工具需求加载，不选择。

## 风险

动态 chunk 可能在网络异常或部署版本切换时加载失败；错误组件必须保持局部，不得吞掉原始 tool call 或影响消息列表。删除未使用的 index 专用导出只针对当前仓库内 consumer，新增 consumer 必须直接声明其 renderer 依赖。

## 验证

旧能力不存在：`ToolCallRenderer.vue` 不再静态导入低频 renderer，`tools/index.js` 不再重新导出这些 renderer。

重新引入条件：只有出现仓库内真实 consumer、需要同步调用 renderer 的公共组件或按需加载导致不可接受的首屏/交互回归时，才恢复对应静态导出，并补充独立决策记录和构建/浏览器证据。

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| renderer 不再静态进入聊天入口 | 首屏加载全部低频 UI | `ToolCallRenderer.vue` | build 产物与源码 import 检查 | 恢复静态 import 后主 chunk/检查应失败 | Passed |
| renderer 加载失败局部化 | 动态 chunk 失败导致页面错误 | `ToolRendererUnavailable.vue` | 组件 unit 或浏览器注入加载失败 | 去掉 errorComponent 后负向测试应失败 | Inspected |
| 未知工具仍显示基础卡片 | 新工具被异步 registry 吞掉 | `ToolCallRenderer.vue` | 现有 tool registry unit | 未知 tool 不应抛错或空白 | Passed |

## 后果

工具 renderer 从聊天入口拆成按需 chunk；基础工具卡片仍同步可用，动态 chunk 失败只显示局部错误卡片。内部 `tools/index.js` 不再承诺未被仓库 consumer 使用的 renderer 专用导出。

## 验证说明

旧能力不存在：`frontend/src/modules/session/ui/tools/index.js` 不再静态导出低频 renderer，聊天入口源码 import 与 build 产物中不包含这些组件。

重新引入条件：只有构建测量证明动态加载引入可感知的首屏延迟，或部署环境不支持动态 chunk 时，才恢复静态导入，并须附 build 产物对比证据。

- `cd frontend && pnpm run lint:check`：Passed。
- `cd frontend && pnpm run build`：Passed；删除 `tools/index.js` 中残余静态导出后不再出现 ineffective dynamic import 警告。
- `python3 scripts/verify_engineering_contracts.py`：Passed。
