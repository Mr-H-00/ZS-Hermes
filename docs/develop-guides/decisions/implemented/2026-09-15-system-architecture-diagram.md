# 系统架构图的文档 Owner 与分层

状态：implemented
类型：architecture
Owner：ARCHITECTURE.md

## 问题

Yuxi 的当前架构事实分布在 `ARCHITECTURE.md`、Docker Compose、Agent 运行时、知识库和沙盒机制页中。读者需要同时理解 Web/API、AgentRun、Durable Task、PostgreSQL/Redis、对象与检索存储以及沙盒边界，现有入口缺少一张按层次组织的总览图。

## 决策

`docs/mechanisms/system-architecture.md` 使用由 VitePress 静态托管的 SVG 架构图解释 Compose 部署拓扑、AgentRun 请求与执行时序、知识库导入与检索链路、Workdir 与 Sandbox 关系、LITE 模式边界和组件事实 Owner。图使用 VitePress 原生 Markdown 图片语法引用 `docs/public/architecture/` 中的资源，由构建器按站点 base 生成正确路径。

`ARCHITECTURE.md`、`docker-compose.yml`、源码和已有机制页继续拥有可执行事实；架构图只做解释性投影，不维护第二套完整配置清单。页面通过机制详解索引和 VitePress 侧栏进入文档导航。状态、权限、lease、失败恢复和外部存储回读语义继续由对应机制页承载。

## 替代方案

- 只在 `README.md` 增加一张图：面向首次认识项目的页面不适合承载运行时 Owner、lease、LITE 和失败边界。
- 只修改 `ARCHITECTURE.md`：能保持单一入口，但不利于机制专题导航，也会把图和代码地图混在一起。
- 使用外部绘图文件：增加构建和更新链路，且不能保证随 VitePress 一起渲染。

## 后果

- 服务、路由、存储或运行时边界变化时，SVG 架构页需要同步更新；页面保留事实 Owner 和源码定位，避免成为第二套运行时清单。
- 文档构建证明 SVG、Markdown、导航和链接可处理，不能证明 AgentRun、worker、SSE、文件、对象、Milvus、Neo4j 或 Sandbox 的真实链路正确。
- 构建页从 VitePress public 目录读取 SVG，部署 base 改变时仍由主题组件统一处理资源路径。

## 验证

| 验收主张 | 语义 Owner | 直接证据 | 结果 |
| --- | --- | --- | --- |
| 图作为 VitePress 静态 SVG 交付 | `docs/public/architecture/`、VitePress public asset 规则 | 2026-09-15 执行 `$node $pnpm --dir docs run build`；回读 `docs/.vitepress/dist/mechanisms/system-architecture.html` 与四张复制的 SVG | 通过 |
| SVG 是可解析的 XML 文件 | `docs/public/architecture/` | .NET `XmlDocument` 逐个加载四张 SVG | 通过 |
| 页面在 `/Yuxi/` base 下引用静态资源 | `docs/mechanisms/system-architecture.md`、`docs/.vitepress/config.mts` | Markdown 图片使用 `/architecture/*.svg`，由 VitePress 构建器生成部署路径 | 通过 |
| 图区分普通 Request/FIFO 与 resume、文件索引与独立图谱 Task、个人 Skill 与共享投影、以及知识工具边界 | `ARCHITECTURE.md`、对应机制页和源码入口 | 对照当前系统 Owner 与独立文档 Review | 已检查 |
| 页面明确 PostgreSQL、Redis、MinIO、Milvus、Neo4j 和 UserWorkspace 的职责，以及 LITE 与可选解析服务边界 | `ARCHITECTURE.md`、`docker-compose.yml`、`yuxi.config.runtime` | 对照当前系统 Owner 与独立文档 Review | 已检查 |

本记录先以 `proposed` 创建，待文档构建和产物回读通过后迁移为 `implemented`；这次迁移不改变架构图的事实 Owner。本次未运行浏览器视觉验收；构建产物、导航和静态资源已回读，真实运行链路仍按相关 integration 或 E2E 证明。
