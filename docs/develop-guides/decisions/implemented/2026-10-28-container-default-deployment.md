# 默认部署使用 Container 沙盒

状态：implemented
类型：feature
Owner：docker-compose.yml

## 问题

首次部署默认要求额外的 Cube/KVM 执行节点，而快速开始已经提供 Container 选项。Compose、环境模板、初始化和教程需要采用同一个默认选择。

## 决策

### 实现方案

开发与生产 Compose、环境模板和进程后端选择缺省值统一为 `container`。Bash 与 PowerShell 初始化在未配置后端时写入 Container 并生成 runner token，重跑保留有效密钥及显式 Cube 选择。runner 继续由 `container` profile 装配，教程和初始化提示显式提供该参数；API/worker 不持有 Docker socket。

生产 Container 继续要求预构建镜像及 digest。Cube 作为显式选项保留原来的节点校验；任一后端缺少必要配置均明确失败，不自动切换。

## 替代方案

仅修改教程会让复制模板或直接使用初始化脚本的用户得到不同结果。移除 runner 的 profile 则会让 Cube 部署无条件启动不需要的 Docker runner，因此保留显式 profile。

## 后果

Container 共享宿主内核且不保留跨 Run 的 Home；可信单租户用途和 Cube 强隔离选项由部署文档明确说明。已有环境的显式后端和数据保持原配置；缺少后端字段的旧 Cube 环境需在更新前显式设置 `SANDBOX_BACKEND=cube`。Makefile 默认启用 Container，可通过 `COMPOSE="docker compose"` 使用已配置的 Cube 环境。

## 验证

宿主 `uv run --project backend --no-sync --group test pytest backend/test/unit/config backend/test/unit/sandbox/test_container_provider.py -q` 通过 225 项，含真实初始化文件回读与 Docker Compose 渲染；默认后端、认证一致性、生产镜像要求、显式 Cube 及缺失凭据拒绝均有断言。`scripts.test_bump_version` 覆盖模板镜像提示同步、开发版文档保留与非法版本拒绝。

Windows 初始化由 `scripts/test_init_security.ps1` 执行真实 PowerShell 脚本并回读配置，镜像操作在该用例中替身化。当前 Linux 环境没有 PowerShell，原生执行交由 `Backend Unit Tests` 的 Windows job；发布标签必须等包含此检查的同一提交 CI 全部成功。完整容器执行、文件与清理链路由 `Runtime System Tests` 另行验证，配置 unit 不替代运行链路证据。
