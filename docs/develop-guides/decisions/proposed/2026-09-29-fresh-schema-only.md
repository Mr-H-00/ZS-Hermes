# 全新部署只初始化当前 Schema

状态：proposed
类型：simplification
Owner：backend/yuxi/migrations/main.py

## 问题

旧部署的文件搬迁、停机证明和数据库升级共用启动前置进程。全新版本没有需要迁移的旧安装，继续保留旧目录挂载、迁移脚本和 quiesce 接口会扩大部署与运行边界。

## 提案

### 实现方案

Compose 的 `schema-init` 在 PostgreSQL advisory lock 内检查版本。精确当前版本直接结束；空库建立当前业务、知识与 checkpoint Schema 并写入版本；旧版本或带 Yuxi 表的未版本化库在 DDL 前失败。API 和 worker 只读校验版本。旧文件目录迁移、共享 Skill 搬迁、Run 收敛、停机证明与 provisioner quiesce 入口退役。

## 替代方案

保留旧迁移入口并由配置开关禁用：仍需维护旧卷、失败恢复和安全边界。完全取消版本表：无法阻止旧库被当前运行进程误用。

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 旧库在建表前失败 | 误把旧库当空库 | `migrations/main.py` | 相关 unit 与真实 PostgreSQL 集成测试 | business=2 或未版本化 `conversations` 表 | Not run |
| 旧文件迁移入口不存在 | Compose 仍挂载旧卷或提供停机脚本 | Compose、provisioner、脚本 | 负向搜索、Compose 配置与相关 unit | 恢复旧卷或 quiesce 路由 | Passed |

旧能力不存在：`get_legacy_storage_dir`、`YUXI_LEGACY_STORAGE_DIR`、`migrations/legacy`、`scripts/migrate-storage.sh`、`/api/sandboxes/quiesce` 与旧版 Schema 升级入口均无运行时 consumer。

重新引入条件：出现明确的旧版数据导入需求，并有独立的输入格式、停写边界、完整数据验证和真实 PostgreSQL/文件系统证据；不接回正常启动链路。

## 风险

当前 Schema 初始化仍复用 `ensure_business_schema` 与 `ensure_knowledge_schema` 中的约束 SQL。必须以真实 PostgreSQL 验证新库完整建表；版本写入之前失败会留下需要显式处理的半成品数据库。
