# CI 运行目录的非 root 所有权

状态：implemented
类型：bug-fix
Owner：.github/workflows/system-tests.yml

## 问题

Runtime System Tests 的两个 job 在干净 runner 上启动 API 与 worker 时，Docker 自动创建缺失的 bind mount 根目录并将其归 root 所有。Compose 中 API 与 worker 使用 `1000:1000`，内置 Skill 同步无法创建 `skill-sources/shared`，必需组件启动失败，readiness 与后续运行链路测试无法开始。

## 决策

两个 job 在 API 镜像构建完成后、首次启动拓扑前，用一次性 root API 容器将 `user-data`、`skill-sources`、`skill-projections` 的挂载根目录所有权设置为 `1000:1000`。准备命令以非零状态报告错误并阻止后续启动。API 与 worker 的 shipping 用户保持由 Compose 拥有的非 root 身份。

### 实现方案

workflow 使用现有 API 镜像与 Compose 挂载映射，执行 `docker compose run --rm --no-deps --user 0:0 api install -d -o 1000 -g 1000 -m 0700 /app/user-data /app/skill-sources /app/skill-projections`。不启动依赖服务，不递归更改已有子目录，不修改业务状态或应用启动代码。随后的真实 readiness 与运行链路测试验证 shipping 进程能初始化目录。`scripts/test_release_workflows.py` 在现有 trust job 中校验两个 job 均在构建与首次启动之间接入准备命令，并通过删除目录参数和单个 job 的所有权修复构造负向案例。

## 替代方案

- 将 API 与 worker 改为 root：扩大运行进程权限，偏离 Compose 的既有身份，不采用。
- 对目录开放全员写权限：绕过所有权约束，不采用。
- 在应用启动时递归修复宿主目录：应用没有该权限，且会把部署准备混入业务启动，不采用。
- 通过 runner 上的 sudo 调整固定宿主路径：需要重复解释 Compose 的实际挂载位置；一次性容器直接使用当前挂载契约。

## 后果

root 权限只用于 CI 冷启动前的一次性目录准备；长期进程使用非 root 身份。合入 develop/1.0 后复用上游的 install 准备命令，同时收敛三个挂载根的模式为 0700；重复准备只更新挂载根目录的所有权与模式，保留已有文件与子目录。开发与生产部署不新增自动所有权修复；用户数据的部署迁移仍由原有 Owner 负责。

## 验证

- `python3 -m unittest scripts.test_release_workflows`：7 tests，Passed。准备步骤未接入时新增正向测试因缺少所有权命令失败；遗漏三个目录中任意一个或任一 job 时负向案例检测到缺陷。
- 真实 Docker bind mount 探针：使用临时 Compose 拓扑、当前 API 镜像与 shipping 用户；Docker 创建的三个根目录均为 root，`1000:1000` 在每个目录创建子目录/文件均因 PermissionError 失败；执行从 workflow 读取的准备命令后，根目录 UID/GID 为 `1000:1000`，同身份创建文件并从独立容器回读内容及 UID 成功；重复准备后原文件仍可回读、目录仍可写。Passed。探针关闭网络以避免本机已耗尽的 Docker 地址池，未使用长期数据目录。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts`：Passed，后者 64 tests。
- `docker compose run --rm --no-deps -e PYTEST_ADDOPTS='-p no:cacheprovider' api uv run --no-sync pytest test/unit -m 'not slow' -q`：Passed，2465 passed、55 skipped。
- `uv tool run --offline ruff check --config backend/pyproject.toml scripts/test_release_workflows.py` 与 `git diff --check`：Passed。Ruff 整文件格式检查仍报告修改前既有行的布局差异，未重排无关代码。
- `cd docs && pnpm run build`：Passed，包含相对链接检查。
- GitHub 上完整冷启动、readiness、后台作业 与 Agent 主链路的结果以更新后运行记录为准；本地目录探针不替代这些检查。

合入 `829f65fd` 时采用上游相同语义的 `install -d` 命令，移除重复准备步骤；原 `chown` 探针是之前提交的证据，最终命令的冷启动行为以新 head CI 为准。工作流负控同步核对当前命令。
