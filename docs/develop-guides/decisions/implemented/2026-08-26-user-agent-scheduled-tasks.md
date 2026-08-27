# 用户自建 Agent 定时任务

状态：implemented
类型：feature
Owner：backend/package/yuxi/services/scheduled_agent_service.py

## 问题

用户可以手动提交 AgentRun，但无法把固定的 Agent、Project 与提示词配置为按时执行的长期任务。定时触发不能依赖 API 进程内存，也不能复制 AgentRun 的执行状态，否则重启、多 worker 和状态回写延迟会造成任务丢失、重复或永久误判为运行中。

## 决策

PostgreSQL 保存用户自己的任务定义和触发记录。任务定义拥有 cron、IANA 时区、启用状态与下一次触发时间；触发记录只拥有 occurrence、配置快照和提交状态。`AgentRunRequest` 与 `AgentRun` 继续分别拥有排队和执行状态，接口查询时关联它们，不把运行结果周期复制回触发记录。

worker 在现有 reconciliation loop 中恢复未提交触发记录，并使用 `FOR UPDATE SKIP LOCKED` 领取到期任务。领取事务推进 `next_run_at`、创建唯一 occurrence 并提交，随后在 occurrence 行锁内使用稳定 request/thread ID 幂等调用 `submit_run_command`。明确的 Project、Agent 或请求契约错误终结 occurrence；未知瞬时错误保持 `dispatching`，恢复轮次在行锁内重查 Request 后再收敛。单条 occurrence 的失败只记录并留待下轮恢复，不阻断同批记录、其他到期任务或普通 worker 启动。停机错过多个周期时合并为一次，同一任务已有非终态 Request/Run 时记录 skipped。暂停期间不产生 occurrence，恢复时从当前时间重新计算下次触发。

每次触发创建绑定原 Project 的新 Conversation。任务软删除只停止未来触发；账号软删除在同一事务物理删除任务定义，并由数据库级联清理调度历史；Project 物理删除使用相同外键级联。Run now 复用相同的 occurrence 与提交路径。

前端入口位于智能体管理的定时任务 Tab。默认只显示任务列表；选择任务或点击 Header 中的新建入口后才展开工作区。工作区不区分查看态和编辑态，名称、指令、运行上下文与频率始终可以直接编辑，合法变更经短暂 debounce 后自动保存；新任务在必填项完整后自动创建，不提供第二套编辑页面或保存按钮。

频率编辑只为每日、每周、每月和每年提供结构化控件，其他表达式保留为自定义五段 Cron。Project、模型和审批复用现有选择组件，任务指令使用普通多行输入；聊天附件、提及、发送和流式状态不进入定时任务。创建默认使用 `default` 审批模式，`always_trust` 只允许用户显式选择。

运行记录直接链接本次触发创建的 Conversation。接口只在对应 `AgentRunRequest` 已存在时返回 `conversation_available=true`，前端同时要求 `thread_id` 才允许跳转；尚未创建对话的 occurrence 不可点击。

## 替代方案

- API 进程内定时器：重启和多实例下不能恢复或去重，拒绝采用。
- Redis/ARQ 保存 cron：Redis 不是业务事实 Owner，无法闭合权限、恢复和审计，拒绝采用。
- 在触发记录复制 AgentRun 终态：需要额外轮询和回写，会形成第二状态 Owner，拒绝采用。
- 复用聊天输入组件：定时任务不需要附件、提及、发送和流式状态，耦合公共聊天链路的成本高于直接表单，拒绝采用。
- 独立详情路由、展示态和旧地址兼容：该能力尚未发布，没有兼容 consumer，拒绝增加页面、状态和导航表面。

## 后果

任务定义和 occurrence 成为 PostgreSQL 业务事实，调度复用现有 worker 健康与 AgentRun 生命周期。代价是 business schema 升至版本 3，并增加 `croniter` 依赖。任务删除保留历史 Conversation、Message 和 AgentRun；账号删除会清理任务与调度 occurrence，但已进入普通运行链路的 Conversation、Message 和 AgentRun 仍按各自生命周期处理。

