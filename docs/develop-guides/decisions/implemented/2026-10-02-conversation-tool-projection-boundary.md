# 会话工具调用投影边界

状态：implemented
类型：architecture
Owner：frontend/src/modules/session/model/toolCallProjection.js

## 问题

`conversation/model/messageGrouping.js` 为了归一化工具调用，反向导入 `conversation/ui/tools/toolRegistry.js`。工具 registry 同时拥有图标、展示名称和纯数据投影，导致 model 层依赖 UI 层，后续拆分会把展示生命周期和消息事实投影绑定在一起。

## 决策

将工具调用的解析、校验、归一化、状态派生和子智能体关联移动到 conversation model 的纯模块。UI registry 继续作为现有展示消费者的兼容入口，转出纯投影函数并保留图标映射；model 层只直接依赖新的 model 模块。

### 实现方案

`model/toolCallProjection.js` 拥有无 Vue、无图标组件依赖的工具调用投影函数。`model/messageGrouping.js` 直接导入 `enrichTaskToolCalls`。`ui/tools/toolRegistry.js` 只装配 UI 图标并从 model 模块 re-export 纯函数，现有 renderer import 不变；后续 UI registry 可以独立按需拆分而不影响消息投影。

## 替代方案

- 让 `messageGrouping` 继续深导入 UI registry：改动最小，但保留层级倒挂，不选择。
- 将全部工具 renderer 和 registry 移到 shared：会把会话业务语义扩散到 shared，违反 shared 无业务依赖约束，不选择。
- 为 model 与 UI 各保留一套实现：短期容易，但会产生状态规则漂移，不选择。

## 验收结果

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| conversation model 不导入 conversation ui/tools | 投影层再次依赖展示层 | `model/toolCallProjection.js` | 前端边界单测与源码搜索 | 恢复 `messageGrouping → ui/tools/toolRegistry`，边界测试应失败 | Passed |
| 现有 UI renderer 继续从 registry 获得纯工具函数 | 迁移破坏现有 renderer 或测试 | `ui/tools/toolRegistry.js` | `toolRegistry.test.js` 与完整前端 unit | 删除 registry re-export，现有解析测试应失败 | Passed |
| 工具归一化结果只有一个实现 Owner | model/UI 规则漂移 | `model/toolCallProjection.js` | 工具投影与分组相关 unit | 在 UI registry 增加另一份归一化逻辑，源码边界 review 拒绝 | Passed |

## 后果

消息分组和工具 renderer 共享同一个纯投影 Owner；UI registry 仍保留现有导入入口，但不再拥有消息归一化规则。工具图标和展示注册仍属于 UI 层，后续可以独立做懒加载而不把 UI 依赖带进 model。

## 验证

- `cd frontend && pnpm exec node --test --test-concurrency=1 test/unit/toolRegistry.test.js test/unit/toolRendering.test.js test/unit/agentInputQueue.test.js test/unit/frontendAuthBoundary.test.js`：Passed，27/27。
- 完整 frontend lint/build、浏览器视觉验证：Not run。
