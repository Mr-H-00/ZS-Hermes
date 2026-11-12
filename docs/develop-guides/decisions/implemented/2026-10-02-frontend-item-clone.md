# 前端协议 Item 克隆

状态：implemented
类型：simplification
Owner：frontend/src/modules/session/model/agentItems.js

## 问题

SSE item 快照和 done 事件在多个路径使用 JSON 序列化深拷贝。该路径位于流式恢复和内容块收敛的热区，且 JSON 克隆会丢失 `undefined` 等 JavaScript 值。

## 决策

优先使用原生 `structuredClone`，仅在运行时没有该能力时回退 JSON 克隆。现有 item 协议仍要求可结构化克隆的普通对象/数组；不引入手写递归克隆或第三方依赖。

## 替代方案

- 保留 JSON 克隆：兼容面熟悉，但继续丢失值且热路径开销未收敛。
- 手写递归克隆：代码和边界更多，当前没有非结构化 item consumer，不选择。
- 引入依赖：当前只需一个运行时能力检测，不增加依赖。

## 后果

现代浏览器保留结构化数据语义，旧运行时继续拥有原有 JSON 兼容回退；若 item 引入不可结构化克隆值，仍会显式失败，不静默猜测。

## 验证

旧能力不存在：`agentItems.js` 的常规克隆路径不再直接以 `JSON.parse(JSON.stringify(value))` 为首选实现。

重新引入条件：只有支持范围明确排除 `structuredClone` 的目标运行时，或基准证明原生实现造成不可接受回归时，才调整实现，并附兼容性/性能证据。

- `cd frontend && pnpm exec node --test test/unit/agentItems.test.js`：Passed。
- `cd frontend && pnpm run lint:check`：Passed。
- `python3 scripts/verify_engineering_contracts.py`：Passed。
