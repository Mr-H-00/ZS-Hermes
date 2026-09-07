# 测试审计与发布门禁 oracle 收敛

状态：implemented
类型：simplification
Owner：backend/test/unit/agents/test_chatbot_prompt.py

## 问题

测试套件精简后的独立审查发现几处证据边界仍不完整：Prompt 测试没有验证实际组装结果，默认分块上限测试不足以排除错误默认值，benchmark reorder 测试依赖调度时序，决策记录还引用了已删除的测试文件。

## 决策

在运行时组装后的 chatbot prompt 上保留 `html:preview` 不被重复注入的负向断言。benchmark worker 用事件同步异常与后续调用，确保 reorder buffer 的测试不依赖固定睡眠。默认 512 token 配置使用精确 token 计数作为 oracle，并修正决策记录中的合并文件路径。

## 替代方案

- 只检查源字符串：拒绝，因为它不能证明实际运行时 prompt 没有重复注入。
- 保留固定延迟和调用计数：拒绝，因为事件循环调度变化会制造假失败或假绿。
- 只断言硬上限：拒绝，因为 1.5 倍硬上限允许错误的默认分块值通过。

## 后果

测试继续覆盖真实组装结果、worker 异常收敛和默认配置语义；精确分块 oracle 依赖当前 tokenizer 的确定性结果，tokenizer 或分块策略变更时必须同步审阅语义 Owner 和决策记录。

## 验证

- Prompt、分块和 benchmark generation 相关单元测试：36 passed。
- `ruff check` 与改动文件 `ruff format --check`：通过。
- 将已删除的 `test_semantic_chunking_empty_heading.py` 引用改为合并后的 `test_semantic_chunking.py`。

旧能力不存在：测试不得退回只检查源常量、固定睡眠调度或模糊长度上限的证据。

重新引入条件：只有新的测试仍能独立证明同一运行时事实、对调度有显式同步并能区分配置回归时，才可替换当前 oracle。
