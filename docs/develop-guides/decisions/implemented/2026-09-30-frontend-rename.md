# 前端工程统一命名为 frontend

状态：implemented
类型：architecture
Owner：docker-compose.yml

## 问题

前端工程需要统一目录、Compose 服务、镜像、CI 与开发命令中的名称，避免改名后构建失败、检查失去覆盖或文档指向失效路径。运行槽位需要保留配置的访问端口。

## 决策

### 实现方案

前端源码由 `frontend/` 拥有，开发与生产 Compose 使用 `frontend` 服务、`docker/frontend.Dockerfile` 和 frontend 镜像；端口由 `YUXI_FRONTEND_PORT` 设置。CI、Dependabot、版本脚本、工程契约检查和文档同步使用新路径。本地端口配置按原值改名，重新构建前端并替换旧服务容器。

业务协议的 web channel、网络搜索与外部资源名称保持各自语义。数据库、用户数据和后端行为不改变。历史材料只机械修正被移动源码的引用，不重写历史结论。

## 替代方案

仅改目录会留下服务名、镜像与开发命令不一致；保留旧变量兼容会增加用户未要求的长期维护路径。采用统一名称，部署者同步修改自有配置与命令。

## 后果

自有部署命令与端口变量需要同步改名；旧服务名和变量名没有兼容别名。前端容器替换产生短暂中断，数据库与其他持久服务保持运行。开发默认端口为 5173，生产默认端口为 80，由 Compose 拥有。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 结果 |
|---|---|---|---|---|---|
| 前端构建与服务使用 frontend | 路径遗漏或使用旧服务 | Compose、Dockerfile、frontend/package.json | Compose 配置检查、Docker build、lint、unit、生产 build | 既有 Compose 测试与依赖策略测试拒绝旧路径 | Passed |
| 工程检查与文档仍覆盖前端 | 检查因目录改名静默失效 | scripts/verify_engineering_contracts.py、CI、文档主题 | verifier 及其 unit、脚本测试、docs build | 非 API Owner 中的 API 字面量测试仍被拒绝 | Passed |
| 新服务可提供页面与 API 代理 | 容器启动但页面失败 | frontend Vite、API readiness | 真实浏览器 DOM、静态资源与代理响应回读 | 请求失败或页面空白使浏览器断言失败 | Passed |

验证使用 `docker compose build frontend`、`docker compose up -d --no-deps frontend`、`docker compose exec -T frontend pnpm run lint:check`、`docker compose exec -T frontend pnpm run test:unit` 和 `docker compose exec -T frontend pnpm run build`；前端 385 项单测通过。`docker build -f docker/frontend.Dockerfile --target production -t yuxi-v1-frontend:rename-production-check .` 构建生产镜像，并在临时 Nginx 容器中回读首页、登录路由、JS/CMap 资源和同源 readiness。浏览器控制台无错误；临时验证容器在检查后停止。生产 Compose 使用占位凭据检查结构，未部署生产环境。

`python3 scripts/verify_engineering_contracts.py` 和 `python3 -m unittest scripts.test_verify_engineering_contracts scripts.test_bump_version scripts.test_dependency_update_policy` 通过，72 项测试覆盖工程边界负控、版本脚本和依赖路径。`cd docs && pnpm run build` 通过。发布事件与缺失 tag 负控通过；完整 `scripts.test_release_workflows` 的冷构建预算负控存在既有失败，未修改 HEAD 中的同一测试也失败。

规定的后端 `uv run --group test pytest test/unit -m "not slow"` 因容器内 root 所有的 editable metadata 无法同步依赖。使用镜像已有依赖运行 `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow"`，2388 passed、55 skipped。常驻 API 容器未挂载仓库根目录，Compose 静态测试最初跳过；使用同一 API 镜像的一次性容器，只读挂载仓库并设置 `YUXI_PROJECT_ROOT=/repo`，执行 `docker compose run --rm --no-deps --entrypoint uv -v "$PWD:/repo:ro" -e YUXI_PROJECT_ROOT=/repo api run --no-sync --group test pytest -p no:cacheprovider test/unit/config/test_docker_compose_worktree_slots.py -q`，4 passed。宿主后端环境缺少 pytest，未承担这些测试。
