# 前端应用与业务模块主干

状态：implemented
类型：architecture
Owner：frontend/src/app/main.js

## 问题

页面、业务组件、状态和通用工具按技术种类集中堆放，AgentView 同时管理路由、智能体选择和配置弹窗；根组件与访问守卫参与业务初始化，不同入口的依赖和失败边界难以维护。

## 决策

### 实现方案

应用装配、布局和路由归属 app，路由入口归属 pages，业务 UI、状态和逻辑按实际领域归属 modules，通用 UI 和工具归属 shared。API 保留集中 HTTP 边界。旧技术分类目录和入口已移除，源码、动态入口、测试与 active 文档引用指向实际 Owner；历史归档保留其快照。

| Owner | 职责与运行链路 |
|---|---|
| frontend/src/app/main.js、app/App.vue | 安装 Pinia、Router、Antd，提供主题与根 RouterView；品牌加载由信息 store 合并请求 |
| frontend/src/app/router/index.js | 页面路由与身份、角色体验守卫；角色不足直接导航到 Agent 页 |
| frontend/src/pages/AgentView.vue、pages/agent/threadRouteCoordinator.ts | 装配会话工作区与智能体选择器；组件准备后消费路由，队尾注册每次选择并跳过过期 revision，当前失败才重定向，成功消费 agent query |
| frontend/src/modules/agents/model/agent.js | 多入口等待同一初始化 Promise；身份 reset 增加 generation，旧目录、元数据与详情响应失效，旧 finally 不移除新请求的 Owner |
| frontend/src/modules/agents/ui/SessionAgentPicker.vue | 智能体切换与编辑入口；打开弹窗时并行加载选项与组件，挂载完成后调用编辑或创建命令 |
| frontend/src/modules/session/ui/SessionWorkspace.vue | 原有聊天编排的实际入口，消费会话、输入、工具、审批和工作区组件 |
| frontend/eslint-boundaries.js | 检查别名、相对路径、/src 路径和静态动态导入的目标；modules 禁止引用 app/pages，shared 禁止引用业务层与 API，旧分类入口被拒绝 |
| frontend/tsconfig.json、frontend/package.json | 新 TypeScript 核心经 strict tsc 检查，生产 build 先执行 typecheck |

领域模块的 ui 保存界面，model 保存状态、composable 与领域转换。目录归属覆盖 session、agents、projects、workspace、knowledge、extensions、identity、settings、tasks、dashboard。跨领域的现有真实 consumer 保留直接引用；统一 public entry、共享 Thread Session、纯消息投影、ThreadSession 与 SessionComposer 的新契约由[完整重构提案](../proposed/2026-09-30-agent-view-frontend-refactor.md)继续收敛。

会话模块直接消费当前 Public Thread API：History 返回 thread/runs/items，SSE data 是公开事件，公开 item reducer 按 event_id 去重并保护已结束内容块，显示平滑只改变 displayText。SSE 恢复等待快照处理结束再消费后续事件，游标保存后端原值。输入和取消遵循 agent.session.input.*，业务选项位于 yuxi；消息通过 mode 指定执行方式，steer 的目标 Turn 由后端确定并沿用执行配置，客户端不提交消息 turn_id。协议与 Schema 的唯一约束由[公开事件决策](./2026-09-30-langgraph-agents-events.md)及后端 Owner 拥有。

子任务拥有独立 Thread/Turn/Run。子详情先从指定 Run 定位自己的 Turn，再读取等待点与恢复结果；父会话提供的状态读取和审批入口与子详情一起装配。父完成不停止子任务观察，审批绑定可见子 Thread 的真实 waitpoint。子订阅在断流或 resync 回读到 waiting 时也恢复问答或审批，不能要求重开面板才显示。共享 Thread Session 和自洽阅读组件属于完整提案的后续阶段，当前父子状态提供仍由 SessionWorkspace 拥有。

