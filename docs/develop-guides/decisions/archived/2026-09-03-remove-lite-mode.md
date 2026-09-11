# 取消 LITE 运行模式（历史记录）

状态：archived
类型：simplification
Owner：docker-compose.yml

本记录曾主张删除 LITE，现已被[合并 v0.7.3 并保留定制能力](../implemented/2026-09-10-merge-v0-7-3.md)取代，不再是当前运行时依据。

## 问题

当时的产品方案试图只交付完整知识能力路径，因此认为同时维护完整模式和 `LITE_MODE` 会增加 Compose、Schema、API/worker、路由、Durable Task、能力发现和前端的维护面。

## 决策

当时决定移除 `LITE_MODE`、`make up-lite` 及其条件装配，让 shipping Compose、迁移器、API、worker、知识路由、Skill、工具和 Web discovery 只保留完整知识路径。

## 替代方案

- 保留双模式：维护成本较高，但能支持低资源部署。
- 只在 Compose 中少启动外部服务：无法定义清晰的缺失服务和 readiness 语义。
- 拆分镜像或依赖组：会增加构建与发布矩阵。
- 删除知识库、图谱和评估：会改变产品核心能力。

## 后果

按当时方案，轻量部署需要补齐 Milvus、etcd、Neo4j 和完整知识运行时；应用装配不再分叉。该后果不适用于当前版本，因为合并决定恢复并保留 LITE 契约。

## 验证

历史验证曾检查完整拓扑、知识 schema、readiness/discovery、后端 unit、真实 integration、Web unit/build 和工程信任 gate。该结果仅说明当时的删除方案，不能覆盖当前合并后的双模式；当前事实以新的合并记录及其 Owner-local 测试为准。
