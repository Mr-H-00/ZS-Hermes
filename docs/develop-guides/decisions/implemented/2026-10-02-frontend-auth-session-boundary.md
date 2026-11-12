# 前端认证会话边界收敛

状态：implemented
类型：bug-fix
Owner：frontend/src/modules/identity/model/user.js

## 问题

前端超级管理员 API 包装器调用的权限检查只返回布尔值却没有检查结果，普通登录用户可以进入请求发送阶段，客户端守卫因此不是 fail-closed。后端仍是最终授权边界，但前端 API 契约与其它管理员包装器不一致。

OIDC 回调和超管模拟登录还绕过用户 Store，直接写入 `user_token`。这使会话状态更新和持久化写入存在多个 Owner，后续容易出现 Pinia 状态、项目缓存和持久 token 不一致。

## 决策

由 identity Store 的 `applySession` 统一更新会话响应式状态并写入持久 token；登录、OIDC 回调和模拟登录都调用该动作。API 的认证和客户端权限断言由无业务依赖的 `apis/auth_context.js` 承担，identity Store 注入 token、角色判断和失效回调；`checkSuperAdminPermission` 保持布尔谓词语义，供已有界面早退逻辑使用。

### 实现方案

会话入口仍由各 API/页面拥有，但所有成功的会话载荷经过 `useUserStore().applySession(data)`；该动作负责清理项目上下文、更新 Store 状态并写入 `user_token`。超级管理员 API 包装器通过 `assertApiSuperAdminPermission()` 读取 identity 注入的角色上下文，失败即抛出“需要超级管理员权限”，不触发 `fetch`。

## 替代方案

- 让每个页面继续直接写 token：改动最小，但会继续保留多个会话 Owner，不选择。
- 让 `checkSuperAdminPermission` 改为抛异常：可以统一 API 与页面调用契约，但会改变 Debug 页面现有的布尔早退路径，且错误处理范围更大，暂不选择。
- 只依赖后端拒绝：安全边界仍成立，但客户端会继续发送无效请求并保留错误的 API 包装器契约，不选择。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 非超级管理员调用 `apiSuperAdmin*` 在客户端同步失败 | 包装器忽略布尔权限结果并发出请求 | `frontend/src/apis/base.js` | `frontend/test/unit/frontendAuthBoundary.test.js` | 普通用户调用 `apiSuperAdminGet`，断言抛出权限错误且 `fetch` 次数为 0 | Passed |
| 成功会话写入只有 identity Store 是持久 token Owner | OIDC 或模拟登录重新直接写 `user_token` | `frontend/src/modules/identity/model/user.js` | 前端认证边界单测的源码检查 + 相关单测 | 在 OIDC/Debug 文件中恢复直接写入，测试应失败 | Passed |
| 后端授权语义不被前端修复冒充 | 客户端 guard 被误当成安全边界 | 后端认证依赖与 API service | 本补丁不改变后端；后端授权回归不在本次前端最小验证范围 | 直接请求后端仍由后端权限检查拒绝 | Inspected |

## 风险

`applySession` 会继续沿用现有登录路径的项目缓存清理行为；OIDC 和模拟登录现在也会一致执行该清理。API 包装器新增的是客户端早退，不改变后端返回或权限模型。API 与 identity 的模块环由后续的认证上下文决策收敛，认证上下文不拥有持久会话事实。

## 后果

认证成功后的响应式状态、项目缓存清理和持久 token 现在由 identity Store 的同一个动作拥有；OIDC 和模拟登录与普通登录保持一致。超级管理员 API 在浏览器端提前失败，减少无效请求，但后端仍是最终授权边界。`apis ↔ identity` 的模块环和 OIDC/登录页两个 redirect key 尚未在本决策中解决。

- `cd frontend && pnpm exec node --test --test-concurrency=1 test/unit/frontendAuthBoundary.test.js test/unit/api_boundary.test.js`：Passed，14/14。
- `cd frontend && pnpm run lint:check`：Not run，当前工作树缺少已声明的 `@typescript-eslint/parser` 安装，ESLint 在加载配置阶段退出；未将此结果写成代码通过。
- 浏览器视觉验证、完整 frontend unit、build 和后端权限链路：Not run，本补丁不宣称这些范围已通过。

旧能力不存在：OIDC 回调和超管模拟登录中的直接 `user_token` 写入已删除；API 超级管理员包装器不再忽略权限谓词结果。

重新引入条件：只有 identity Store 的认证会话 Owner 发生明确决策变更，或 API 基础层改为新的认证上下文注入机制时，才允许增加其他 token 写入入口，并须同步更新负向测试。