前端只保留列表和一个原地编辑器；频率转换与自动保存各有一个就近、可独立验证的语义 Owner。无法完整填写的草稿不会发送请求；创建请求在途时的新输入会继续 PATCH 同一任务，旧请求回包不能覆盖更新的非法或失败状态，保存失败会保留同一 payload 并阻止任务、标签或路由切换；无法无损映射的 Cron 不会被结构化控件改写。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 用户只能管理自己的任务并绑定可见 Agent 与自有 Project | 越权读取、修改或触发 | router、service、repository | `python -m pytest -q test/integration/api/test_scheduled_agent_api.py` | 其他用户读取不到任务，修改、触发和删除均返回 404 | Passed |
| 多 worker 对同一 occurrence 只创建一个触发意图，瞬时失败可以恢复 | 重复请求、模型副作用、occurrence 永久丢失或单条坏记录拖垮 worker | repository + scheduled service | service unit + `python -m pytest -q test/integration/services/test_scheduled_agent_repository.py` | 并发领取只能有一个事务取得任务；Request 写入前首次失败后恢复轮次只产生一个 Request；首条持续失败时同批第二条仍提交 | Passed |
| 触发记录不复制 Request/Run 终态 | 镜像状态漂移后永久阻塞后续任务 | ScheduledAgentRun + AgentRunRequest + AgentRun 查询 | service unit + PostgreSQL integration | AgentRun 终态后重叠判断恢复为 false | Passed |
| occurrence 提交后进入统一 Request/Run 与 worker 链路 | ARQ 先于持久事实，或只创建任务不执行 | scheduled service + submit_run_command + worker | `python -m pytest -q test/e2e/test_deterministic_agent_path_e2e.py::test_scheduled_task_run_now_reaches_exact_conversation_and_result` | E2E 回读 Run 终态、输出和同一 thread 历史 | Passed |
| schema 1 可以幂等升级，账号软删除同步清理任务数据 | main 数据库无法升级、丢失既有数据或删除账号后恢复旧任务 | storage migration + UserRepository + PostgreSQL constraints | `python -m pytest -q test/integration/services/test_schema_migration_version.py test/integration/services/test_scheduled_agent_repository.py` | 隔离 v1 schema 连续升级两次仍保留既有 User、Project、Job、Run；未知未来版本被拒绝；真实软删除后 Job 与 occurrence 均不存在 | Passed |
| 暂停不补跑，恢复从当前时间计算下一次触发 | 恢复后立即执行暂停期旧时间 | scheduled service | service unit + PostgreSQL integration | `false → true` 后 `next_run_at` 晚于当前时间 | Passed |
| 无人值守任务默认不完全信任工具 | 未显式授权即执行敏感工具 | router + service + editor | router/service unit + HTTP integration | 省略审批字段时持久化为 `default` | Passed |
| 默认列表、按需展开、原地自动保存和历史跳转可用 | 首屏被表单占满、保存丢失或跳入相邻对话 | ScheduledAgentsView + ScheduledAgentEditor | web unit、lint、build、真实浏览器与 API 回读 | 延迟 create 期间继续编辑时最终只保留最新版；在途请求后出现非法编辑时旧回包不能放行；失败 payload 重试成功前任务、标签和路由均不能离开；历史必须同时具有 thread 与可用标记 | Passed |

真实浏览器已覆盖默认列表、点击展开、新建草稿、自动保存 API 回读、运行历史跳转、浅色、暗色与 1024px 响应式。定时到点后的周期扫描未单独等待真实时钟触发；PostgreSQL claim integration 与 worker startup/reconciliation unit 分别覆盖领取和装配边界。

旧能力不存在：不保留独立 scheduler 状态回写循环、聊天输入组件扩展、单消费者表单/路由 helper、独立展示态、保存按钮、未发布旧地址兼容和未消费的详情/历史 API。

重新引入条件：只有出现两个以上真实 consumer 或已发布兼容承诺时，才提取共享表单/导航抽象或增加独立历史接口；只有 AgentRun 无法提供所需审计事实时，才单独提案增加新的持久执行字段。
