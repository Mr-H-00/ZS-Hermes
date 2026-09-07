# 测试审计与发布门禁 oracle 收敛

状态：proposed  
类型：simplification  
Owner：backend/test/unit

## 问题

测试套件精简后，独立审查发现仍有几处证据边界不完整：Prompt 测试没有验证实际组装结果，默认分块上限测试的 oracle 不足以排除错误默认值，benchmark reorder 测试依赖调度时序，决策记录还引用了已删除的测试文件。

## 提案

- 在运行时组装后的 chatbot prompt 上保留 `html:preview` 不被重复注入的负向断言。
- 用事件同步 benchmark worker，使异常 worker 与 reorder buffer 的测试不依赖 `sleep` 或偶然调用顺序。
- 断言默认 512 token 配置产生的确定性分块结果，排除 768 等错误默认值。
- 修正决策记录中的文件引用，并把本次审查收敛过程纳入决策生命周期。

## 替代方案

- 只检查源字符串：拒绝，因为它不能证明实际运行时 prompt 没有重复注入。
- 保留固定延迟和调用计数：拒绝，因为事件循环调度变化会制造假失败或假绿。
- 只断言硬上限：拒绝，因为 1.5 倍硬上限允许错误的默认分块值通过。

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| chatbot 运行时 prompt 不重复注入 HTML 预览指令 | prompt 组装路径意外携带该指令 | `build_prompt_with_context` | 运行时 prompt 单测 | 在组装结果中注入 `html:preview` 后测试失败 | Not run |
| reorder buffer 异常测试与调度时序无关 | worker 顺序变化导致断言漂移 | `iter_generated_benchmark_items` | benchmark generation 单测 | 移除事件同步或恢复异常处理后测试失败 | Not run |
| 默认分块配置仍为 512 | 默认值错误放宽后仍返回相同模糊结果 | `general.chunk_markdown` | 精确 token 计数与 chunk 序列 | 将默认值改为 768 后测试失败 | Not run |
| 决策记录不引用已删除文件 | 文档导航指向不存在路径 | decisions 文档 | 相对路径与工程契约检查 | 恢复旧路径后检查失败 | Not run |

旧能力不存在：测试不得退回只检查源常量、固定睡眠调度或模糊长度上限的证据。

重新引入条件：只有新的测试仍能独立证明同一运行时事实、对调度有显式同步并能区分配置回归时，才可替换当前 oracle。

## 风险

精确 oracle 可能暴露 tokenizer 或分块实现的真实变更；若行为确实改变，应先更新语义 Owner 与决策记录，不以放宽断言隐藏回归。
