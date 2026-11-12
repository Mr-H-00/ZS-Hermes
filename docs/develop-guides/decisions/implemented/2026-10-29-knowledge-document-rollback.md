# 知识文档派生产物的撤销与重做

状态：implemented
类型：feature
Owner：backend/yuxi/modules/knowledge/services/document_resets.py

## 问题

知识文档从原件形成解析 Markdown 和图片，再形成分块、检索索引与图谱。用户需要撤销指定阶段及其下游产物，并从保留的可信输入重做；直接修改文件状态不能证明派生产物已经撤销。

## 决策

### 实现方案

业务 service 拥有动作准入与持久任务，repository 拥有文件、代次、抽取与图谱关联事实，worker 执行外部清理并在成功后发布结果。复用现有 PostgreSQL 投影锁、后台任务和清理重试边界，不引入通用回放引擎。

| 动作 | 产物与结果 |
|---|---|
| 清除图谱 | 删除文件贡献及失去引用的实体、三元组和图向量，清空抽取结果及构建标记；保留原件、解析正文、分块、普通索引和 indexed 状态 |
| 内部清除入库数据 | 先撤销图谱，再撤销分块与检索索引；保留解析产物并回到 parsed |
| 内部清除解析结果 | 撤销解析产物及其全部下游；保留原件并回到 uploaded |
| 重新解析 | 从原件生成新的可信解析产物；成功发布前后按明确边界撤销下游 |
| 重新入库 | 保留已有先构建新 generation、成功后切换的保障；内部清理原子供重新解析与正文保存复用，不提供独立清除菜单或 HTTP 入口 |
| 编辑 Markdown | 所有非运行的上传、解析、入库及错误态可进入编辑；有解析指针必须严格读正文，无指针明确从空稿人工解析。保存时撤销下游，发布新正文并回到 parsed，保留原件和既有图片 |

文件清除图谱使用 knowledge_graph_index 的 clear_file 动作。该知识库已有 pending/running 图谱任务时返回冲突，包含排队构建和修复，不默默取消或追加旧任务。worker 在排他投影锁内重新确认文件所属、稳定状态和提交代次；与构建、重入库、投影删除串行。正常提及的撤销在外部删除成功后提交，失败保留 PostgreSQL 图谱关联。历史分块及提及已丢失时，仅用目标文件 Neo4j 来源补足实体、三元组候选，先提交其中已无提及或关系端点的 PG 孤儿撤销；Neo4j 归属保留到向量删除成功，PG 行已不存在的来源候选仍执行幂等向量清理。成功后清空所有代次分块的抽取结果及构建标记。文件在排队期间被删除或替换代次时，任务显式失败。

领域清理使用 knowledge_document_reset 的 clear_index、clear_parse、reparse、save_markdown 动作；清理索引与解析结果的产品入口仅有重新解析和正文保存，两个独立清除动作没有文件菜单、前端 API 包装或 HTTP 入口。清理索引和解析资源的原子由重新解析及 Markdown 保存编排调用。提交事务复用目录/知识库短锁：撤销拒绝本库全部 pending/running 文档解析、入库、导入处理、图谱与撤销任务；普通任务只拒绝活动撤销，保留既有并行及去重。导入处理和文件、回执仍同事务登记。排队任务不被静默取消。

执行方先取得排他投影锁，再确认任务租约。文件开始清理时保留 processing_job_id 关联该撤销意图，普通 executor 和文件 claim 拒绝活动撤销及尚未恢复的失败撤销。未完成的动作由文件 projection_status 保存 clear_index、clear_parse、reparse 或 save_markdown，失败后继续保留；Job 历史被回收不会解除恢复限制，processing_job_id 只用于当前执行归属。开始与最终发布分别由受租约保护的领域事务拥有；开始清理即切换到新空代次、归零计数，撤销旧检索和图谱的可见性，clear_parse/reparse 同时摘除 Markdown 引用。相同撤销的失败重试复用已有空代次。外部清理不持文件行锁，不在另一连接更新该文件统计；最终发布复核文件未删除、归属、代次和处理 Owner，成功后才发布目标主状态。失败保留撤销关联，用现有 error_message 明确要求重试相同动作，不新增 resetting 状态。

