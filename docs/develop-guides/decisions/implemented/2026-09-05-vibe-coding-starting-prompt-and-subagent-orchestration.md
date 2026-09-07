# Vibe Coding 起始 Prompt 与子 Agent 编排

状态：implemented
类型：process
Owner：doc/prompt.md

## 问题

Yuxi 的实现输入分散在 `doc/proposal.md`、`doc/detailed-design.md` 和 `doc/tasks/` 中，缺少一份可以直接喂给无人值守主 Agent 的统一起始 Prompt。若不固定主/子 Agent 的编排方式、模块顺序、测试门槛和进度跟踪源，后续实现很容易重新漂回手工协调、临时口头约定和不一致的验收标准。

## 决策

新增 `doc/prompt.md` 作为本次 Parent-Child 相关工作的 canonical 起始 Prompt。该 Prompt 明确：

1. 主 Agent 负责整体压缩、模块拆分、进度追踪和最终独立 Review。
2. 每个 `doc/tasks/*` 子任务对应一个子 Agent，按固定顺序逐个推进。
3. `doc/tasks/progress.md` 作为模块进度源，完成后逐项勾选。
4. 每个模块必须配套 `pytest` 测试；触及 API、存储、worker、SSE 或前端时按仓库测试规范升级验证。
5. 若出现会影响验收、数据、权限或外部状态的不明确点，必须先问用户，不得自行脑补。

## 替代方案

- 继续依赖自然语言临时指挥，不新增统一 Prompt。
- 为每个任务单独写一份起始 Prompt。
- 让主 Agent 手工串联所有模块，不拆分子 Agent。

## 后果

- 后续实现入口更单一，模块顺序、测试门槛和仓库约束不会散落在多份临时说明里。
- 子 Agent 分工更清晰，容易对照 `doc/tasks/` 逐项收敛。
- 代价是 `doc/prompt.md` 必须跟随 proposal / design / task 文件变化同步维护。

## 验证

- `doc/prompt.md` 已显式引用需求、设计、任务与工程约束文档。
- Prompt 中包含主/子 Agent 编排、任务顺序、测试门槛和歧义提问规则。
- `doc/tasks/progress.md` 仍作为模块完成状态的唯一进度源。
