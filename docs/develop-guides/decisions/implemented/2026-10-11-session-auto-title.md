# 会话标题使用一次尽力执行的模型调用

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/services/titles.py

## 问题

Web 把首条消息截断后直接改名，缺少模型生成的主题标题，且可能覆盖用户已指定的名字。标题是辅助展示信息，失败不应影响聊天。

## 决策

### 实现方案

未指定标题的新会话在 Session metadata 中保留一次自动命名资格。worker 开始执行聊天时，在事件循环中发起独立、总计最多等待 8 秒的调用；服务先在会话锁内领取资格并保存截断标题，再在事务外调用现有 fast_model。结果写回重新加锁检查领取标记，手动改名清除标记。协作子会话和显式命名的会话不参与。

前端创建普通会话时不传标题，删除发送前的截断写入，沿用已有会话读取与侧栏同步展示服务端标题。调用失败或进程退出保留临时标题，不恢复、不重试，不新增 BackgroundJob、数据库字段或事件协议。

## 替代方案

- BackgroundJob：提供持久重试与恢复，但标题不需要这项交付保证。
- 前端调用生成接口：需要额外 HTTP 入口，且其他客户端无法自然复用。
- 在聊天前同步生成：增加首个回答的等待时间。

## 后果

后台调用没有持久恢复保证；worker 在领取后退出时保留临时标题。侧栏沿用现有低频刷新，标题可能延迟数秒显示。无自动命名资格的历史会话不补生成。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 生成标题写入 PostgreSQL，重复执行不再改名 | 只有模型返回而未保存 | titles / Session | 隔离 PostgreSQL 集成测试 | 重复调用 | Passed |
| 模型抛出超时后保留截断标题 | 辅助调用异常外溢 | titles | 定向集成测试 | 模型边界抛出 TimeoutError | Passed |
| 手动命名优先 | 迟到结果覆盖用户修改 | threads / titles | 定向集成测试 | 模型等待期间改成相同标题 | Passed |

- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_thread_priority_inputs.py -k auto_title --confcutdir=test/integration/services -q`：3 passed；真实 PostgreSQL 临时 Schema，模型响应固定，不代表真实 provider 可用。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit/storage/test_session_repository.py test/unit/routers/test_public_session_contract.py -q`：27 passed。
- `docker compose exec -T frontend node --test test/unit/threadCreation.test.js test/unit/chatThreadsStore.test.js test/unit/publicAgentApi.test.js`：12 passed；创建与 API 断言更新后重跑对应 3 项通过。
- `docker compose exec -T api uv run --group test pytest test/unit -m 'not slow' -q`：2653 passed、55 skipped；skip 不作为产品链路通过证据。
- 前端 `pnpm run lint:check` 和 `pnpm run build`、修改文件 Ruff、工程信任检查及其 64 项单测、文档 `pnpm run build` 和 `git diff --check` 通过。前端单测按辅助功能范围执行相关集合。

初次交付时真实 HTTP/worker 链路未验证：现有数据库 business Schema 8 与当时主干要求的 6 不匹配。2026-10-11 按用户确认补回主干遗漏提交后，Schema 8 输入消费与标题实现的合并通过独立审查；本次真实 PostgreSQL 输入集合 25 passed（包含上述 3 项标题用例），API/worker 生命周期四个场景通过，`/api/system/ready` 返回 200。详见[主干恢复验证](2026-10-10-input-message-projection.md#_2026-10-11-主干恢复验证)。这些生命周期场景使用显式标题，不证明真实自动命名模型可用；真实外部模型调用与浏览器完整链路仍未执行。
