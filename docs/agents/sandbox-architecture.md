# 沙盒配置与运维

本文说明如何为 Yuxi 配置 `sandbox-provisioner`、选择 Docker 或 Kubernetes 承载、注入受控运行环境并验证实例。身份派生、虚拟路径、挂载权限、网络隔离、回收与恢复语义见[沙盒机制详解](../mechanisms/sandbox.md)；本页不重复内部实现。

## 选择承载方式

应用层固定使用 `SANDBOX_PROVIDER=provisioner`。provisioner 进程读取 `PROVISIONER_BACKEND` 选择承载方式；Compose 用户应设置宿主环境的 `SANDBOX_PROVISIONER_BACKEND`，Compose 再把它映射为容器内变量。不要在 `.env` 中把两个名称当作同一入口混用。

| backend | 用途 | 是否提供真实隔离 |
| --- | --- | --- |
| `docker` | 默认开发、单机部署；按需创建本机容器 | 是 |
| `kubernetes` | 由目标集群创建 Pod 与 NodePort Service | 是，取决于集群安全配置 |
| `memory` | unit 或占位测试，只保存 ID 到 URL 映射 | 否 |

生产或开发运行不要使用 `memory`。切换 backend 只改变动态沙盒的承载位置，API 和 worker 仍通过同一个 provisioner 认证代理访问沙盒。

## 应用层配置

API 与 worker 使用下面的变量连接 provisioner；实际默认值和 Compose 注入以 `docker-compose.yml` 为准：

| 变量 | 约束 |
| --- | --- |
| `SANDBOX_PROVIDER` | 当前必须为 `provisioner` |
| `SANDBOX_PROVISIONER_URL` | API/worker 可达的 provisioner 地址 |
| `SANDBOX_PROVISIONER_TOKEN` | 管理与代理接口 Bearer token，至少 32 个随机字符 |
| `SANDBOX_VIRTUAL_PATH_PREFIX` | 用户数据虚拟根，通常为 `/home/gem/user-data` |
| `SANDBOX_EXEC_TIMEOUT_SECONDS` | 单次命令执行超时 |
| `SANDBOX_MAX_OUTPUT_BYTES` | 单次命令返回给调用方的最大字节数 |

`SANDBOX_PROVISIONER_TOKEN` 只能提供给 API、worker 和 provisioner。不要把它写进 `sandbox.env`、Agent 用户环境、Skill、日志或文档示例。

## Provisioner 通用配置

Compose 用宿主变量生成 provisioner 容器变量；直接部署 provisioner 时则设置右侧容器变量：

| Compose/.env 输入 | provisioner 容器变量 | 作用 |
| --- | --- | --- |
| `SANDBOX_PROVISIONER_BACKEND` | `PROVISIONER_BACKEND` | `docker`、`kubernetes` 或仅测试使用的 `memory` |
| `SANDBOX_PROVISIONER_URL` | `PROVISIONER_PUBLIC_URL` | 写入每个响应的认证代理 URL；必须从 API/worker 可达 |
| `SANDBOX_IMAGE` | `SANDBOX_IMAGE` | 动态沙盒使用的镜像 |
| `SANDBOX_CONTAINER_PORT` | `SANDBOX_CONTAINER_PORT` | 镜像内 agent-sandbox HTTP 端口 |
| `SANDBOX_HEALTH_TIMEOUT_SECONDS` | `SANDBOX_HEALTH_TIMEOUT_SECONDS` | 实例创建后的健康检查总等待时间 |
| `SANDBOX_IDLE_TIMEOUT_SECONDS` | `SANDBOX_IDLE_TIMEOUT_SECONDS` | 无活动实例的回收阈值 |
| `SANDBOX_IDLE_CHECK_INTERVAL_SECONDS` | `SANDBOX_IDLE_CHECK_INTERVAL_SECONDS` | idle reaper 扫描间隔 |
| `SANDBOX_EXEC_TIMEOUT_SECONDS` | `SANDBOX_EXEC_TIMEOUT_SECONDS` | provisioner 计算安全回收下限时使用的命令超时 |

API/worker 连接地址与 `PROVISIONER_PUBLIC_URL` 通常来自同一个 `SANDBOX_PROVISIONER_URL`，但混合部署时必须确认该地址既能由 API/worker 请求 create/touch，也能访问返回的 `/api/sandboxes/<id>/proxy`。idle timeout 若小于命令超时加 30 秒，运行时会提高到该下限。

## Docker 后端配置

Docker backend 要求 provisioner 能访问宿主机 Docker daemon，并能解析线程数据在宿主机上的真实路径：

| 变量 | 作用 |
| --- | --- |
| `DOCKER_NETWORK_PREFIX` | 每个沙盒独立 bridge 网络的名称前缀 |
| `DOCKER_SANDBOX_PREFIX` | 动态容器名称前缀 |
| `DOCKER_THREADS_HOST_PATH` | `saves/threads` 在宿主机上的绝对路径；未设置时尝试从 provisioner 挂载推导 |

Compose 部署需要把 Docker socket 和 `saves` 对应目录挂入 provisioner。每个沙盒只加入自身网络，provisioner 同时加入该网络并提供认证代理；不要把动态沙盒接入承载 PostgreSQL、Redis、MinIO 等服务的应用网络，也不要把沙盒端口发布到宿主机。

## Kubernetes 后端配置

Kubernetes backend 使用 kubeconfig 或 Pod 内服务账号创建沙盒 Pod 和 NodePort Service：

