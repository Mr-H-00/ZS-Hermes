# PR 汇总门禁与测试证据收敛

状态：implemented
类型：testing
Owner：.github/workflows/ci.yml

## 问题

各质量工作流独立按路径触发，缺少每个 PR 都产生结果的汇总检查。GitHub 当前 main 生效规则未要求测试通过；workflow 成功不能解释为强制合并门禁。系统测试准备和执行成本显著高于 unit，缺少统一保留的测试结果用于后续优化。工程检查器复制完整 pytest 命令和节点，修改报告参数也需要同步多处无关期望。

容器与宿主 UID 不同，报告目录可写不能证明宿主能读取 `0600` JUnit。许可证步骤需要下载运行时 wheel，默认 30 秒网络读取超时会因下载停顿失败。

## 决策

### 实现方案

无路径过滤的 PR caller 调用现有质量工作流，保留各工作流 push、tag 和手动入口。PR 选择按后端、前端、文档和依赖等粗粒度范围计算，共享配置、CI 和无法分类的文件保守扩大检查；现有测试选择器和用例不删减。选择逻辑由一个标准库脚本拥有，caller 只连接其输出与 job。仅修改后端 unit 时保持不启动完整系统拓扑。各多 job 被调用工作流检查必需子 job 全部成功；顶层 `check` 必须具有选择结果；子工作流显式使用 `check-jobs`；系统测试执行完整 job，由 `check-jobs` 汇总；取舍见 [CI 脚本归位决定](2026-10-29-ci-script-ownership.md)。顶层汇总拒绝选择失败、缺失结果、必需检查失败、取消及意外跳过。PR 不发布 Pages，不使用部署凭据。

pytest 入口在 CI 显式启用 JUnit 和慢测试输出。不同调用各自写报告，artifact 仅保留测试结果；失败仍由原测试命令返回，skip 单独记录。工程检查器保留稳定执行入口和已知失败传播约束，移除逐项复制的业务测试节点与完整命令文本。

容器生成的 JUnit 遵守 API entrypoint 的 `umask 077`。两个系统 job 在上传前将专用 `backend/test/ci-reports` 目录及文件归属交给宿主 runner，保持报告 `0600`，不修改业务存储或放宽应用 umask。许可证审计步骤的 uv HTTP 超时为 120 秒，允许大型 wheel 下载的短暂网络停顿，job 仍受 20 分钟上限约束。

测试规范明确新增测试的独立失败面、跨层证据差异、删除替代证据以及偶发失败处理。本决定不修改业务实现，不批量删除测试，不改变高风险验证层级，不引入函数级选择系统或新的测试框架。远端 required check 需在对应 workflow 已部署并产生真实结果后启用，不将本地验证写成远端生效。

## 替代方案

- 每个 PR 执行所有完整拓扑：无需选择器，但文档与前端改动也承担系统测试成本。
- 跨 workflow 查询并轮询检查状态：引入 API 权限、运行关联与等待状态，不采用。
- 直接把带路径过滤的工作流设为 required：未触发检查可能保持 Pending，不采用。
- 可复用工作流与薄汇总入口：复用测试主体，显式检查执行结果，采用。
- 放宽应用 umask 或公开所有容器输出：超出测试报告需要，不采用；归属转换仅作用于 CI 专用报告目录。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 每个 PR 汇总且只接受必要检查成功 | 路径过滤缺结果、取消或 skip 假绿 | ci.yml 与汇总脚本 | 选择与结果单测、workflow 静态验证 | 缺必需 job、失败、取消、意外 skip、损坏选择结果 | Passed |
| 现有业务测试范围与退出状态保留 | 接线迁移遗漏测试或吞错 | 各 workflow、测试运行脚本 | selector 对比与脚本测试 | pytest 返回非零仍导致入口失败 | Passed |
| 测试报告不覆盖且区分跳过 | 多次 pytest 覆盖报告、skip 计入成功 | 测试入口与 JUnit artifact | 小型真实 pytest 样本与报告回读 | 失败和 skip 样本结果分别存在 | Passed |
| 容器私有报告可由宿主上传 | 目录可写但 `0600` 文件归属不同，压缩上传 EACCES | system-tests.yml 上传前归属步骤 | entrypoint 创建私有文件，执行真实 workflow 命令后由宿主解析 XML | 归属转换前宿主读取明确失败，恢复旧 workflow 时回归失败 | Passed |
| 检查器保护稳定入口 | 参数调整误报、实际入口丢失漏报 | 工程契约检查器 | 检查器单测与故障注入 | 移除入口或加入已知吞错模式被拒绝 | Passed |
| 规范与现有系统事实一致 | 文档误称远端已阻断 | 测试规范与工程信任系统 | 相对链接、docs build、独立 Review | 必需检查未部署明确标记未验证 | Passed |

本地验证结果：

- `backend/.venv/bin/python -m unittest discover -s scripts/ci -p 'test_*.py'`：102 项通过，包括选择与汇总负控、真实 pytest 的通过/失败/跳过 XML 回读，以及真实文件、Docker 构建上下文与权限检查。
- `python3 scripts/ci/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.ci.test_verify_engineering_contracts`：通过；检查器自测 56 项。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q`：3359 passed、125 skipped。容器缺少仓库根文件的配置测试另由宿主 `uv run --project backend --no-sync --group test pytest backend/test/unit/config -q` 验证，207 passed；其余跳过不算产品通过。
- 对照变更前的业务执行节点：系统、unit、运行脚本和独立服务脚本的测试路径、节点与顺序保留；不包含已迁移的事件路径过滤。
- CI Shell 语法、新增 Python 脚本 Ruff 检查、`git diff --check`：通过。
- actionlint v1.7.12 对全部工作流通过；临时配置声明已有 `yuxi-cube` 自托管标签，未安装的 shellcheck/pyflakes 未执行。
- 修改文档相对链接检查与 `pnpm --dir docs run build`：通过。

Not run：完整 Compose integration/E2E 未重跑，业务测试与选择节点未变；Windows PowerShell 执行、GitHub fork PR 调度、实际 artifact 上传、Pages 部署与远端 required rule 未验证。本地测试不证明远端合并已经阻断。

## 后果

选择器可能漏掉共享影响，因此未知路径默认扩大执行。GitHub 调度、fork PR 和远端 branch rules 的最终行为需要真实 PR 验证；本地负向测试不能替代。系统测试执行完整 job，见 [CI 脚本归位决定](2026-10-29-ci-script-ownership.md)；镜像构建拓扑保留；前端组件测试采用[真实模块加载](2026-10-29-test-maintenance-completion.md)，业务实现保持不变。
