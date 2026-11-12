# 草稿清理有界批次

附件表与行状态清理替代本文的对象扫描/Redis 游标方案，当前 Owner 和证据见[事实归一决定](../implemented/2026-10-09-agents-owner-simplification.md)。本文保留当时取舍与验证记录。

状态：archived
类型：simplification
Owner：backend/yuxi/modules/agents/services/attachments.py

## 问题

已提交附件的来源备份持续保留。每次上传全量枚举当前用户和 APP 的对象，再锁定所有历史附件，会使上传成本和锁数量随历史增长。

## 决策

### 实现方案

MinIO 元数据枚举接受上限和续读位置。上传最多清理 32 个对象的批次，文件锁仅覆盖该批 manifest。短期 Redis 游标记录当前作用域的枚举位置，读到尾部重新从头扫描；游标丢失仅重复扫描，删除授权始终由 PostgreSQL 的提交归属及 advisory lock 决定。存储或缓存失败记录清理警告，已完成上传仍可用。

## 替代方案

固定首批会被永久保留的来源占满，后批过期文件无法清理。提交后迁移备份前缀会扩大复制、来源定位和崩溃恢复路径。独立 GC 任务及配置增加维护表面，当前消费者是上传触发的草稿清理。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 枚举、内存及文件锁保持有界 | 历史增长影响上传 | MinIO client、attachments service | 单元与真实 PG/MinIO/HTTP 清理测试 | 首批都是已提交来源，后批过期 draft 仍被删除 | Passed |
| 清理不删除已接收来源 | 恢复备份丢失 | AttachmentRepository | PG 提交归属与存储回读 | 清理游标丢失后重复扫描保留来源 | Passed |

旧能力不存在：上传全量对象枚举与全量历史文件加锁被有界批次替代。
重新引入条件：没有恢复全量扫描的场景；独立 GC 只在实际清理吞吐或定时需求出现后讨论。

## 后果

低频上传的作用域通过后续上传逐批清理，过期引用立即拒绝使用。Redis 只保存清理位置，缓存丢失不改变文件提交事实。

实际执行 `docker compose exec -T api uv run --no-sync --group test pytest test/unit/services/test_attachment_service.py test/unit/services/test_session_thread_status.py test/unit/storage/test_session_repository.py test/unit/storage/test_minio_public_images.py test/integration/api/test_public_agent_files.py -q`：51 passed。独立 Reviewer 核对有界迭代、首批保留/后批清理的反例与缓存位置可丢失语义，未要求增加业务状态或独立 GC 任务。
