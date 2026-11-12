# 智能体管理列表按创建时间排序

状态：implemented
类型：feature
Owner：frontend/src/modules/agents/ui/AgentManagePanel.vue

## 问题

管理页按内置标记和名称排序，新建智能体的位置难以预测。

## 决策

### 实现方案

管理页消费 API 返回的 `created_at`，搜索过滤后按创建时间正序排列；同一时间按数字 `id` 正序保持稳定。排序复制列表，不修改接口数据。范围仅为管理页，沿用已有搜索和加载流程。

## 替代方案

保留内置置顶与名称排序不能保证创建先后顺序；更改共享 Store 或后端排序会影响聊天选择入口。采用管理页局部排序，不新增排序控件。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 最早创建排在前面，新建在后，搜索保持顺序 | 内置优先或名称顺序覆盖时间 | AgentManagePanel | frontend unit 与真实页面 DOM | 旧内置与较新自建角色混排，名称顺序与时间相反 | Passed |

## 后果

管理页依赖接口提供创建时间与数字 ID；不涉及持久化或权限变化。

验证结果：

- `docker compose exec frontend node --test test/unit/agentManageOrder.test.js`：通过，覆盖创建时间、时区、同时间 ID、搜索、改名和输入列表不变。
- `docker compose exec frontend pnpm run test:unit`：510 passed；最终正序调整后单独重跑排序测试通过。
- `docker compose exec frontend pnpm run lint:check` 和 `docker compose exec frontend pnpm run build`：通过。
- 真实浏览器：回读 API 的创建时间并逐项检查管理页 DOM 顺序，验证搜索保持顺序和无匹配空状态，保存截图。布局与主题样式没有变化。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts` 与 `git diff --check`：通过。

preset 首次初始化以默认助手优先、其余按模块文件名顺序执行，具体由[内置内容发现](2026-09-17-builtin-discovery.md)拥有；本决定只定义管理页展示顺序。
