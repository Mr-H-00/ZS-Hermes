
![Yuxi：可私有部署的多租户知识智能体平台](https://xerrors.oss-cn-shanghai.aliyuncs.com/posts/2026/08/20260818-151118-mac-1787037059154-8c08f48c.png)


[![](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=ffffff)](https://github.com/xerrors/Yuxi/blob/main/docker-compose.yml)
[![](https://img.shields.io/github/v/release/xerrors/Yuxi?color=046A82)](https://github.com/xerrors/Yuxi/releases/latest)
[![License](https://img.shields.io/github/license/bitcookies/winrar-keygen.svg?logo=github)](https://github.com/xerrors/Yuxi/blob/main/LICENSE)
[![DeepWiki](https://img.shields.io/badge/DeepWiki-blue.svg)](https://deepwiki.com/xerrors/Yuxi)
[![Bilibili](https://img.shields.io/badge/知识库演示-00A1D6?logo=bilibili&logoColor=fff)](https://www.bilibili.com/video/BV1erE26iEgv/?share_source=copy_web&vd_source=37b0bdbf95b72ea38b2dc959cfadc4d8)


<a href="https://trendshift.io/repositories/24335" target="_blank"><img src="https://trendshift.io/api/badge/repositories/24335" alt="xerrors%2FYuxi | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

[[项目主页]](https://xerrors.github.io/Yuxi/) · [[快速开始]](https://xerrors.github.io/Yuxi/intro/quick-start) · [[演示视频]](https://www.bilibili.com/video/BV1erE26iEgv/) · [[版本记录]](https://github.com/xerrors/Yuxi/releases) · [[English]](README.en.md)



> 📢 作者为江南大学软件工程博士研究生，研究方向为 AI Agent、知识图谱与大模型应用，预计 2027年12月毕业，现寻求**实习/全职**机会。联系邮箱：wenjie.zhang@stu.jiangnan.edu.cn

---

## Yuxi 是什么

Yuxi（语析）是一个**可私有部署的多租户知识智能体平台**。把 **RAG 检索、Milvus 知识库内知识图谱、LangGraph 多智能体编排、MCP/Skills、沙盒工具与权限管理** 放进同一个工作台。

管理员负责接入模型、建设知识库并配置用户与部门权限；用户在统一对话界面中调用知识、工具和子智能体，获得带来源引用、基于图谱上下文的推理以及可预览、可下载产物的回答。

## 为什么选择 Yuxi

- **知识与智能体真正协同**：知识库和知识图谱让 Agent 在运行时可检索、可引用的知识来源。
- **从回答到任务交付**：Skills、MCP、工具、子智能体和沙盒文件系统共同支持长任务执行与产物交付。
- **面向团队而非单用户 Demo**：提供多租户、用户/部门权限、统一模型配置和外部 API Key 集成。
- **部署路径清晰**：Docker Compose 开箱即用。

## 核心能力

- **智能体运行时**：LangGraph、DeepAgents、SubAgents、Skills、MCP、Tools、中间件与异步 Worker。
- **知识库与 RAG**：多格式解析、Embedding/Rerank、检索评估、来源引用和文件预览。
- **知识图谱**：从知识库内容抽取实体关系，在 Milvus 与 Neo4j 中构建、检索并展示子图。
- **沙盒与产物**：隔离文件系统，支持文本、图片、PDF、HTML 等产物落盘、预览和下载。
- **平台治理**：用户与部门权限、模型供应商配置、API Key 调用、运行状态与评估能力。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | Vue 3 · Vite · Pinia |
| 后端 | FastAPI · LangGraph · ARQ (异步 worker) |
| 存储 | PostgreSQL · Redis · MinIO · Milvus · Neo4j |
| 文档解析 | MinerU · PaddleX · RapidOCR |
| 部署 | Docker Compose |

## 快速开始

**前置要求**：已安装 [Docker](https://docs.docker.com/get-docker/) 与 Docker Compose，并准备至少一个兼容 OpenAI 接口的大模型 API。

**1. 克隆代码并初始化**

```bash
git clone --branch v0.7.1 --depth 1 https://github.com/xerrors/Yuxi.git
cd Yuxi

# Linux/macOS
./scripts/init.sh

# Windows PowerShell
.\scripts\init.ps1
```

**2. 使用 Docker 启动**

```bash
docker compose up --build
```

从旧文件布局升级到 v0.7.2 时不能直接 `up`；停机迁移命令、失败恢复与 Kubernetes 边界只在
[生产部署指南](docs/advanced/deployment.md#2-启动服务) 中维护。

**3. 访问平台**

等待启动完成后，浏览器打开 `http://localhost:5173`，使用初始化时生成的管理员账户登录即可。

> 💡 不需要知识库 / 知识图谱等重依赖时，可使用 `make up-lite` 以 LITE 轻量模式启动，加快冷启动速度。更多部署说明见 [项目文档](https://xerrors.github.io/Yuxi)。

详细配置、生产部署和故障排查请阅读[快速开始指南](https://xerrors.github.io/Yuxi/intro/quick-start)。最新开发动态见 [Changelog](https://xerrors.github.io/Yuxi/develop-guides/changelog)，规划中的能力见[开发路线图](https://xerrors.github.io/Yuxi/develop-guides/roadmap)。

## 致谢

本项目参考并引用了以下优秀开源项目，在此致以诚挚的感谢：

- [LightRAG](https://github.com/HKUDS/LightRAG) - 早期版本曾参考其图谱构建与检索思路
- [DeepAgents](https://github.com/langchain-ai/deepagents) - 直接引入作为深度智能体框架
- [DeerFlow](https://github.com/bytedance/deer-flow) - 参考了其 Sandbox 智能体架构的实现思路
- [RAGflow](https://github.com/infiniflow/ragflow) - 参考了其文档 Text Chunking 的分块策略
- [LangGraph](https://github.com/langchain-ai/langgraph) - 多智能体编排框架，本项目的核心架构基础
- [QwenPaw](https://github.com/agentscope-ai/QwenPaw) - 参考模型配置与个人文件区域设计

## 参与贡献

感谢所有贡献者的支持！

<a href="https://github.com/xerrors/Yuxi/contributors">
  <img src="https://contrib.rocks/image?repo=xerrors/Yuxi&max=100&columns=10" />
</a>


## Star History

[![Star History Chart](https://star-history.dera.page/svg?repos=xerrors/Yuxi)](https://star-history.dera.page/#xerrors/Yuxi)


## 开源协议

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

Docker Compose 引入的第三方组件（Neo4j 社区版 GPL-3.0、MinIO AGPL-3.0 等）保留各自原始许可证，部署与再分发边界见[生产部署指南](docs/advanced/deployment.md)。



[![给 Yuxi 一个 Star](https://xerrors.oss-cn-shanghai.aliyuncs.com/posts/2026/08/20260818-184409-image-da91658b.png)](https://github.com/xerrors/Yuxi)
