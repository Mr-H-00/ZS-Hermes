# 前端 API 认证上下文解环

状态：implemented
类型：architecture
Owner：frontend/src/apis/auth_context.js

## 问题

`frontend/src/apis` 的基础请求层和部分 API 模块反向导入 identity Store，而 identity Store 又通过认证 API 进入 `apis`，形成 `apis ↔ identity` import 环。这个环让 API 基础层不能独立加载和测试，也把 token、登出和客户端权限判断耦合到 HTTP 实现内部。

Thread SSE 还绕过基础请求层，直接从 identity Store 取得 Authorization header，导致普通 HTTP 与 SSE 各有一套认证入口。

## 决策

`apis/auth_context.js` 拥有无业务依赖的认证上下文协议。API 层只读取该上下文提供的 token、权限谓词和认证失效回调；identity Store 在自身装配时注入这些回调。API 层不再导入 identity，identity 仍拥有会话状态、token 持久化和登出清理。

### 实现方案

认证上下文提供：

- `getApiToken()`：读取当前 Authorization token；
- `getApiAuthHeaders()`：为普通 HTTP 和 Thread SSE 生成一致的认证头；
- `assertApiAdminPermission()` / `assertApiSuperAdminPermission()`：只执行客户端体验级权限早退；
- `handleApiUnauthorized()`：将 401 交还给 identity 的登出 Owner；未装配应用时保留安全的本地 token 清理降级。

identity Store 通过 `configureApiAuth()` 注入自身的 token、角色判断和 logout 回调。`base.js` 和 `agent_api.js` 只依赖 `auth_context.js`，后端仍是最终授权边界。

## 替代方案

- 让 `base.js` 直接读取 `localStorage`：可以消除部分 import 环，但 HTTP 层会重新拥有 token 持久化事实，且无法可靠同步 Pinia 登出状态，不选择。
- 保留 API 对 identity 的直接依赖，只依靠函数体延迟调用：不能消除模块环，也无法统一 SSE 和 HTTP 认证入口，不选择。
- 在每个 API 调用方显式传 token：调用面会扩散认证细节，容易遗漏 SSE、重试和失效清理，不选择。

## 验收结果

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| `src/apis` 不再静态导入 identity | API 基础层重新形成反向环 | `frontend/src/apis/auth_context.js` 与 API 模块 | API 源码边界测试、完整 unit | 恢复任一 `src/apis → modules/identity` import，测试应失败 | Passed |
| HTTP 与 Thread SSE 使用同一认证 token provider | SSE 继续绕过 API 认证上下文 | `frontend/src/apis/base.js`、`agent_api.js` | 认证上下文 unit + API 边界测试 | 改回 `useUserStore().getAuthHeaders()`，边界测试应失败 | Passed |
| 401 仍清理 identity 会话并跳转登录 | 解环后认证失效只清 localStorage，Pinia 仍显示登录 | `frontend/src/modules/identity/model/user.js` | 既有 API 401 测试与认证边界测试 | 让失效回调不调用 logout，现有 401 状态断言应失败 | Passed |
| 客户端 admin guard 不替代后端授权 | 注入的角色谓词被误当最终安全边界 | 后端认证依赖与 API service | 本变更不改变后端；真实 API/E2E 负责最终授权 | 直接请求后端仍必须由后端拒绝 | Inspected |

## 后果

API 基础层、普通 API 模块和 Thread SSE 不再直接依赖 identity；identity Store 仍拥有 token 持久化、响应式会话和登出清理。认证上下文增加了一个有限的进程内 provider，但没有引入跨标签页 broker、业务缓存或新的公开 API。

## 风险

认证上下文是一个小型进程内 provider，不承担跨标签页状态同步，也不保存业务状态。应用未完成 identity 装配时的 fallback 只清理本地 token，不伪装成已登出 UI；正式应用路径必须由 identity 注入 logout 回调。客户端权限早退继续只是体验优化，后端授权不变。

## 验证

- `cd frontend && pnpm exec node --test --test-concurrency=1 test/unit/frontendAuthBoundary.test.js test/unit/api_boundary.test.js test/unit/publicAgentApi.test.js`：Passed，16/16。
- `python3 scripts/verify_engineering_contracts.py`：待本轮全部变更完成后复跑。
- 完整 frontend lint/build、真实浏览器 SSE 和后端授权链路：Not run。
