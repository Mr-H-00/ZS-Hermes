# OIDC 登录身份事务

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/identity/services/oidc.py

## 问题

OIDC 自动开户先提交部门，用户 repository 又使用独立事务，绑定占位行也独立提交。后续失败无法回滚同一次登录创建的身份事实。

## 决策

### 实现方案

identity service 拥有登录用例的唯一提交点；部门、用户和绑定辅助函数使用传入的 session，只 flush。唯一约束冲突使用 savepoint 恢复并读取获胜记录，不回滚外层事务。绑定冲突只有已存在同一目标的绑定才能视为成功。响应数据完成装配后统一提交，失败统一回滚；HTTP 层在成功提交后才发布一次性登录 code。账号绑定与权限规则、OIDC 协议、Schema 和登录响应保持既有语义。

## 替代方案

只移动文件不能收敛事务。补偿删除已经提交的部门会与并发开户竞争。采用同一事务并复用数据库唯一约束。

## 后果

既有 helper 的直接消费者负责提交，API Key 注销恢复测试显式提交其准备事务。开户、绑定、恢复及登录时间更新均由登录 service 提交。外部 provider 请求发生在身份事务之前。

## 验证

- 修复前使用隔离 PostgreSQL schema 执行 `test_failed_login_rolls_back_department_user_and_binding`，标准与 raw username 两种模式均因独立回读仍存在部门而失败（2 failed）。
- `test/integration/services/test_oidc_login_transaction.py` 的 8 项测试通过：响应装配失败无身份残留、并发开户唯一完整身份、同 sub 不同 raw username 竞争只允许匹配账号获胜、绑定冲突回滚，以及真实 TCP HTTP 回调只在提交后发布可一次消费的 code。HTTP 测试使用生产认证路由、真实 PostgreSQL和独立事务回读；provider 身份输入和令牌签发结果固定，不调用外部 SaaS 或验证 JWT 密码学实现。
- 同时运行 `test/unit/services/test_oidc_service.py` 的 6 项测试通过，保留既有绑定和终端用户拒绝规则。
- 新集成测试接入 `system-tests.yml` 独立 step，空认证环境仅避免顶层 fixture 清理共享 API 数据；测试自己创建并删除隔离 schema，无 skip。

外部 OIDC provider、部署级负载均衡和浏览器登录未验证；本变更不调整进程内 state/code 存储。

- 完整后端 unit：2622 passed、55 skipped；skip 包含镜像未挂载完整仓库根目录的 Compose 配置检查，不计作产品通过。工程契约、64 项 verifier unit、Ruff 与 docs build 通过。
