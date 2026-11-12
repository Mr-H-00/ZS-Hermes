# 来源标注的 Web 内部边界

状态：implemented
类型：architecture
Owner：backend/yuxi/api/routers/agents/references.py

## 问题

回答来源标注属于 Web 阅读功能，入口与持久投影需要明确产品用户边界。通用登录依赖接受 full API Key，不能单独证明该边界。来源归一化与标注服务是同一用例，分成两个文件增加了不必要的阅读跳转。

## 决策

### 实现方案

GET/POST 来源接口位于 `/api/agent/threads/{thread_id}/turns/{turn_id}/references`，由 Web 登录 JWT 认证并固定产品用户作用域。API Key 包括 full Key 均被拒绝。Public 路径不提供来源标注，Public Turn metadata 不包含引用；前端从内部 GET 恢复持久结果。来源提取、模型匹配和校验集中在 `services/references.py`，持久查询继续由 repository 拥有。引用数据与交互见 [Turn 按需标注来源](2026-10-09-turn-reference-annotations.md)。

## 替代方案

- 只把路由移出 Public 文件：通用登录依赖仍允许 full Key，无法闭合外部调用边界。
- 保留 Public metadata 供前端恢复：外部消费者仍可读取 Web 标注，前端已有内部 GET consumer，无需额外投影。

## 后果

Web 使用现有持久引用和交互，外部协议不声明来源标注能力。接口位置和认证边界改变，模型、证据验证、锁与权限规则保持不变；不提供旧地址兼容路径。

## 验证

以下为来源任务在其基线上的原始验收；合入当前 main 的验证由[Turn 来源标注记录](2026-10-09-turn-reference-annotations.md#验证)拥有。

- `docker compose exec api uv run --group test pytest test/integration/api/test_turn_references.py -m "not slow" -q`：8 passed、1 deselected。真实 HTTP、PostgreSQL 和 wire 重放验证内部标注与回读；旧 Public GET/POST 404、未登录 401、agents/full Key 403、其他用户和 APP Thread 404；保存后 Public Turn 无 references，OpenAPI 无来源路径。full Key 的拒绝原因断言单独回归：1 passed、8 deselected。
- `docker compose exec api uv run --group test pytest test/unit/services/test_turn_references.py -q`：20 passed；完整后端 unit：2576 passed、55 skipped、7 subtests passed。合并服务后的来源归一化与引用校验通过。
- `docker compose exec frontend node --test test/unit/turnReferences.test.js`：3 passed；前端 lint/build 通过。
- 真实浏览器执行 `frontend/test/browser/turnReferences.js`：标注与刷新只请求内部路径；持久引用恢复、知识库第 8–12 行定位、网页打开、隐藏/显示、浅深色与窄屏通过。CLI 会话中断后由相同 Playwright 脚本直接运行并读取 DOM 结果。
- 工程检查与文档构建结果见 [Turn 按需标注来源的验证](2026-10-09-turn-reference-annotations.md#验证)。

真实模型 slow 探针未在接口边界调整中重跑，完整 Agent worker 检索到标注 E2E 未验证；测试使用独立 Compose 数据与端口。
