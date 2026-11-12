# Sandbox 删除收进与写入同界，并钉住异步文件工具的原生委托

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/runtime/sandbox/backend.py

## 问题

`UserSandboxBackend` 已把 `aread`/`awrite`/`aedit`/`als`/`aglob`/`agrep`/`aupload_files`/`adownload_files` 全部委托给同名同步实现（`await_io(asyncio.to_thread(...))`），同步 `write` 按工具契约覆盖、`edit` 以原始字节匹配——旧 provisioner 后端的异步遮蔽问题在新后端已不存在。但 `delete` 未覆盖，解析到 `BaseSandbox.delete` 的沙箱 `rm -rf`：共享 Skill 投影是只读挂载（`_check_writable` 拒绝写入），删除却绕过同一判定；`adelete` 由协议默认转发到同一实现。模型工具 allowlist 仍显式排除 delete，但授权应在产生副作用的执行器处 fail-closed，而不是只靠暴露面。

## 决策

### 实现方案

`UserSandboxBackend.delete(file_path)` 覆盖为与写/编辑同一口径：`normalize_runtime_path` 失败返回稳定错误；`_check_writable` 拒绝共享 Skill 投影等只读路径；通过后才交给 `super().delete`（保留上游在用户 VM 内 `rm` 的语义与存在性行为）。`adelete` 依赖协议默认 `to_thread(self.delete)` 转发，不单独覆盖；`_AGENT_FS_TOOLS` 继续排除 delete。同时补两类回归测试钉住现状：异步文件工具必须委托本类同步实现（逐个 spy 断言调用），以及 allowlist 不暴露 delete。

## 替代方案

- 只靠工具 allowlist：拒绝。前端/prompt/schema 省略不是授权边界；middleware 内部路径与未来调用方直接落在 backend 上。
- 自研 routed delete（挂载内本地删除 + VM 内脚本）：拒绝。删除与 `execute` 同权（模型本就持有任意命令），本变更闭合的是与写入一致的只读投影判定，不重复实现删除语义。

## 后果

- 对共享 Skill 投影与非法路径的 delete/adelete 从"到达上游 rm"变为稳定错误，无副作用。
- 删除能力仍不对模型暴露；未来开放需单独的审批/审计设计。
- 异步文件工具的原生委托已在上游实现，本变更加了回归钉，不影响既有行为。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 共享投影/非法路径的 delete 在到达上游 rm 前失败 | 只读投影被 `rm -rf` | `UserSandboxBackend.delete` | `test/unit/sandbox/test_user_sandbox_backend.py::test_shared_skills_delete_fails_closed_before_upstream_delete`、`::test_delete_normalizes_path_before_upstream_call` | 移除 delete 覆盖后上游 delete 被调用，用例以 `pytest.fail` 失败（实测） | Passed |
| 异步文件工具委托本类同步实现 | 回落到 BaseSandbox 的沙箱脚本 | `aread`/`awrite`/`aedit`/`als`/`aglob`/`agrep` | `::test_async_file_tools_delegate_to_native_sync_implementations`（逐个 spy 断言调用顺序） | 删除任一异步委托后用例失败（实测删除 awrite 委托） | Passed |
| 回归 | 既有沙盒行为漂移 | `test/unit/sandbox` | `pytest test/unit/sandbox` → 135 passed；`ruff check`/`format` 干净 | 不适用 | Passed |

未验证范围：真实 container/Cube 环境下的删除行为 `Not run`（单元层以宿主文件与上游 delete stub 覆盖判定；镜像内只读挂载的内核语义由 Cube 运行时拥有）。
