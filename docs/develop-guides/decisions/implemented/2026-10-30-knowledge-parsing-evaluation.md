# 知识解析输入与评估事实收敛

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/knowledge/evaluation/service.py

## 问题

评估读取会将过期作业快照回写为业务状态，与执行器的终态事务竞争；零命中题没有指标，造成综合得分偏高。CSV 已被统一解析成 Markdown，QA 仍按原始 CSV 重复解析。图查询把存储故障表示为空成功，且共享实体的两份显示名清理不一致。分块与解析内部还保留无消费者的旧参数和不可达分支。

## 决策

### 实现方案

评估终态由后台作业完成、失败事务拥有，读取只返回持久业务事实与短期进度，不执行修复写入。有标准片段的题目即使零命中也产生完整零值指标，聚合与筛选保持相同口径。QA 仅消费解析后的 Markdown 表格，由 Markdown token 提供单元格正文和数据行范围，不再次按竖线拆列。

已知评估任务类型的待执行作业若版本无法重建，失败事务也执行领域失败回调；数据集已关联新作业时，旧作业失败不能覆盖新状态。

CSV 首行为列名，字段按文本读取，保留前导零、`NA` 和布尔字面值；parser 对单元格中的竖线、反斜线、HTML 字面值和换行分别编码。XLS/XLSX 使用空结构表头保留全部原始行；XLSX 继续使用 Docling 转换与图片提取，不接受其自动将首行当作表头的内容丢弃。QA 不猜测表头用途，只剥离带冒号的明确问答标签，正常的英文句首保持原样。

图谱读取传播存储失败，在可见性裁决时统一清理对外显示字段。共享 documents 服务继续拥有运行配置与输入暂存，parser 继续只生成完整本地产物；删除无消费者的内部参数、旧输入分支和 no-op，不改变公开引擎协议、持久 ID 或来源位置。

## 替代方案

保留读取修复并加 CAS 会维持两个终态 Owner；在 QA 中猜测输入是否为原始 CSV 或 Excel 首行是否为标题会继续保留双重格式协议和内容丢弃风险。两者均不采用。完整合并 parser、documents 与 Knowledge 会破坏聊天附件复用边界，也不采用。

## 后果

移除读取时修复状态的第二 Owner，历史断链评估记录需要显式审计处置；已有错误评估得分不自动重算。CSV、XLS、XLSX 的 QA 输入统一为 parser 产出的 Markdown，不支持把原始 CSV 直接送入 Markdown 分块入口。Excel 原始首行即使写着“Question / Answer”也作为内容保留；新解析产物采用明确的结构表头，已有产物不自动改写，重新解析后生效。图存储故障由现有 HTTP 错误路径明确返回。

配置与输入暂存仍归 documents，纯本地产物归 parser，Knowledge 继续拥有分块与发布。内部装配直接使用 `ParseOptions`；移除标题提升 no-op、无消费的 graph chunk_metadata / query limit、OCR 内部 params 与不可达的远端/裸 base64 输入分支。公开 DocumentEngine 统一签名、历史配置快照和 NLP 明示的公开段落协议保留。

## 验证

独立工作树通过测试包装器连接隔离 PostgreSQL 与 Neo4j，未启动原目录服务或修改原目录数据。新增九个实际模块回归在修复前全部失败，修复后全部通过。

| 验收主张 | 负向案例 | 直接证据 | 结果 |
|---|---|---|---|
| GET 不覆盖执行 Owner | 读取旧作业期间完成、关联新作业 | `test/integration/services/test_knowledge_evaluation_reads.py` 的真实 HTTP 并发与 PG 重读 | 通过 |
| 零命中纳入得分与低召回筛选 | 首题/末题零命中、历史空指标、无标准片段 | `test/unit/knowledge/test_knowledge_audit_regressions.py` 与前述 HTTP/PG 筛选 | 通过 |
| CSV QA 仅保留数据行与来源行 | 逗号、引号、表头 | `test/unit/knowledge/test_knowledge_audit_regressions.py` 的真实 CSV parse→chunk | 通过 |
| 表格内容与来源不被排版吞掉 | 单行 XLSX、转义竖线、引用块、换行、HTML 字面值与普通英文句首 | `test/unit/knowledge/test_table_qa_semantics.py` 的真实 parser→chunk 和 Markdown 输入 | 通过 |
| 图存储故障可观察 | Neo4j 连接拒绝 | 前述 HTTP 集成的 subgraph/labels/stats 三接口 | 通过 |
| 共享实体名称投影一致 | 最近来源代次失效后全库查询 | `test/integration/graphs/test_file_subgraph.py` 的真实 PG/Neo4j/HTTP | 通过 |

执行 `python -m pytest backend/test/unit/knowledge/eval -q -p no:cacheprovider`：50 通过。图谱、OCR、来源定位及新增回归的相关 unit 集合：122 通过。其余 parser 与 chunking 集合：189 通过、1 跳过（外部 RapidOCR 模型 smoke）。`python -m pytest backend/test/integration/graphs/test_file_subgraph.py backend/test/integration/services/test_knowledge_evaluation_reads.py -q -p no:cacheprovider`：7 通过。修改范围 Ruff 检查通过。评估读取的六项真实 PG/HTTP 回归接入 `scripts/ci/run_service_integration.sh`。

表格专项执行 `python -m pytest backend/test/unit/knowledge/test_table_qa_semantics.py backend/test/unit/knowledge/test_document_parser.py backend/test/unit/knowledge/test_knowledge_audit_regressions.py backend/test/unit/knowledge/test_qa_parser.py backend/test/unit/plugins/test_chunk_source_lines.py backend/test/unit/knowledge/test_parser_artifacts.py backend/test/unit/knowledge/test_parser_text_encoding.py -q -p no:cacheprovider`：144 通过。初始八个表格案例有七个在修复前失败；另有两个英文句首案例在收窄标签匹配前失败，均断言实际内容与行号。CSV 编号、`NA`、布尔字面值加入真实 parser→chunk 回归；表格、评估回归与 QA 专项共 48 项通过，parser 与表格集合 28 项通过。大整数验证直接断言 Markdown 文本，不绑定 pandas 内部类型。

旧能力不存在：GET 回写评估终态、QA 原始 CSV 二次解析、不可达标题提升、内部无消费者参数及 OCR 绕过本地暂存的输入分支已删除。

重新引入条件：新增真实输入消费者且明确格式、权限、失败与持久化 Owner，并提供独立回归证据。

外部 OCR 云 API、RapidOCR 实际模型推理与完整 worker/LLM 评估链路未在本次局部验证中执行；单元结果不替代这些集成语义。
