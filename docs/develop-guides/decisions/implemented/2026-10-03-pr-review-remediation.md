# PR #28/#29 审查收敛与门禁修复

状态：implemented
类型：bug-fix
Owner：packages/yuxi-cli/src/cli.ts

## 问题

两个候选 PR 合并到 `develop/1.0` 时，PR #29 的知识库查询页面存在导入冲突；审查还发现 CLI 对结构化错误和浏览器登录回执的校验不完整，SSE 解析器在流尾部需要补齐解码状态。依赖审计同时暴露锁文件中的已知漏洞版本。

## 决策

保留两个 PR 的 Public v1 CLI 与 Milvus 3 文本检索范围，合并时以当前前端依赖实际拥有的 Lucide 搜索图标解决页面冲突。CLI 在保存登录凭据前拒绝缺失或格式错误的回执，结构化错误在缺少错误码时回退到服务端状态文本。将 PyJWT、pypdf 和 urllib3 锁定到当前无已知漏洞的版本。

## 替代方案

- 放弃冲突文件中的一方：不采用，会丢失现有查询交互或文本命中展示。
- 仅接受 CI 的依赖审计失败：不采用，已知漏洞不应随合并进入发布候选。
- 对异常登录回执静默写入配置：不采用，会留下不可用或不可撤销的凭据。

## 后果

CLI 对异常远程响应更早失败；知识库查询页面继续使用现有前端图标依赖。依赖锁文件包含一次明确的安全补丁升级；未改变公共 API 的功能范围。

## 验证

- `cd packages/yuxi-cli && npm test && npm run pack:check`：9 tests passed，npm dry-run 成功。
- `cd frontend && pnpm exec eslint ... && pnpm build`：通过。
- `cd backend && uv run --group test pytest test/unit -m "not slow" -q`：2500 passed。
- `cd backend && uv audit --locked --no-dev`：无已知漏洞。
- `python3 scripts/verify_engineering_contracts.py` 及相关 workflow policy tests：通过。
