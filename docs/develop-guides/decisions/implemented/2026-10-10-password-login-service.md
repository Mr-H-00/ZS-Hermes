# 密码登录用例归属身份服务

状态：implemented
类型：architecture
Owner：backend/yuxi/modules/identity/services/password_login.py

## 问题

密码登录路由直接编排限速、用户状态、验密、失败计数和提交；同域其他登录用例已经由 service 拥有，贡献者需要按认证方式寻找业务状态转换。

## 决策

### 实现方案

身份服务拥有密码登录的限速检查、用户查询、锁定、验密、持久化、Token 签发与部门查询。服务返回登录结果或具名拒绝原因；HTTP 路由保留表单、客户端 IP、状态码、header 和响应装配。密码错误计数先提交再报告失败，成功状态先提交再返回；既有锁定次数、Token 与响应契约保持不变。

## 替代方案

仅移动文件而继续由路由编排不能闭合用例 Owner。通用认证框架会扩大此次范围，当前没有必要。

## 后果

调用方通过身份服务执行完整密码登录用例，协议拒绝原因在入口适配。不更改既有并发失败计数或 Redis 限速算法。

## 验证

真实 TCP HTTP 和独立 PostgreSQL schema 测试覆盖失败计数提交、第五次失败锁定、正确密码不能绕过锁定、成功重置与 Token 身份、过期锁定、未知/end_user/删除账号、限速 header 和 commit 失败不发布 Token。独立会话回读持久事实；限速结果固定以隔离共享 Redis，既有 limiter unit 验证其算法。CI system-tests 执行本用例集成。
