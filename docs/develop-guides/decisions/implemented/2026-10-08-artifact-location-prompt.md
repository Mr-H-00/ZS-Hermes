# Agent 按任务选择产物位置

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/agents/runtime/agent_backends/chatbot/prompt.py

## 问题

主 prompt、交付物工具说明和内置 Skill 对 outputs 的反复推荐或要求，会引导 Agent 忽略用户要求与项目结构。

## 决策

Agent 根据用户要求和项目结构，将产物保存在当前 Workdir 中合适的位置。未经用户明确要求，不得在当前 Workdir 外写入文件的约束继续有效。用户已明确位置选择规则，修改仅涉及提示文案，没有待裁决的运行时方案，因此直接记录为 implemented。

### 实现方案

主 prompt 拥有通用位置指导；present_artifacts 的描述与参数说明只表达展示已有结果文件；deep-research 和 image-gen 按任务选择保存位置，继续向文件工具和展示工具传入绝对虚拟路径。对应工具与 Skill 使用文档同步描述当前行为。文件授权、产物登记、下载和 OCR 工具固定输出路径，以及 large_tool_results、conversation_history 等内部目录协议保持原实现。

## 替代方案

保留 outputs 作为建议目录仍会强化单一路径偏好。修改底层目录协议超出请求范围，并会影响现有工具 consumer。采用删除通用目录偏好、保留工具协议的最小方案。

## 后果

产物可随项目结构组织，调用方不能依据提示词假定最终产物都在 outputs。已有展示工具接受用户可见范围内普通文件，其权限与文件检查不变。历史 changelog 与归档记录保留原有事实。

## 验证

旧能力不存在：全文检索并人工核对主 prompt、present_artifacts、deep-research 和 image-gen，四处均不再指定 outputs 为通用产物位置；固定工具路径和历史描述保留。

重新引入条件：只有用户需求或具体工具协议明确要求固定目录时，才在对应 Owner 定义该目录。

Inspected：修改仅涉及模型可见描述、Skill 示例和当前文档，未修改文件执行边界。相关 unit、工程契约检查和文档构建的实际结果随交付报告记录。真实模型位置选择未运行，文案检查不能保证每次模型都遵循指令。