TypeScript 使用 6.0.x，与当前 parser 的[官方支持范围](https://typescript-eslint.io/users/dependency-versions/)一致。strict 目前覆盖新增 TypeScript 核心；既有 JavaScript 与 SFC 的全量转换和 vue-tsc 检查仍属于完整提案的后续阶段。

## 替代方案

只移动目录可以明确文件归属，却保留页面业务编排与重复启动。一次重写消息、输入、附件和全部面板会把运行状态迁移与目录调整绑定。本决定先收敛装配和页面职责，让后续内核替换有明确 Owner 与依赖 gate。

## 后果

业务文件有明确归属，页面通过可测试的串行协调器适配路由。并发初始化的等待结果属于 Agent store，身份重置后的旧读取不得回填当前状态。目录迁移覆盖全部前端入口，原有用户功能与当前公开事件、独立子 Turn、FIFO 和 SSE 恢复契约共同作为约束。前端与匹配协议及 Schema 的后端组合运行，开发与生产镜像从冻结锁文件安装 TypeScript/parser 等依赖。

前端在独立工作树运行，通过隔离环境的当前 API 检查界面。450px 的应用最小宽度仍由现有布局与样式拥有；390px 视口下页面宽度为 450px，与原工作区的直接 DOM 对照一致。窄屏布局优化属于后续界面设计，不能把本阶段截图当作完整移动端适配。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 依赖边界由实际 lint 拒绝 | 可静态解析的绕路路径漏检或合法路径误拒绝 | frontend/eslint-boundaries.js | frontend lint 与 frontendBoundaries unit | modules 引入 pages、shared 引入 modules/API，别名包含 ..、模板字符串与 /src 导入被拒绝 | Passed |
| 快速切换与结束窗口保留最新路由 | 旧请求覆盖当前路由、结束时 pending 无执行者 | pages/agent/threadRouteCoordinator.ts | threadRouteCoordinator unit | 延迟旧线程失败、同一 Promise 微任务窗口新增路由、组件未挂载、卸载后返回 | Passed |
| 多入口等待目录且身份重置失效旧读取 | 第二入口提前继续、旧账户目录或详情回填、新 Promise 被旧 finally 清除 | modules/agents/model/agent.js | agentInitialization unit | reset 前的目录、知识库与工具元数据、详情在 reset 后返回 | Passed |
| 既有组件和全部入口装配正常 | 动态 import 遗漏、测试读取旧位置 | app/router 与领域 ui | frontend 全量 unit、lint、build | 既有历史、排队、输入、审批、工具、文件与子线程测试保持行为断言 | Passed |
| 新逻辑 strict 类型检查生效 | 只转译或错误类型进入协调器 | tsconfig.json 与 package.json | typecheck，build 内执行 tsc | 临时调用传 threadId:42，被 TS2322 拒绝，随后删除临时 fixture | Passed |
| 页面选择与非核心编辑按需装配 | 挂载前丢 query、懒组件 ref 未就绪、编辑或创建入口丢失 | AgentView 与 SessionAgentPicker | 已登录真实浏览器运行 frontendArchitecture.js；浅/深色桌面和窄屏截图 | 打开前无编辑组件请求，query 被消费，编辑与新建表单均挂载并关闭，桌面无横向溢出 | Passed |
| 子任务快照恢复等待交互 | 等待事件漏收后只有消息、没有问答表单 | modules/session/ui/SubagentThreadView.vue | subagentThreadLifecycle unit 与独立 Reviewer 复跑 | EOF 与 resync 回读 waiting，旧实现均不能恢复审批；隐藏面板的迟到响应不回填 | Passed |
| 文档定位与当前架构一致 | 旧 Owner、相对链接或说明误宣称完整重构完成 | ARCHITECTURE.md 与本记录 | 工程契约、其 64 项 unit、相对链接检查、docs build、git diff --check | owning 路径不存在由工程契约拒绝 | Passed |

前端检查在独立 frontend Compose 服务执行，命令为 pnpm run lint:check、pnpm run test:unit、pnpm run build；build 包含 strict typecheck。浏览器在匹配协议的隔离 API 环境核对当前 DOM 和公开快照，截图只包含验证资源。完整统一阅读器、输入组件、草稿与全量 SFC TypeScript 转换仍按 proposed decision 的各自验收项推进。

公开协议整合已应用到主工作区。最终前端命令 pnpm run lint:check、pnpm run test:unit、pnpm run build 通过，unit 为 400 passed；build 包含 strict typecheck。工程契约与其 64 项 unit、相对链接检查和 docs build 通过。独立 Reviewer 核查完整整合差异、当前 HTTP 回执、队列与 SSE 契约，并独立复跑边界、初始化、队列和子任务生命周期测试；未发现阻断问题。生产构建保留现有大 chunk 提示。

主工作区后端在整合期间从 Schema 11 更新为 12，消息 turn_id 由服务器绑定。最终前端保留主工作区同期更新的 API、发送与队列投影，并在独立工作树冻结当前后端，使用新建 PostgreSQL 数据库和独立 Compose 服务核对 Schema 12，不迁移或删除原环境数据。该快照的 python -m pytest test/unit -m "not slow" 结果为 2423 passed、55 skipped。

Schema 12 的 lifecycle、extended、subagent boundaries、key scope 和公开事件 E2E 首轮为 28 passed、4 failed，另有 1 项 teardown error。失败包括旧测试提交消息 turn_id、关闭 Langfuse 时两项审计断言，以及接管输入收尾后测试归档被 pending steer 阻止。冻结主工作区更新后的用例与 checkpoint 收尾修复、恢复匹配的观测配置，并通过公开 cancel_input API 清理本轮测试留下的 pending steer 后，三项 lifecycle 与全部三项公开事件测试复验为 6 passed，含 session cleanup。两批结果分别记录，不宣称对最终后端快照重新执行了完整 32 项。更早的 Schema 11 快照完整 32 项 E2E 通过，仅作为该版本证据。

浏览器使用 Schema 12、真实 HTTP 和本地 replay provider，已回读图片与已确认附件的公开 item；刷新后用户输入和最终回答各一条，无重复。父子 Thread/Turn 独立，父已完成时可恢复子问答，子提交回执指向自己的 Thread/Turn；子命令拒绝后回读子 Turn 完成、current Run 已切换，父 Turn 仍完成。浏览器离线期间释放模型回放，恢复连接后回读 Turn 为 completed，公开 item 和刷新 DOM 中最终回答均为一条。真实外部 provider 为 Not run。路由参数消费、编辑与新建懒加载已在此前匹配协议的真实页面核对。450px 最小宽度约束保留，390px 视口存在既有横向滚动；不宣称完成移动端适配。

子任务恢复测试使用真实审批状态与事件消费者。新增 EOF、resync 负向案例在修复前为 7 passed、2 failed，失败原因是持久等待点未恢复到可回答状态；修复后生命周期测试 9 passed。独立 Reviewer 另验证隐藏面板后的迟到快照不会恢复审批。

主工作区默认环境的要求命令 docker compose exec api uv run --group test pytest test/unit -m "not slow" 在依赖同步阶段失败，原因是现有 yuxi.egg-info 目录不可更新，未进入测试。原运行环境数据库仍为 Schema 11，与主工作区 Schema 12 不匹配；通过的后端和浏览器检查使用隔离环境，不把默认运行环境计为通过。主工作区并行的后端修改继续保留，验证只证明被冻结的后端快照。
