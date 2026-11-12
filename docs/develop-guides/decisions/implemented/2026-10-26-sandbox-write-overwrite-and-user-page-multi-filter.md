# 沙盒 write_file 覆盖写入与用户分页多选筛选

状态：implemented
类型：feature
Owner：backend/yuxi/modules/agents/runtime/sandbox/backend.py
辅助 Owner：backend/yuxi/api/routers/identity/auth.py（users/page 多选契约）

## 问题

1. 沙盒 `write_file` 对已存在文件返回 `File '...' already exists`。deepagents SDK 的 `WRITE_FILE_TOOL_DESCRIPTION` 明确承诺 "Creates the file if it does not exist; replaces it entirely if it does"，实现与工具契约不一致：模型按工具说明重写文件时被报错打断，被迫生成 v2 副本、删除原文件等绕路调用，浪费多轮执行。
2. 用户管理页的部门与权限筛选是表头上方单选下拉，无法表达"同时查看多个部门/多个角色"；管理员需要逐次切换筛选。

## 决策

### 实现方案

`UserSandboxBackend.write` 改为 `overwrite=True` 整体写入，与 deepagents 工具描述对齐。覆盖经 `upload_authorized_file_from_stream` 的临时文件 + `os.rename(dir_fd)` 发布：rename 不跟随目标组件的 symlink，父目录逐层 no-follow（ELOOP 拒绝），跨用户越权与共享 Skills 只读拒绝路径不变。VM guest 路径的最终组件 symlink 仍会跟随（`edit()` 既有行为）：VM 内进程即用户本人，权限由内核拥有，该差异记录于此。

`/api/auth/users/page` 在保留 `department_id`/`role` 单值参数的基础上新增 `department_ids`/`roles` 逗号分隔多选参数；`parse_csv_ints`/`parse_csv_roles` 解析并收敛合法取值，空值或全部非法视为未筛选。非超级管理员的可见部门仍强制为本部门，多选只作用于角色。前端用户管理页把顶部"全部部门/全部权限"两个单选下拉移入表头筛选按钮（多选复选弹层），保留搜索与用户类型筛选；APP 终端用户视图隐藏表头筛选并清空已选值。

## 替代方案

- write 增加 override 参数：工具 schema 来自 deepagents SDK，无法不改 SDK 增加；且模型已显式表达整体重写意图，额外参数只会制造绕路循环。
- write 保持报错并把报错文案改为引导：仍是死路，模型没有可靠的"读取后覆盖"通道（read 状态不在 backend 层跟踪）。
- 前端当前页客户端过滤实现多选：用户列表是服务端分页，仅过滤当前页会产生错误总数与缺页。

## 后果

模型可以整体覆盖工作区已有文件，包括用户上传后未经读取的文件；这是现代编码代理 Write 工具的通行语义，风险由审批模式与沙盒隔离兜底。旧调用方依赖"写入已存在文件报错"的隐式行为会失去该信号（仓库内无此依赖，既有测试已显式更新）。

users/page 多选契约由本页前端消费；`department_ids` 的取值是部门 id 列表，前端选项只保留带数字 id 的部门。VM guest 覆盖写入跟随最终组件 symlink 的差异未来若需要对齐，需改 `_GUEST_FILE_SCRIPT` 并补对应 guest 侧负向测试。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| write_file 覆盖已存在文件且不穿过链接写目标 | rename 改为原地 truncate 或跟随链接 | `filesystem.py` rename 发布、`test_user_sandbox_backend.py` | `pytest test/unit/sandbox/test_user_sandbox_backend.py` | `test_workspace_write_overwrites_existing_file_without_following_links`（alias 链接被替换、target 字节不变） | Passed |
| symlink 逃逸仍被拒绝 | 父目录 no-follow 失效 | `open_directory_fd` ELOOP、既有逃逸测试 | 同上套件 `test_symlink_cannot_escape_to_other_user_and_never_falls_back` | 写入 `workspace/escape/...` 必须报错 | Passed |
| users/page 多选过滤按角色与部门生效 | 单值相等过滤回归或权限放大 | auth 路由、administration service、UserRepository | 真实 HTTP 集成 `test_auth_router.py::test_admin_user_page_accepts_multi_select_role_and_department_filters` | 部门管理员视角部门始终本部门、多选角色生效 | Passed |
| CSV 参数容错不产生 500 | 全角数字/非法段抛 ValueError | `parse_csv_ints` try/except | 路由单测覆盖由集成参数化补充 | `department_ids=²` 返回 200 空筛选 | Passed（人工核验） |
| 前端筛选状态与请求契约一致 | 旧单值参数残留在请求 | `UserManagementComponent`、`auth_api.js` | `pnpm run test:unit`（userManagement 用例） | 未选值不下发 `department_ids/roles`；end_user 视图清空筛选 | Passed |
