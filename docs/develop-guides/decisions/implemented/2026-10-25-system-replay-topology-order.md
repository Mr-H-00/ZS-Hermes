# 共享模型重放在 Knowledge 拓扑切换后启动

状态：implemented
类型：testing
Owner：.github/workflows/system-tests.yml

## 问题

候选 `f1ea93c7` 的生命周期阶段出现批量模型连接失败并在 25 分钟超时。共享 OpenAI 与协作模型重放通过 `docker compose exec -d api` 启动；后续 Knowledge 关闭/恢复步骤重建 API 容器，独立重放进程随旧容器结束。应用 readiness 不能证明这些测试依赖仍可用。

## 决策

### 实现方案

把现有 Knowledge 关闭/恢复验证整体移到初始化 E2E 管理员之后、启动共享模型重放之前。该验证使用自己启动的 HTTP 重放，不依赖两个共享进程。恢复完整拓扑后，沿用原有共享重放启动与两个 health 协议检查，后续全部消费者继续按原选择器执行。

`scripts/ci/test_resource_boundaries.py` 检查 Knowledge 拓扑切换先于共享重放启动；负控恢复旧顺序必须被拒绝。不新增服务、重启补偿、超时配置或并行 job，不改变任何 E2E oracle。

## 替代方案

- 增大生命周期超时：不能恢复已经结束的模型进程。
- 在 API 每次重建后补启重放：重复维护步骤，仍需防止后续重建遗漏。
- 把重放改成独立 Compose 服务：可隔离生命周期，但扩大当前修复范围。

## 后果

Knowledge 关闭/恢复测试在共享重放启动前完成，两个共享进程与后续消费者使用同一个 API 容器。应用 readiness 与测试模型 health 分别证明各自的前置条件；生产拓扑与产品执行逻辑不变。

本地开发服务不用于切换 Knowledge 拓扑；完整切换后的依赖存活由远端隔离拓扑验证。该修复不证明其他 E2E 已通过，仍须读取最终结果。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 共享重放启动后不会被 Knowledge 测试销毁 | 错误顺序仍存在 | workflow 步骤顺序、API 容器生命周期 | workflow 约束单测；新提交的远端完整系统执行在提交时 Not run | 旧 workflow 在新顺序检查下 1 failed / 1 passed；恢复旧顺序负控必须被拒绝 | Passed，本地顺序约束 |
| 生命周期使用真实可达的共享模型 | 应用 readiness 被当作重放 readiness | 两个重放 health、真实 worker E2E | 端点协议回读与同一生命周期用例的持久结果 | 缺少共享端点时 Turn 在 30 秒内未终结；启动重放后同一用例通过 | Passed，本地目标用例 |

实际执行：

- `python3 -m unittest scripts.test_ci_resource_boundaries scripts.test_release_workflows`：Passed，23 项，含错误步骤顺序负控。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`：Passed，检查器负控 66 项。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q`：Passed，3273 项、122 skipped，81.86 秒；缺少宿主配置文件的范围保持已有宿主验证。
- 重启后的本地 API 对 `http://localhost:8765/health` 与 `http://localhost:8766/health` 均返回连接失败。按正式 workflow 命令启动重放后，两端点均回读 `status: ok`。
- 同一 `test_public_session_initial_text_stream_and_saved_items[text-False]` 在显式设置 `OPENAI_REPLAY_URL=http://api:8765/v1`、`COOPERATION_REPLAY_URL=http://api:8766/v1` 后执行：缺少重放时 1 failed，39.04 秒，观察结果为 Turn 未在 30 秒内终结；重放恢复后 1 passed，3.38 秒，既有 HTTP、SSE 和持久 item 断言不变。

旧[远端系统 run](https://github.com/xerrors/Yuxi/actions/runs/40175073856)的 worker 日志直接记录 OpenAI 连接失败，是连接根因的证据；本地 Turn 超时只是消费者观察结果，二者不互相替代。完整远端生命周期与后续容器链路仍须在新提交上执行。
