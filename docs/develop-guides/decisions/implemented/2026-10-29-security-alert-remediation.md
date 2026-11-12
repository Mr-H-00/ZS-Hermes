# 安全告警修复与持续扫描

状态：implemented
类型：bug-fix
Owner：backend/yuxi/api/routers/knowledge/management.py

## 问题

知识库失败响应和 OCR 健康诊断可能把底层异常、服务地址或凭据详情发送给用户。锁定的 LangGraph 与 SDK 版本命中 [GHSA-3fx2-cqw9-xr3c](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-3fx2-cqw9-xr3c)，CodeQL 主分支结果停留在历史目录布局，不能证明当前源码状态。

## 决策

### 实现方案

知识库 HTTP 适配层的查询、参数、问题生成、文档提交与部分文件管理分支对未知异常返回固定业务文本，保留原有状态码与响应结构。URL 抓取 helper 区分自己定义的校验拒绝与第三方故障，后者即使使用 ValueError 也只发布固定文本；批量删除保留每项身份与可公开的领域错误。文档提交的领域拒绝穿透到既有 HTTP 错误 handler。

Documents service 将健康检查投影为已知状态与固定说明，保持 `details` 为空。引擎直接返回的 message/details 与配置或调用异常都不能进入登录用户响应；未知健康状态显式归为 error。Models service 和 HTTP 适配层保留模型身份、类型与可用性，对提供者异常和探针返回文本使用固定说明。抛出的异常原因记录在服务端日志，不依赖正则猜测哪些内容敏感。

后端 manifest 将 LangGraph 最低版本提高到 1.2.14，锁文件定向更新到 LangGraph 1.2.14 与 SDK 0.4.6。SDK 的安全下限由修复版 LangGraph 的依赖约束拥有，不新增没有直接调用者的顶层依赖。

独立 CodeQL workflow 在 PR、主分支和开发分支更新、版本 tag、每周计划与手工触发时分析 Python、JavaScript/TypeScript 和 Actions。上传权限限定在扫描 job，保持历史语言 category，不排除安全规则。既有 trust workflow 执行的 release workflow 检查拥有触发、语言与上传接线的负控；service integration 显式执行真实 HTTP 信息边界回归，接线负控拒绝遗漏该测试。

历史路径代码不重新实现，当前 fd-relative no-follow 与 Argon2 边界继续由 Workspace、Identity 与负向测试拥有。历史告警的最终状态由当前提交的新扫描结果确认，不在修复提交中批量关闭。

## 替代方案

- 仅人工关闭历史告警：不能发现当前同类缺陷，且没有持续扫描证据。
- 对异常字符串做正则脱敏：不能穷举外部提供者文本与嵌套载荷；固定公开结果更容易验证。
- 全局中间件重写所有错误：会覆盖已有业务错误与诊断契约，影响范围超过目标。
- 每个文件调用点重复校验：不能替代真正打开文件时的 no-follow 边界。

## 后果

客户端保留业务错误与结构化诊断状态，未知内部错误的细节需要管理员查阅日志。OCR 的 configured 仅表示凭据已配置，不能作为真实解析成功的证据。CodeQL job 成功只证明分析与上传完成，安全告警是否解决仍需查看对应提交结果。

## 验证

| 验收主张 | 语义 Owner | 证据与结果 | 负向案例 |
|---|---|---|---|
| 用户响应隐藏内部异常，已知业务拒绝不变 | 知识库 HTTP 适配层 | 真实 TCP HTTP 回归 30 项通过，包含 OCR 与模型诊断 | 旧代码首轮 16 项中 15 项因人工敏感标记外发或领域错误被吞而失败；真实 URL helper 补充负控 3 failed、1 passed |
| OCR 与模型诊断不发布提供者载荷 | Documents/Models service 与 HTTP 出口 | 同一真实 HTTP 集合覆盖配置异常、抛出异常、直接返回详情、未知状态及成功/失败结果 | 人工敏感标记不得出现在 HTTP 正文，领域错误与模型身份独立断言 |
| 锁定依赖避开已知漏洞 | manifest、uv.lock、dependency-audit | uv lock --check 通过；uv audit --locked --no-dev 检查 195 包无已知漏洞 | 现有依赖审计漏洞夹具保持不变 |
| 当前文件与密码边界拒绝历史缺陷 | Workspace 与 Identity | 完整宿主 unit：3483 passed，1 skipped；涵盖 v3 流式工具调用与 reasoning 回归 | 祖先目录换成外部 symlink 后读取被拒绝；旧 SHA-256 密码被拒绝 |
| 持续 CodeQL 接线不缺失 | codeql.yml 与 release workflow tests | 工程契约与 102 项脚本测试通过；后端全源码 Ruff 通过 | 删除触发、语言、category 或上传步骤时接线检查拒绝 |

实际命令：

```bash
uv run --project backend --no-sync --group test pytest backend/test/integration/api/test_error_information_boundary.py -q
uv run --project backend --no-sync --group test pytest backend/test/unit -m "not slow" -q
python3 scripts/ci/verify_engineering_contracts.py
backend/.venv/bin/python -m unittest discover -s scripts/ci -p 'test_*.py'
uv lock --project backend --check
uv audit --project backend --locked --no-dev
```

隔离临时 Compose 的 API 容器以普通用户执行完整 unit：3473 passed，11 skipped。容器使用既有 API 基础镜像，只读挂载本工作树，先按锁文件同步依赖，再按 API Dockerfile 从已安装 RapidOCR wheel 复制预置模型；没有连接业务服务。容器缺少 Docker Compose CLI 的 11 项部署检查由宿主完整 unit 覆盖。这是容器测试环境证据，不替代新 API 镜像构建或真实服务 E2E。

容器命令在该隔离 Compose 中执行：

```bash
uv sync --locked --group test --no-dev --no-install-project
docker compose exec -T --user 1000:1000 -e UV_CACHE_DIR=/tmp/uv-cache-unit-user api uv run --no-sync --group test pytest test/unit -m "not slow" -q -rs -p no:cacheprovider
```

文档 `pnpm --dir docs run build` 通过；CodeQL YAML 解析确认三个语言 job、预期触发与上传权限。独立 Reviewer 审查完整 diff 并另行运行 30 项真实 HTTP 回归、26 项 workflow/CI 接线检查，未留下未解决问题。

宿主唯一跳过项是未设置 RAPIDOCR_MODEL_DIR 的镜像预置模型检查，不代表真实 OCR 解析通过。真实 PostgreSQL、worker、外部模型和解析提供者 E2E 未在本轮执行；错误边界测试使用真实 TCP HTTP 与确定性业务故障注入，不把它们当作跨进程链路证据。GitHub 新扫描及上传需推送后核验。
