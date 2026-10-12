# 按功能拆分工作区导航

状态：implemented
类型：feature
Owner：web/src/layouts/AppLayout.vue

## 问题

定时任务、模型供应商和技能扩展嵌在其他页面的标签中，用户无法从侧栏直接发现这些功能。现有模块名称也需要与用户要求一致。

## 决策

会话导航按置顶、项目、最近排列；置顶分组仅在存在置顶会话时显示，支持折叠，新置顶会话会展开该分组。置顶会话集中展示，不在项目和最近重复出现；取消置顶后按原项目归属返回。项目状态仍汇总其全部会话，包括置顶会话。分组投影由 `web/src/utils/projectConversationGroups.js` 拥有，交互由 `web/src/components/ConversationNavSection.vue` 拥有；沿用后端保存的 `is_pinned`，不新增持久化状态。

侧栏依次显示新对话、定时任务、网盘、知识库、智能体中心、技能・工具・MCP、API服务、系统仪表盘。定时任务使用圆形时钟图标，网盘使用云朵图标。知识库保留原知识管理页面；技能、工具和 MCP 使用独立页面。API服务复用模型供应商管理面板。

导航由 AppLayout 拥有，页面装配及体验权限由 router 和 views 拥有；后端 API、持久化、任务执行及授权保持现有语义。知识能力关闭时不展示知识库；知识库保留登录用户可见入口，知识库管理仅管理员可用。普通用户继续可访问技能，API服务仅管理员可访问，系统仪表盘仅超级管理员可访问。

## 替代方案

只新增指向旧标签的侧栏链接会留下混合页面和重复入口，无法完成独立模块的要求；重新实现管理面板会扩大维护范围。采用独立页面入口并复用现有面板。

## 验证

置顶分组调整按用户既有要求未执行测试、构建或页面验证；相关分组测试的预期结果已随展示语义更新，未运行。

网盘和知识库的名称、云朵图标及智能体中心排序调整按用户明确要求不执行测试或页面验证；下列证据属于此前模块拆分，不能作为本次文案、图标及排序调整已经验证的依据。

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 名称与顺序符合需求 | 菜单遗漏或位置错误 | web/src/layouts/AppLayout.vue | 浏览器侧栏与页面回读 | 旧菜单缺少独立入口 | Inspected：源码与浏览器页面已核对 |
| 页面分离并复用原功能 | 旧标签仍混合其他功能 | web/src/router/index.js、web/src/views | Web build、浏览器导航与技能详情返回 | 智能体页仍显示定时任务标签 | Passed：build 成功，浏览器回读完成 |
| 角色与知识能力保持现有约束 | 拆分后错误显示管理入口 | web/src/router/index.js、web/src/views | `docker compose exec -T web node --test test/unit/navigation_modules.test.js` | 普通用户进入 API服务、MCP、Dashboard 被拒；LITE 进入资源中心转技能模块 | Passed：实际守卫与 matcher 测试通过 |

`docker compose exec -T web pnpm run lint:check`、`docker compose exec -T web pnpm run build`、`pnpm --dir docs run build`、工程契约检查及其单元测试通过（62 项，1 项跳过）。真实浏览器检查管理员侧栏、资源中心、任务空态、智能体中心、API服务、技能详情返回、浅色/深色与折叠侧栏。普通用户与 LITE 通过前端路由测试验证；真实普通用户/LITE 浏览器及任务保存失败链路未执行。

后端标准 `uv run --group test pytest test/unit -m "not slow"` 因容器 editable 包权限失败；使用 `--no-sync` 复用现有环境执行时，2154 项通过、55 项跳过、1 项完整模式导入超时。该超时项单独复核通过（27.53 秒）。前端全量单测汇总 343 项通过、1 项失败：`tasker_polling.test.js` 完成断言后没有退出，运行超过六分钟后终止该测试进程。新增导航路由测试单独执行通过，新增测试 lint 通过。不能把全量结果声明为全部通过。

## 后果

技能与 MCP 使用独立模块的详情与返回入口，旧详情 URL alias 和旧标签转向保留已有消费者。定时任务仍复用原组件的离开保存守卫。此次变更不修改后端授权、调度逻辑和数据。