| Compose/.env 输入 | provisioner 容器变量 | 作用 |
| --- | --- | --- |
| `SANDBOX_K8S_NAMESPACE` | `K8S_NAMESPACE` | 沙盒 Pod 与 Service 所在 namespace |
| `KUBECONFIG_PATH` | `KUBECONFIG_PATH` | provisioner 容器内 kubeconfig 路径；集群内运行时可留空 |
| `SANDBOX_NODE_HOST` | `NODE_HOST` | provisioner 能访问 NodePort 的节点地址 |
| `THREAD_PVC` | `THREAD_PVC` | workspace、uploads、outputs 与 Skills 线程投影使用的共享 PVC |
| `SKILLS_PVC` | `SKILLS_PVC` | 当前实现读取但未进入 Pod 挂载，属于预留字段 |

当前返回给 API/worker 的仍是 provisioner 代理 URL；`NODE_HOST` 只需从 provisioner 可达。Pod 禁用 ServiceAccount token 自动挂载，除非未来由明确威胁模型和实现变更调整。PVC 必须支持 provisioner 选择的访问模式和 `subPath` 目录结构。

## Docker Compose 开发配置

默认开发拓扑由 Compose 启动 API、worker 和 provisioner，再由 provisioner 动态创建短生命周期沙盒；仓库没有“直接在 API 容器执行用户命令”的本地模式。通常以 `.env.template` 与 Compose 的默认字段为起点，仅生成独立的强随机 provisioner token：

```env
SANDBOX_PROVIDER=provisioner
SANDBOX_PROVISIONER_URL=http://sandbox-provisioner:8002
SANDBOX_PROVISIONER_TOKEN=<至少 32 个随机字符>
SANDBOX_PROVISIONER_BACKEND=docker
```

启动与初步检查：

```bash
docker compose up -d
curl --fail http://localhost:8002/health
```

健康响应应报告 `backend=docker`。动态沙盒只在首次文件或命令操作时创建；仅启动 Compose 后看不到沙盒容器是正常现象。

## Kubernetes 接入步骤

1. 在目标 namespace 创建或确认 `THREAD_PVC`，预先验证 provisioner 与沙盒 Pod 都能访问预期 `subPath`。
2. 为 provisioner 提供最小权限的 kubeconfig，或让它在集群内使用受限 ServiceAccount；权限仅覆盖目标 namespace 所需的 Pod 与 Service 操作。
3. Compose 混合部署设置 `SANDBOX_PROVISIONER_BACKEND=kubernetes`、`SANDBOX_K8S_NAMESPACE`、PVC、`SANDBOX_NODE_HOST` 与 API/worker 可达的 `SANDBOX_PROVISIONER_URL`，并把 kubeconfig 只读挂入 provisioner。直接部署 provisioner 时使用对应的容器变量；集群内部署通常不设置 `KUBECONFIG_PATH`。
4. 从 provisioner 所在网络验证 Kubernetes API 和 `http://<NODE_HOST>:<nodePort>` 可达。API/worker 无需直接访问 NodePort。
5. 创建测试线程触发真实 shell 与文件读写，再核对 Pod、Service、PVC 文件和 provisioner 代理响应。

当前没有多集群选择 UI、Ingress backend 或自动节点发现。需要这些能力时应作为明确的部署功能实现，不能只通过文档假设存在。

## 沙盒运行环境

动态沙盒的环境由两类来源合并：provisioner 读取的全局 `docker/sandbox_provisioner/sandbox.env`，以及当前用户为 Agent 配置的环境变量；用户级值覆盖同名全局值。它们都会对沙盒内代码可见，应按可被不可信代码读取和外传来处理。

只注入任务真正需要的低权限变量。禁止注入 provisioner token、数据库凭据、对象存储管理凭据、云平台管理员密钥和其他租户秘密。代理变量可以配置，但应限制目标网络并避免让沙盒进入应用内部网络。

远程 Skill 拉取使用不继承全局和用户环境的一次性 sandbox；不要依赖 `sandbox.env` 为 Skill 安装提供凭据。Kubernetes 沙盒同样禁用 ServiceAccount token 自动挂载。

## 验证与排障

按下面顺序验证，避免把应用、provisioner、实例和文件路径问题混在一起：

1. 调用 provisioner `/health`，确认 backend、idle timeout 和依赖初始化状态。
2. 触发一个真实线程的 shell 命令与 `outputs` 写入，确认创建或复用的是该线程对应实例。
3. Docker 检查独立网络、挂载和 provisioner 代理；Kubernetes 检查 Pod、NodePort Service、PVC `subPath` 与 `NODE_HOST` 可达性。
4. 分别从沙盒 API 和 viewer 读取同一个虚拟文件，确认虚拟路径解析到同一所属线程；HTTP 状态仅作为接口可达性证据。
5. 等待超过 idle timeout，确认实例被回收、持久文件仍存在，并能在下一次操作重建实例。

常见错误应优先检查：应用层 URL/token 与 provisioner backend 是否混配、Docker host path 是否推导错误、Kubernetes PVC 子目录是否缺失、file thread 与 skills thread 是否取错、以及 provisioner touch 失败后复用的实例是否已经失效。进一步定位使用[沙盒机制详解](../mechanisms/sandbox.md)中的 Owner 和失败边界。

## 配置来源

变量名与注入位置以 `docker-compose.yml`、`docker-compose.prod.yml`、`.env.template` 和 `docker/sandbox_provisioner/app.py` 为准。本页只解释运维语义，不复制镜像标签或全部默认值；修改配置时同步检查这些 Owner 与部署模板。
