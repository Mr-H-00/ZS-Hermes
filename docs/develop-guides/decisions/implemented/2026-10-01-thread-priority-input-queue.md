# Thread 级优先输入队列

状态：implemented
类型：architecture
Owner：backend/yuxi/modules/agents/services/inputs.py

## 问题

接收时绑定 Turn 的 steer 会因在途 Turn 已结束而被拒绝。分离的 follow-up 队列和 Turn 专属 steer 批次不能表达 Thread 级优先级，effective_mode 又重复解释输入类型。

## 决策

follow-up 独立 FIFO；steer 是 Thread 级最高优先级 Input，所有 pending steer 消息按接收顺序合批。领取封闭批次，后到消息形成下一批。pending Input 不绑定 Turn，消费时固定 Turn/Run；运行中在安全边界接续同 Turn，空闲时优先创建新 Turn。普通消息不指定目标 turn_id，Receipt 与 HTTP/SSE 不返回 effective_mode。未指定 mode 的运行中 steer、空闲 follow-up 默认规则保留。

### 实现方案

Input repository 拥有 Thread 内优先排序。每次提交独立保存 Input 与 Receipt，scheduler 领取空闲优先队头，runs service 在保存 checkpoint 后消费 steer；正式 Message 在消费时生成。独立提交、连续就绪前缀合批、逐条取消及转引导由[输入投影分离决定](2026-10-10-input-message-projection.md)拥有，取代唯一 pending steer 和消息成员关系。

每个新 Input 冻结用于新 Turn 的默认配置；同 Turn 接续继承当前 Run 配置。直接 steer 不接受模型或审批配置。waiting/cancelling 的接收限制及 queue_paused 不被优先级绕过。失败、取消和租约失联暂停队列并保留 pending 输入，显式 cancel_input 只取消选中的未消费 Input，显式 continue 解除暂停并优先领取。父重新委派遇子 Thread 暂停或已有 pending 输入时，在 Thread 锁内返回 busy，不写入委派、不解除暂停、不领取旧输入。

SteerMiddleware 将模型前提前让位原因经 GraphExecutionResult 交给输出事务。首次模型前让位允许没有 AI 输出，真正 completed 仍要求同 Run 输出。若让位批次已取消，原 Run 保留 owner 并从已有 checkpoint 继续，不重放用户输入或已执行工具；模型后本已结束的轮次不额外调用模型。执行层在 recorder 与 adapter 之前将多个图段的 seq 衔接为 Run 内递增序列，保持公开事件 ID 唯一及持久审计顺序，不修改源事件对象、payload 和 namespace。

Business Schema 的新库初始化和旧库拒绝边界由输入投影分离及后续附件简化决定拥有。消费提交后才投递 ARQ；恢复扫描包含 steer-only 的空闲队列。

## 替代方案

- 保留 Turn 定向 steer：不能消除在途结束竞态，也不能形成单一优先队列。
- Turn 结束时改为 follow-up：丢失发送方指定的优先级，仍需维护有效模式解释。
- 新建优先队列基础设施：复制现有 Input、锁与消费批次，增加维护表面。
- 只给状态和压缩事件增加图段身份：不能避免正文事件 ID 重复及审计序号倒置。

## 后果

这是持久 schema 与公开消息协议变更。旧客户端发送消息 turn_id 需要更新；旧数据库需部署方另行安排数据迁移，初始化入口不修改旧数据。持续 steer 可以延后 follow-up；独立取消不影响其他 pending Input。

后端验收包含基本测试和完整 worker/SSE/真实 PostgreSQL checkpoint 的 E2E，不以低层级测试替代完整链路。前端 UI 验收由其 owning 变更负责，本记录不重复其审阅。

## 验证

- 后端全量非 slow unit：2427 passed、55 skipped。实际命令为 `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow"`；标准 uv 同步因容器 editable build 的 egg-info 写权限失败，使用已安装的测试环境，不计标准命令为通过。
- `backend/test/integration/services/test_thread_priority_inputs.py` 的 13 个 PG/HTTP 用例分次覆盖并通过：原队列集合 8 项、消费/父委派集合 3 项、参数化模型前让位/取消集合 2 项、续流序号集合 1 项，含 1 项重复。使用真实隔离 PG Schema，不修改现有数据库。复现入口为 `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_thread_priority_inputs.py`；本次并非一次完整重跑，分组选择分别为 `-k 'before_first_model or parent_delegation'`、`-k before_first_model` 和 `-k preserves_wire_ids`。
- 4 项图/执行回归通过：真实 LangGraph 覆盖模型前、工具后和模型后取消，确认不重放输入、工具或额外模型；扩展事件身份回归保留真实 adapter。真实 PG 续流回归保留 shipping execution、recorder 与 adapter，回读 Model.sequence 为 0、10、14，正文 ID 唯一，最终输出仍来自原 Run。图源与远程 tracing 隔离，未声称该用例执行了真实 PostgreSQL checkpoint。
- completed 缺同 Run 输出仍被拒绝；暂停与已有队列使父委派 busy；消息目标 turn_id 返回 422；批次封闭、优先消费、完成竞态、失败/取消保留、恢复扫描及作用域均有业务结果或数据库回读断言。
- 完整后端 E2E 在独立 Compose 槽位、全新 business Schema v12 上一次重跑：44 passed、3 skipped，耗时 475.11 秒。实际命令为槽位 Compose 的 `exec -T api uv run --no-sync --group test pytest test/e2e -m e2e -q --tb=short --durations=20`，结果日志 `/tmp/yuxi-priority-e2e-all-final.log`。包含真实 API、worker、Redis 投递、SSE、PostgreSQL checkpoint、安全边界、沙盒文件与等待恢复；确定性场景使用模型重放服务，等待恢复读取真实 Langfuse。steer 用例回读两条 steer 合批优先于 F1、接续原 Turn，随后 F1 在不同 Turn 完成并持久化预期输出。公开协议断言按完整 SSE frame、output items 归属及父工具调用关联子 Run 验证，不按内容中的链接或最新创建时间猜测归属。
- 3 项显式真实模型探针未执行：未配置 `YUXI_REASONING_E2E_MODEL`、`E2E_VISION_MODEL` 和 `E2E_NON_VISION_MODEL`；独立复跑 `-rs` 确认跳过原因，不计为通过。pytest cache 写权限 warning 不影响断言；隔离槽位已停止，保留状态且未修改既有数据库。
- 后端实现经全新独立 Reviewer 对完整需求、scoped diff、源码、规范和实际日志审查通过；本轮 E2E 断言修正后按用户要求不再调用 Codex。相关 Ruff、工程契约检查及 64 项 verifier 单测通过。
- 旧 Schema 扩大测试首例超时未计为通过；本轮未重复前端 UI 验收。
