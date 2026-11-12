# beta6 发布验证与测试契约对齐

状态：implemented
类型：testing
Owner：backend/test/integration/services/test_mcp_oauth.py

## 问题

main 的后端 unit、service integration 与 runtime system 检查存在失败。部分测试仍要求知识库顶层异常返回 HTTP 200，或把 MCP 临时发现故障解释为整轮永久不可用；另有测试缺少正式 Context 的 Skill 来源字段和文件执行 Owner，失败发生在目标拒绝边界之前。查询配置 unit 还直接读取 Redis 模型目录，依赖了 unit 环境未装配的服务。

候选提交的供应链审计还发现锁文件中的 pypdf 6.21.2 和 pyasn1 0.6.4 存在已知漏洞，需要升级后才能绑定发布 tag。

## 决策

### 实现方案

测试以现有语义 Owner 为准，不为旧断言修改生产行为：

- 知识库未知顶层异常保持 HTTP 500；真实 HTTP 测试同时检查脱敏 JSON 或框架的通用错误正文，已知领域拒绝继续检查原状态和说明。Dify 协议测试对两种检索入口都验证 HTTP 500 和安全提示，未实现的导出验证现有 HTTP 501 及“知识库导出暂不支持”提示。
- MCP 临时发现失败不写入成功目录或永久不可用缓存，并在相同 Run 的缓存对象上验证下一次发现可以恢复。明确凭据失效继续禁止依赖工具并展示安全提示。真实 worker E2E 区分临时网络失败和未连接、错误凭据。
- OAuth 工具撤权测试使用正式 `ChatBotContext`，提供 Skill 身份快照；文件撤销测试提供成对的 job 与 worker 身份，分别验证清理状态拒绝和作业删除后的失租更新返回空值，并回读文件确认没有写入。
- 查询配置 unit 只替换模型目录缓存边界，执行器构造仍明确失败；不再把 Redis 可用性当作纯配置测试的前提。
- E2E 归档清理按现有会话范围的 409 拒绝提示等待执行与输入收敛，其他拒绝仍立即失败。
- 协作重放协议直接解析完整 JSON 的场景使用独立测试账号，回读数据库确认只有本例的六名成员或两个根会话，并显式设置 4096 token 的工具结果预算，保留成员列表和精确结果断言；默认 300 token 的归档、预览和回读由独立摘要与大结果用例验证。
- 委托令牌的非法时效字段在用例执行时按相对偏移生成，避免测试收集后的等待把未来签发时间变为过去。
- pypdf 的最低版本提升至 6.21.4，pyasn1 的传递依赖约束提升至 0.6.5，锁文件仅升级这两个补丁版本。修复范围由上游 [pypdf 公告](https://github.com/py-pdf/pypdf/security/advisories/GHSA-cggf-277g-3q54)和 [pyasn1 公告](https://github.com/pyasn1/pyasn1/security/advisories/GHSA-9m75-vc3g-4x39)及锁文件审计确认，不添加漏洞忽略项。

现有行为与取舍分别由[中间件修复决定](./2026-10-30-agent-middleware-repair.md)、[知识库用例决定](./2026-10-30-knowledge-usecase-simplification.md)及对应源码拥有。版本同步继续使用 `scripts/bump-version.sh`；先推送提交，确认同一提交的 CI 全部通过后再创建并显式推送 beta tag，不创建 GitHub Release。

## 替代方案

恢复 HTTP 200 的错误包装、永久缓存网络故障，或放宽执行身份校验，都会改变已有公开行为或安全边界，不采用。跳过失败用例、增加无关外部服务来掩盖 unit 依赖，也不采用。

## 后果

生产权限、持久化与异常处理逻辑保持不变。测试仍验证工具撤权后的文件副作用不存在、敏感诊断不会出现在响应和模型协议中，以及撤销失败后的持久文件状态不能绕过。临时 MCP 故障的可恢复性由实际协议服务与相同运行缓存验证。

## 验证

后端完整 unit、相关真实 HTTP/PostgreSQL integration、MCP 故障 worker E2E，以及前端完整 unit、lint/build、文档 build 和工程契约检查共同参与发布验收。原 main 的 Actions 失败记录作为修复前证据；测试期望的变化由独立 Reviewer 对照源码和既有决定审阅。最终发布证据以 tag 指向提交的 GitHub Actions 结果为准。
