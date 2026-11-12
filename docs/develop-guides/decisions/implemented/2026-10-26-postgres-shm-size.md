# PostgreSQL 容器显式配置 shm_size=1g

状态：implemented
类型：bug-fix
Owner：docker-compose.prod.yml

## 问题

postgres 服务未设置 `shm_size`，Docker 默认 `/dev/shm` 仅 64MiB。高峰期并行查询/hash 聚合申请 POSIX 共享内存段失败，应用日志出现 `asyncpg.exceptions.DiskFullError: could not resize shared memory segment "/PostgreSQL.xxxx" to 8388608 bytes: No space left on device`。该错误不是数据盘满，是容器 shm 配额不足；不解决则高负载下查询持续报错。

## 决策

### 实现方案

两份 Compose（`docker-compose.yml` 与 `docker-compose.prod.yml`）的 postgres 服务显式声明 `shm_size: 1g`，附注释说明默认值根因。1g 是配额上限而非预占。属于小而完整、在同一变更内生效且无待裁决替代项的操作配置修复，故不另开 proposal，直接记录 implemented。

## 替代方案

- `max_parallel_workers_per_gather=0` 禁用并行查询：拒绝。规避而非解决，牺牲大查询性能。
- `dynamic_shared_memory_type=sysv`：拒绝。把问题迁移到内核 SYSV 配额，与官方镜像惯例相悖，恶化可观察性。
- 挂载宿主机 `/dev/shm`：拒绝。扩大容器间共享面，无隔离收益。

## 后果

- `shm_size` 是 recreate 级变更：**既有部署更新该配置后必须重建 postgres 容器才生效**，期间 api/worker 数据库连接瞬断，须低峰执行。
- `shm_size` 只影响 postgres 服务；graph（neo4j）等其他容器的 shm 策略不在本变更范围。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| compose 渲染出 1GiB shm | 默认 64MiB 下并行查询申请共享内存段失败 | `docker-compose.yml` / `docker-compose.prod.yml` postgres 服务 | `docker compose -f docker-compose.yml config` 与 `-f docker-compose.prod.yml config` 均渲染 `shm_size: "1073741824"`；基线无该键 | 移除该行后渲染回落为无 `shm_size`（Docker 默认 64MiB） | Passed |
| 报障环境容器实际生效 | 配置不被容器采纳 | postgres 容器 | 报障环境按该配置重建后 `docker exec postgres df -h /dev/shm` 显示 1.0G | 报障日志原文即缺陷存在性证据 | Passed |

未验证范围：高峰期 `DiskFullError` 复发归零需部署后以应用日志复采。
