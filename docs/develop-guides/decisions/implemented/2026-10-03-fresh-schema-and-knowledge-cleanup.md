# Fresh PostgreSQL 基线与知识清理边界

状态：implemented
类型：architecture
Owner：backend/yuxi/migrations/main.py

## 问题

新部署需要数据库独立拒绝错配的执行消息、跨知识库关系和错误来源关系。初始化中断、索引重建失败与外部存储清理需要明确的事实归属。部署接受重建数据库；JobTracker 保留现有进度与取消消费方式，恢复、重新加载和清理由对应业务领域拥有。

## 决策

### 实现方案

ORM 与 fresh baseline DDL 拥有当前结构，初始化器在空库创建基线，API/worker 校验版本。文件 repository 在事务内登记代次切换、删除事实和清理意图。知识 worker 的固定 cron 直接消费 pending outbox；文件索引和图谱写入持有共享投影锁，清理持有排他锁。外部 I/O 完成后释放锁，清理成功后提交关联移除。普通检索与图谱响应使用 PostgreSQL 可见事实过滤，外部存储保持派生投影职责。

### 初始化与关系约束

当前版本只支持全新建库，直接创建当前结构，不考虑历史数据兼容或数据库迁移。业务域版本为 `2`、知识域版本为 `1`，只标记当前初始化契约，不提供历史升级链；业务版本切换由 [Session 命名决定](2026-10-03-conversation-to-session.md) 拥有；执行输入关系和索引代次的简化见[后续关系收敛决定](2026-10-04-execution-and-mention-scope.md)。初始化器确认空库后提交 initializing 标记；进程中断后的重试只清理带该标记的半成品，完成后移除标记。版本门禁拒绝旧库和未完成初始化；部署重建需要对应数据授权。

Run 的整数 `session_record_id` / 字符串 `thread_id` 绑定同一 Session；带执行归属的 Message 同时绑定 Run/Turn 和 Session，输入/输出指针指向所属 Run 的消息。开放 Attempt 的部分唯一约束和关键 Run 状态检查保留在 schema。

KnowledgeFile/Folder、Chunk、图谱 mention、Dataset/Item、EvaluationRun/RunItem 使用复合键约束冗余归属。文件的 active_generation 拥有唯一服务代次；冗余版本表的移除由后续关系收敛决定拥有。删除 Dataset 保留逐题评估快照；文件夹删除保持子文件关系的既定行为。

### 版本、查询与局部清理

构建使用单调 generation；新版本完成前保留旧 active。服务代次、统计与 indexed 状态同事务切换。文件自身的状态及当前处理身份拒绝删除/替换后的旧完成回调。

Milvus 召回前按 PG 的 active 文件/generation 过滤，召回后回查 Chunk 并从 PG 补齐正文。读请求只打开现有集合；有可见 Chunk 而投影缺失时显式失败。图谱构建候选、计数、写入回查和公开子图使用一致的 active 范围；Neo4j 尚未清理时，PG tombstone 仍使对应内容不可见。共享实体的属性带最后写入 Chunk 的来源；来源失效时只返回仍有可见 mention 的稳定身份信息。

文件/KB 删除先提交 tombstone 与清理意图，HTTP 路由不提前删除 MinIO 原件。Outbox 仅有 pending/applied、对象范围、代次及错误记录；消费持有行锁并使用 SKIP LOCKED。错误保留 pending，后续消费重复同一幂等清理。JobTracker 不承载 outbox 清理任务、索引补发或上下文恢复。

索引、图谱发布与清理通过 KB 投影锁协调；取消时等待已启动 I/O 结束，多分支删除等待全部 I/O 结束再抛错。代次清理按原始 Chunk ID 清理 Neo4j 与图向量；PG 关联在外部成功后提交移除，失败回滚以保留重试依据。代次清理移除旧 Chunk，只保留当前 active/building 代次的内容。

### 凭据与索引

Provider 普通管理响应遮蔽 API Key、Authorization、Cookie 与嵌套凭据。编辑回传脱敏占位符时保留原值。权限依赖保持现有超级管理员要求。

索引根据实际访问路径校准，保留 Message 历史与 lease/task 的既有路径，调整 Input 队头、Receipt/成员查询和 Turn Run 排序。物理审计对比键、谓词、表达式、排序及访问方法，移除精确重复声明。

## 替代方案

- **升级旧库、回填和历史补列**：重建是部署前提，兼容分支没有验收价值。
- **JobTracker 恢复/清理平台或 outbox lease/token/retry 状态机**：局部行锁、幂等清理和错误记录满足当前消费入口，避免增加 JobTracker 重构依赖。
- **先删 active 再重建**：构建失败会破坏服务中的内容。
- **全库 ENUM/时区转换、全面关系化 JSON 或身份重构**：维持现有身份与快照 Owner，范围聚焦可验证的关系和副作用边界。

## 后果

- fresh baseline 不兼容旧数据库；初始化器没有历史升级承诺。
- 删除 API 成功表达 PG 不可见事实已提交，外部清理由 outbox 结果及存储回读证明。
- 外部服务持续故障保留 pending/error；系统没有跨存储 exactly-once、全库孤儿扫描或自动恢复索引上下文的承诺。
- 旧代次内容受清理边界限制，原始字节由 MinIO 拥有，PG 不保存历史内容快照。

## 验证

验证由 ORM、repository、实际 HTTP/worker 装配与 `.github/workflows/system-tests.yml` 中的拒绝 gate 闭合：

- **Passed**：25 项隔离 PostgreSQL integration，覆盖进程终止后初始化重试、关系约束、两万行 EXPLAIN 校准/移除索引反证、代次切换、删除可见性、SKIP LOCKED、取消晚写与 HTTP owning transaction 失败保留原件。
- **Passed**：真实 API/worker/PG/MinIO/Milvus 3.0.2/Neo4j E2E。building 候选排名更高仍不能占用 active top-k；图谱清理错误保留 PG 关联；重建/删除后回读图存储与向量；持锁阻塞清理期间，真实 HTTP 子图隐藏已删 Chunk/来源属性，同时保留其他可见文件的共享实体。
- **Passed**：Run lease/Turn 因果与 Provider HTTP/PG 回归 10 项；身份约束 1 项；Input/manifest/委托取消/知识统计 24 项。
- **Passed**：host unit 2578 项；容器 unit 的 slow 跳过依照现有标记单独记录。Ruff、工程信任检查与文档构建按提交前命令验证。
- **Passed**：等待问题回答及大工具审批的两项真实 worker E2E 通过；resume Message 首次 flush 前同时绑定 Turn/Run，PG 回读确认输入指针归属原 Thread/Turn 和新 Run，避免违反成对执行范围约束。
- **Passed**：同步后的权限边界与 Input Schema 集合分别 11/5 项通过真实 HTTP/PG 验证；清理断言先检查 tombstone 再等待物理删除，错 Run/Turn 的 Message 与跨 Run 输出指针断言由 PG 拒绝，合法相邻 Turn 的 usage 不混入父子统计。
- **Not run**：功能提交前未执行远程 CI；后续检查结果以 GitHub 对应提交为准。外部服务永久故障、跨系统 exactly-once 和生产旧库升级不属于验收范围。
