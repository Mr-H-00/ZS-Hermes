# beta3 候选的依赖安全补丁

状态：implemented
类型：bug-fix
Owner：backend/uv.lock

## 问题

候选 `eddfdc4d` 的依赖审计在 Python、前端和文档三个 job 失败。Python 锁文件的 aiohttp、certifi、langgraph-checkpoint-postgres、opentelemetry-api、Pillow 和 pypdf 命中已公开告警；Node 命中 markdown-it 与 Vite。候选标签需要对应提交通过完整 CI，不能用历史审计或其他检查成功替代。

## 决策

### 实现方案

只更新命中告警的包及解析所需的关联依赖，保留应用 beta3 版本与既有审计、许可证、unit、integration 和系统门禁。Python 锁定 aiohttp 3.14.4、certifi 2026.10.19、langgraph-checkpoint-postgres 3.1.3、opentelemetry-api 1.45.1、Pillow 13.0.0 和 pypdf 6.21.2；manifest 保留相应安全下限。解析同步更新 Docling Core/Doclang、LangGraph checkpoint、OpenTelemetry 关联包与 yarl，Docling Slim 保持 2.132.0。

已下载包的 metadata 确认 Docling Core 2.101.1 要求 `Pillow<13`，2.104.0 放宽至 `<14` 且要求 `doclang>=0.8,<0.9`，因此 Pillow 安全版本带动这两项兼容更新。checkpoint-postgres 的实际运行版本为 3.1.2 → 3.1.3，manifest 的下限由 2 提升至 3.1.3 不表示本次锁文件跨主版本。

前端 manifest 与锁文件将 markdown-it 更新至 15.0.3、Vite 更新至 8.2.4。文档沿用 `pnpm-workspace.yaml` 的 `vitepress>vite` 定向覆盖，将 7.3.6 更新至 7.3.7，保留 VitePress 1.6.4 与既有插件。这些补丁修复 [Markdown 多行引用拒绝服务](https://github.com/advisories/GHSA-gfr2-rv44-r23c)与 [Vite/launch-editor 的 Windows 检查绕过](https://github.com/advisories/GHSA-587h-7wmj-h5pm)。

## 替代方案

- 忽略告警或降低门禁：不能满足候选的安全审计验收。
- 全量升级：引入与当前失败无关的兼容改动。
- 继续使用旧版本并创建标签：违反远端 CI 全绿前置条件。

## 后果

Python 的 Pillow 主版本与必要传递依赖发生升级，真实图像、Office/PDF 解析和持久化消费者继续由既有测试拥有。宿主更新后的完整 unit 验证本地解析消费契约；正式镜像及跨 worker 行为仍由新提交的远端系统 CI 验证，不能使用旧镜像的通过结果替代。开发服务未因安装宿主测试依赖而重启。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 锁定依赖不再触发当前安全门禁 | 审计旧环境或忽略真实告警 | manifests、锁文件、dependency-audit workflow | 对更新后的锁执行审计与许可证清单检查 | 既有 Python/Node 漏洞夹具继续失败 | Passed，本地审计与负控 |
| 解析、图像和 Markdown 消费契约保持 | 安全更新破坏渲染或解析结果 | 实际 parser 与渲染消费者 | 后端 unit、前端 unit/build、docs build；远端完整系统测试在提交时尚未运行 | 既有解析、清洗与产物 oracle 保留 | 本地 Passed；新提交远端 CI 在提交时 Not run |

实际执行：

- `uv sync --project backend --locked --group test`：Passed；后端测试使用更新后的依赖。
- `make audit-dependencies`：Passed，Python、CLI、前端和文档无已知告警，Python/Node 漏洞负控按预期拒绝。
- `make audit-licenses`：Passed，输出更新后的传递许可证清单；该命令不构成法律审查结论。
- `uv run --project backend --no-sync --group test pytest backend/test/unit -m 'not slow' -q`：Passed，3395 项，无跳过，包含宿主部署配置与解析夹具。
- `pnpm --dir frontend run lint:check`、`pnpm --dir frontend run test:unit`、`pnpm --dir frontend run build`：Passed，849 项，无跳过，含 typecheck。
- `pnpm --dir docs install --frozen-lockfile`、`pnpm --dir docs run build`：Passed，使用更新后的 Vite。

旧锁的远端[依赖审计](https://github.com/xerrors/Yuxi/actions/runs/40173409664)在同一候选提交上失败，是本变更的直接负向证据。远端新锁的运行结果以 Actions 的提交 SHA 与 job conclusion 为准。

## 风险

当前 Python 约束可解析，无强制覆盖冲突；Pillow 主版本变化仍需要候选镜像中的完整系统结果。确定性 CI 不证明外部供应商与真实语料质量。
