# 输入框提及智能体

状态：implemented
类型：feature
Owner：frontend/src/modules/session/model/useAgentMentionConfig.js

## 问题

聊天输入框能提及知识库和 Skills，但用户无法从同一入口选择已创建的智能体用于协作。

## 决策

### 实现方案

SessionWorkspace 将现有智能体目录传给提及配置，仅保留 can_run 的条目。资源候选使用名称和描述搜索，插入 `@agent:<agent_id>`；提及解析和显示名称映射负责输入 chip 与消息展示。菜单顺序和键盘选择保持一致。协作 middleware 说明该引用对应 create_session 的 agent_id，由当前智能体按任务需要调用，现有后端授权仍是执行边界。

不改变当前会话选中的智能体，不新增路由、持久字段或自动派发逻辑。

## 替代方案

直接切换收件智能体会改变会话归属；只插入名称无法区分同名配置。两者都不满足用户确认的协作语义。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 可运行目录参与候选且更新响应式生效 | 展示不可运行配置 | useAgentMentionConfig.js | 前端 unit | can_run=false 与缺失标识 | Passed：相关 unit 与浏览器验证 |
| 引用保存 ID、显示名称、支持删除 | 同名误选或 chip 退化为文本 | mention_utils.js、MessageInputComponent.vue | unit、浏览器 DOM | 同名不同 ID、无其他资源 | Passed：相关 unit 与浏览器验证 |
| 模型请求收到智能体引用格式与按需协作说明 | 提及被误认作切换会话 | cooperation.py | middleware unit、源码审查 | 移除提示注入使模型请求断言失败 | Passed：middleware unit；Inspected：装配链路 |

前端执行 `pnpm run test:unit`（511 passed）、`pnpm run lint:check`、`pnpm run build`，文档执行 `pnpm --dir docs run build`，均通过。`python3 -m unittest scripts.test_verify_engineering_contracts` 的 64 项通过。`python3 scripts/verify_engineering_contracts.py` 被已有 `2026-10-09-agents-session-protocol.md` 的 `refactor` 类型阻断，此记录未随本功能修改。

浏览器运行本 worktree 前端，连接现有开发 API，回读候选 DOM 与 chip 的 `data-mention-raw`，验证名称、描述和 ID 搜索、点击、Enter、Tab、方向键顺序、Backspace、Escape、无结果以及 1440/1024/768/375 宽度；浅深主题截图在交付中提供。所连开发 API 的线程列表协议与本 worktree 不一致，导航读取报错；验证范围限于输入框及提及目录，完整消息发送和真实 provider 根据提及创建协作子会话为 Not run。

后端在现有开发 API 容器中复制完整 tracked 仓库至隔离目录，使用该目录的源码与测试，保留已安装依赖并以 `--no-sync` 执行 `pytest -c pyproject.toml test/unit -m "not slow" -q`：2611 passed。直接使用 worktree 的 `docker compose exec` 因缺少环境配置不能启动；首次只复制包与测试造成路径和跨包导入失败，完整布局验证替代了该无效结果。前端聚焦执行 `pnpm exec node --test test/unit/agentMentionConfig.test.js test/unit/mention_utils.test.js`：6 passed。

## 后果

模型自主判断是否调用；提及本身不保证启动协作。目录权限变化由协作服务即时重新校验。浏览器和真实模型验证依赖运行环境，缺失证据须明确记录。
