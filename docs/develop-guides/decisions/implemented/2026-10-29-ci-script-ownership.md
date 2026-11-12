# CI 脚本归位并收敛选择层级

状态：implemented
类型：simplification
Owner：docs/develop-guides/testing-guidelines.md

## 问题

CI 准备、检查、探针和测试混在用户初始化、拉镜像与版本脚本之间。系统专项选择在尚未测得远端收益时增加了第二套输入、条件、汇总模式和测试，维护成本与测试减负目标不匹配。

## 决策

目标是降低查找和修改成本，保留粗粒度 CI 选择、完整业务测试与失败传播。非目标是新增脚本框架、兼容转发、测试清单、按数量删测试或宣称尚未测量的提速。

### 实现方案

`scripts/` 根目录保留用户直接调用的初始化、拉镜像与版本入口；`scripts/eval/` 保留评估用途。CI 的准备、校验、探针、测试与负控 fixture 集中到 `scripts/ci/`，移除重复的 `ci_` 命名前缀。workflow、Makefile、活动文档和内部调用一次更新；实际脚本修正仓库根定位，测试使用迁移后的真实入口。归档历史保留原貌。

PR 继续由一个 gate 根据粗粒度改动选择工作流，必需结果缺失、失败、取消和意外跳过仍拒绝通过。撤销系统专项的二级选择与专用汇总，系统两个 job 恢复完整执行，由既有 `check-jobs` 汇总。保留 JUnit 与耗时输出；远端门禁生效、PR 等待时间、总 runner 时间和偶发失败仍需真实运行证据。前端源码重组测试改用真实模块，取舍和负控见[测试维护决定](2026-10-29-test-maintenance-completion.md)。

## 替代方案

- 保留根目录平铺：调用路径不变，但职责混杂继续扩大。
- 只搬文件、保留所有选择规则：改善导航，不能减少新增维护面。
- 保留旧路径 wrapper 或新增通用运行器：产生双入口或新框架，无当前消费者需要。
- 归位脚本并撤销未证明收益的二级选择：采用，接受局部 PR 系统测试恢复全量的成本。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| CI 入口集中且调用可执行 | 搬迁后根路径、import 或 workflow 指向失效 | scripts/ci 与调用方 | 现有脚本测试、工作流静态检查、活动引用扫描 | 临时工作目录和负控仍执行真实入口 | Passed |
| 顶层门禁拒绝失败，系统完整运行 | 错误 skip 或吞错 | gate、workflow | 既有选择与汇总负控，测试命令前后对比 | 缺失、失败、取消、意外 skip 拒绝 | Passed |
| 维护面净减少，用户入口不变 | 留下旧入口、重复配置或误改部署 | scripts 与活动文档 | diff、旧入口搜索、docs build | 二级选择字段和旧 CI 文件不存在 | Passed |

旧能力不存在：根目录 CI 入口和系统专项二级选择均删除，旧路径不提供转发。
重新引入条件：真实耗时证据证明专项选择有足够收益，并能用更简单的边界保留高风险覆盖；公开消费者确需兼容时另行裁决。

实际验证：

- `backend/.venv/bin/python -m unittest scripts.ci.test_gate scripts.ci.test_reporting scripts.ci.test_resource_boundaries scripts.ci.test_dependency_update_policy scripts.ci.test_release_workflows scripts.ci.test_verify_engineering_contracts scripts.ci.test_bump_version scripts.ci.test_cube_node_setup`：95 项通过，覆盖真实 Git 差异、失败传播、报告、临时入口和既有负控。
- `python3 -m unittest scripts.ci.test_docker_build_context scripts.ci.test_container_permissions`：3 项通过，使用真实 Docker 和合成临时目录、独立镜像与 Compose project。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q`：3357 passed、125 skipped；宿主 `uv run --project backend --no-sync --group test pytest backend/test/unit/config -q`：207 passed，补充容器缺少仓库根文件的配置验证。
- `python3 scripts/ci/verify_engineering_contracts.py`、CI Shell 语法与 actionlint：通过。actionlint 临时配置声明已有 `yuxi-cube` 标签，未执行未安装的 shellcheck/pyflakes。
- 脚本移动前后对照，57 个系统运行命令块除迁移路径外内容与顺序不变。活动调用方无旧 CI 入口，历史执行证据保留原命令。
- 独立 Reviewer 未发现阻断项；修改文档相对链接、`pnpm --dir docs run build` 与 `git diff --check` 通过。

Not run：远端 required 规则、GitHub 条件调度与耗时、完整系统 E2E、KVM 探针和 PowerShell 执行未验证。本地无 `pwsh`，且本次不重建共享业务拓扑。

## 后果

完整系统测试增加少数局部 PR 的潜在成本，但保留原有覆盖。脚本移动会影响 Python import、Shell/PowerShell 路径和测试临时目录；必须验证真实入口，历史命令不视为当前接口。远端 required 规则与全部系统拓扑不由本地静态验证证明。
