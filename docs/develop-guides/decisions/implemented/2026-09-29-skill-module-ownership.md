# Skill 模块按运行时、用例与仓储分层

状态：implemented
类型：architecture
Owner：backend/yuxi/modules/extensions/skills/shared.py

## 问题

Skill 安装和管理用例曾散落在 `agents/skills` 与顶层 `services`；数据库查询也位于 Agent 目录。目录职责与架构约定不一致，调用方需要跨多个位置寻找同一业务链路。

## 决策

### 实现方案

共享、个人、草稿、远程获取、编辑和投影用例与运行时解析统一位于 `yuxi.modules.extensions.skills`；同目录 `repository.py` 拥有数据库查询，`models.py` 拥有 Skill ORM，`builtin/` 提供随代码发布的资源。`package.py` 集中包解析、快照复制和 slug 改写，`resolved.py` 描述来源。API、worker 与 Agent 直接导入实际 Owner，旧模块路径不提供兼容转发。

`edit.py` 拥有读取及已公开文件/依赖 HTTP 契约的适配，`content.py` 拥有受管理 Skill 的资源锁、完整内容修订、不可变目录与数据库引用提交；文件发布和失败边界由[完整内容提交决定](./2026-10-05-skill-content-commit.md)解释，不再原地替换或恢复旧文件；`projection.py` 拥有授权快照、共享行锁到用户投影锁的顺序和投影刷新；`runtime.py` 在预加载读取完成前保持共享行锁。个人来源由 `personal.py` 持有，文件访问复用 `modules/workspace/paths.py` 与 filesystem 原语。Agent 的 Input/Turn/Run、事务提交与投递顺序继续由 Agent 模块拥有；API 的文件响应继续消费 `PreparedFile` 或预览结果。

简单共享查询由调用方直接使用 `SkillRepository`，service 不提供逐方法转发。内置来源筛选由 repository 的 SQL 查询执行，保留原有排序。service 保留组合查询、安装、授权与一致性用例。

## 替代方案

- 继续保留散落文件，仅重命名模块：改动小，但架构 Owner 仍不清楚。
- 把所有文件放进 `agents/skills`：Agent 运行时入口集中，却继续让安装、权限和 PostgreSQL 查询受 Agent 目录拥有。
- 为包格式和类型单设顶层目录：增加一个查找位置，且包快照复制本就拥有文件副作用；当前没有需要独立维护的领域包边界。

## 后果

调用方按运行时、用例、格式和数据库边界定位 Skill 代码。包格式与文件复制归属 Skill service 内部模块，`workspace` 不依赖这些模块；`modules/extensions/skills/shared.py` 从同域 `builtin/` 发现随代码发布的 Skill。旧 Python 模块路径的仓库外消费者需要同步迁移。

## 验证

合并后的相关后端单测通过 184 项，覆盖编辑修订值冲突、文件/索引回滚、授权、草稿和运行时解析；前端相关单测通过 13 项。工程信任检查及 64 项测试、Ruff 与单测收集通过。测试环境显式加载分域 ORM，独立运行 Skills 测试无需依赖其他测试的导入顺序。

上游真实 PostgreSQL/HTTP 与 worker 验证见[共享 Skill 编辑验证](./2026-09-28-shared-skill-edit.md#验证)。这些结果只对应上游版本；合并后的 integration/E2E 依用户要求只做收集，待后续调整完成再执行。新增共享编辑 E2E 使用当前 Thread/Input/Turn 接口并回读投影文件与 Run 清单。
