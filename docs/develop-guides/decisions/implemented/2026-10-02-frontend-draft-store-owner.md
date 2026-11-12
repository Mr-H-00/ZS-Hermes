# 前端会话草稿单一 Owner

状态：implemented
类型：simplification
Owner：frontend/src/modules/session/model/thread_draft.js

## 问题

`chatThreads` store 和 `ConversationWorkspace` 各自创建 `createThreadDraftStore()`，虽然使用同一组 localStorage key，但形成两个可写 Owner，归档/项目删除清理与输入框保存存在认知和时序分叉风险。

## 决策

由 `thread_draft.js` 创建并导出唯一 `threadDraftStore`；线程列表清理和工作区草稿 session 都引用该实例。`createThreadDraftStore` 继续保留用于注入存储的单元测试和明确隔离场景，不能在产品装配处重复创建同一默认存储实例。

## 替代方案

- 保留两个实例：当前能工作，但同一 key 的写入 Owner 不唯一，不选择。
- 把草稿搬入 Pinia：会扩大会话 UI 状态范围，且不改变 localStorage 的 scope/降级语义，不选择。
- 删除存储抽象直接操作 localStorage：会丢失测试注入和隐私模式降级边界，不选择。

## 后果

默认产品路径只有一个草稿存储实例；测试和特殊宿主仍可创建注入存储。localStorage key、草稿切换、清理和隐私模式静默降级行为不变。

## 验证

旧能力不存在：`chatThreads.js` 与 `SessionWorkspace.vue` 不再调用 `createThreadDraftStore()`，仅引用 `thread_draft.js` 的共享实例。

重新引入条件：只有出现不同用户/存储 scope 的明确宿主边界，且新增实例不读写当前默认 key 时，才允许通过工厂创建额外 Owner，并补充 scope 决策与测试。

- `cd frontend && pnpm exec node --test test/unit/thread_draft.test.js`：Passed。
- `cd frontend && pnpm run lint:check`：Passed。
- `python3 scripts/verify_engineering_contracts.py`：受其他工作区的 backend bootstrap 决策记录阻塞，非本变更错误。