clear_parse 严格删除该文件 parsed/images 前缀，不复用吞异常的解析 attempt 回收。取消或任一前缀失败都等待全部在途 I/O 完成后释放投影锁。原件不在此删除范围。reparse 先成功发布 uploaded，再由原件生成独立 attempt；解析失败沿用 error_parsing 及已有重试路径，清理失败则继续要求恢复原撤销动作。

Markdown 编辑使用管理权限的 GET/POST documents/{file_id}/markdown。GET 在共享投影锁和文件读取快照内返回正文、has_parsed_markdown 及不暴露对象路径的 revision；revision 是包含知识库、文件、generation 与可空 Markdown 指针的无歧义序列化摘要。有指针时严格读取 MinIO，错误不能冒充空稿，也不从 chunks 拼接正文；仅指针为空时返回明确空稿。合法空字符串是可保存的人工解析结果。

保存沿 knowledge_document_reset 的 save_markdown 动作编排：提交持目录/文件锁检查 revision 后登记任务，worker 取得排他投影锁、确认租约并再次检查来源修订；先成功写入独立 UUID 正文，再在拥有事务中切空代次并撤销下游读取，清理图谱、分块与普通检索，最后发布新 Markdown 指针及 parsed。待处理任务的 payload 保留正文供 worker 执行，正常的成功、失败或取消终态事务删除正文，只保留任务范围与诊断信息。原件和现有解析图片不在删除范围，不分析引用或回收未引用图片；现有解析图片在后续重新解析或删除文件时统一清理，重新解析始终保留原件。旧 Markdown 仅按对象定点回收，回收失败不撤销已发布结果、不把保存报告为失败。

未完成保存的动作事实仍归文件 projection_status=save_markdown；原 Markdown 指针保留到发布成功，失败回收本次未发布对象并保留客户端草稿。正文保存与普通解析回收本次产物前均复核 PostgreSQL 当前正文指针；发布已提交但调用返回失败不能误删已引用产物，确认查询失败则保守保留。仅当动作仍为未完成 save_markdown 且来源指针未变时，允许当前 generation 或 generation-1 对应的 revision 重试；重复失败不再推进空代次，恢复不依赖作业历史。清除解析、重新解析、重新入库或成功换指针后的旧 revision 均拒绝。该例外只允许尚未发布新正文的原稿恢复，不放宽其他动作的修订检查。

界面在知识库管理入口显式启用 FileDetailModal 的编辑能力，复用已有 AgentFilePreview 草稿编辑器，引用和会话打开方式默认只读。草稿可在编辑与预览间切换，退出前确认放弃未保存修改；已入库保存确认撤销下游。提交只表达入队，任务成功后才关闭编辑、刷新并显示已保存；冲突或保存结果不明时保留草稿并供用户核对当前正文，再决定是否覆盖。保存期间允许下载草稿并关闭，关闭不取消任务。无解析指针的空稿可显式保存，不以内容 truthiness 判断可编辑或可保存。

## 替代方案

直接改 status 无法撤销存储；先删后重新入库会破坏当前可用索引保障；通用阶段回放引擎增加尚无 consumer 的状态与配置。逐动作线性用例保留现有 Owner 和失败边界。

## 后果

PostgreSQL 与外部图、向量服务没有跨库事务；清理可能暂时撤销外部记录而正常提及事务回滚，任务失败必须可观察并能重试。历史无来源孤儿的 PG 预清提交失败、向量删除失败及 Neo4j 已删后的失败窗口均由真实存储回读测试覆盖。排他投影锁按知识库串行，清理会等待正在执行的入库/构建。共享实体属性由仍可见的提及对应抽取结果恢复；无可信匹配来源时使用规范名称、空属性且不发布来源。普通图向量内容以规范名称为准，不因来源属性恢复而重建。已有任务历史和无法识别处理版本的任务仍按原有历史回收规则处理，不在终态回调中迁移。重新解析的解析产物与图片边界已由含图片 DOCX 的真实链路验证；Markdown 保存通过同一含图片 DOCX 的真实 HTTP/worker 链路验证，原件和既有图片字节保持，下游撤销后可重新入库与构图。


## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 单文件清除图谱保留正文和普通检索 | 外部存储误删 | graph service、chunks、Milvus executor | 真实 HTTP/worker E2E 回读原件字节、Markdown、PostgreSQL、Neo4j、Milvus 及三模式检索，见 [E2E](https://github.com/xerrors/Yuxi/blob/main/backend/test/e2e/test_milvus_text_retrieval_e2e.py) | 其他文件共享实体与三元组仍可检索 | Passed |
| 半成品与重试可闭合 | Neo4j 已写、向量失败或删除中断 | graph repository 与 service | [集成测试](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/graphs/test_file_graph_clear.py) 和 E2E 回读引用、构建标记及外部记录 | graph_indexed=false 仍须清除；删除失败不能提交关联撤销；共享属性从仍有效的正文恢复 | Passed |
| 并发及排队旧写不能复活 | 构建、重入库、删除交错 | 持久后台任务与投影锁 | 真实 PostgreSQL 锁竞争、真实租约失效与 [HTTP 拒绝](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/api/test_graph_build_status.py) 后回读任务和文件 | pending build 冲突；代次改变失败；失租旧清理不能取得删除权 | Passed |
| 重新解析及其内部清理原子遵守阶段边界 | 原件或图片误删、半清理恢复被绕过 | document_resets service/repository | [真实 PG 恢复测试](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/services/test_knowledge_document_resets.py) 与含图片 DOCX 的 HTTP/worker E2E 回读对象字节和三类索引 | 旧排队意图阻止撤销；取消等待全部线程；Job 历史回收不解除恢复限制；删除 KB 后作业仍达失败终态 | Passed |
| Markdown 编辑严格读取并条件发布 | 旧修订覆盖、正文读取错误冒充空文档 | document_resets service/repository | 真实 PG CAS 与 HTTP/worker/MinIO 回读新正文、原件和图片 | 清除/重解析/重入库与成功保存后旧修订拒绝；有指针读取失败不能空稿；无指针空串可保存 | Passed |
| 保存失败可以恢复且没有伪成功 | 暂存失败撤销旧正文、Job 历史删除丢失恢复事实 | 文件动作与正文指针、编辑器草稿 | 故障注入、正式历史删除与浏览器任务终态验证 | 暂存失败不切空代次；清理失败保留原稿可重试；旧正文回收失败不推翻新正文；入队不关闭编辑 | Passed |
| 后台终态不保留编辑正文，损坏任务仍能结束 | 历史任务残留正文或失败回调重复报错 | document_resets 终态回调 | 真实 PostgreSQL 与 worker 任务终态回读，见上述集成测试 | 正文清除被移除、payload 为非对象或缺少 action 时的负控 | Passed |

验证使用真实 PostgreSQL、Redis、Neo4j、Milvus、MinIO 和 HTTP/worker。后台作业系统测试 job 的完整知识拓扑运行以下固定选择器，任一负控失败即拒绝合入：

```bash
docker compose exec -T api uv run --no-sync --no-dev pytest test/integration/graphs/test_file_graph_clear.py test/integration/services/test_knowledge_document_resets.py -q
```

该选择器 63 项通过，完整正文/图谱 E2E 3 项通过，相关真实 HTTP 15 项通过；独立关闭正文回收前的持久指针保护后，保存与解析的提交后取消/异常四项负控均失败。移除终态正文清除或损坏 payload 的 guard 后，对应的数据库回读负控失败。后端 unit 3357 项通过、125 项跳过，前端 unit 928 项通过；跳过项不算覆盖。工程契约校验及其 66 项 unittest、后端 Ruff、前端 lint/build 和文档构建通过。

E2E 模型端使用确定的 HTTP 协议 replay，验证真实 worker 调用及产物发布，不验证实时模型质量。一次与其他测试并发的运行中图向量检索返回空，独占重跑通过；首次失败原因未确认。浏览器使用真实组件与 API 夹具核验菜单、编辑、冲突及终态刷新，不冒充完整浏览器至真后端链路；真实后端行为由独立 HTTP/worker E2E 证明。
