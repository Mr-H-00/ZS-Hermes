# 前端服务端错误文案边界

状态：implemented
类型：bug-fix
Owner：frontend/src/apis/base.js

## 问题

API 基础层把 `docker compose logs api` 等内部运维指令拼入 5xx 用户文案。浏览器用户不应看到部署拓扑和容器命令；日志排障信息属于开发/运维观测面，不属于终端错误协议。

## 决策

5xx 只展示通用、可行动但不泄露内部部署信息的文案；保留脱敏请求路径、状态码和服务端日志用于开发者控制台，具体排障仍由 Compose 日志和后端观测 Owner 负责。

## 替代方案

- 原样展示后端/部署命令：诊断方便但泄露内部环境信息，不选择。
- 完全隐藏错误：降低泄露但用户无法理解请求失败，不选择。
- 将内部指令放入结构化错误响应：仍可能被页面直接展示，且扩大公开协议，不选择。

## 后果

终端用户只看到通用 5xx 文案，开发者控制台仍保留脱敏路径和状态码；后端日志和 Compose 运维入口不进入公开错误协议。

## 验证

- `cd frontend && pnpm exec node --test --test-concurrency=1 test/unit/api_boundary.test.js test/unit/frontendAuthBoundary.test.js`：Passed，16/16。
- `cd frontend && pnpm run lint:check`：Passed。
- `cd frontend && pnpm run build`：Passed。

## 验收结果

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 5xx 用户文案不包含部署/容器命令 | 内部拓扑泄露到 UI | `frontend/src/apis/base.js` | API boundary unit | 恢复 `docker compose` 文案，负向断言应失败 | Passed |
| 5xx 仍保留稳定错误状态并抛出异常 | 文案收敛误吞掉错误 | `frontend/src/apis/base.js` | API boundary unit | 让 500 返回成功或不抛错，测试应失败 | Passed |
