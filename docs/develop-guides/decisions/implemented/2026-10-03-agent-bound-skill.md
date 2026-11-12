# Agent 专属 Skill、历史快照与统一编辑

状态：implemented
类型：feature
Owner：backend/yuxi/modules/extensions/skills/bound.py

## 问题

系统提示词定义角色、回答要求和简短规则，操作流程、脚本和参考资料需要可编辑的完整文件包。Agent 管理者还需要发版留存和恢复历史，权限、执行加载与删除必须遵循同一个 Agent 的生命周期。旧版 PR #1064 的功能需要适配 v1 的事务、文件修订与运行准备边界。

## 决策

Agent 按需绑定一个专属 Skill，系统提示词继续生效。Agent 始终读取固定 slug 的当前内容，专属根文件在 Run 准备时自动预加载；发版只记录当前已保存的完整包，历史恢复覆盖当前内容，不改变绑定 slug。普通 Skill 复用文件编辑与内容提交，不提供历史版本。

### 实现方案

`Skill.bound_agent_id` 的 nullable 外键与唯一约束拥有稳定绑定，Agent 删除级联删除绑定与历史行。新绑定使用 `self-UUID8`，已有绑定保留数据库 slug；合法独立模板在提交时重写为绑定 slug。业务 Schema 使用版本 4 的全新部署基线，启动拒绝旧 Schema，不添加旧库兼容路径。

专属内容的权限和所有权从 Agent 派生，不保存独立共享范围，也不提供独立启停、删除或转移。普通 Skill 列表、选择项、mention 和依赖候选隐藏绑定资源，直接文件接口仍执行授权。创建、上传、内容修改与历史操作遵循 Agent → Skill 锁序，写入前刷新有效操作者。单 Skill ZIP 压缩体限制为 10 MiB，展开限制为 50 MiB、2000 个条目；路径边界拒绝穿越、链接、特殊文件和无效根说明。绑定依赖沿用普通 Skill、工具和 MCP 解析，依赖权限须覆盖 Agent 的范围；其他 Skill 不能依赖专属包。

Agent definitions service 协调 Agent 与绑定的授权变化和删除：repository 在共同事务内 flush，service 拥有最终提交。权限扩大先校验绑定依赖，撤权在提交前撤下旧用户投影，提交后按最新范围重建。删除沿用活跃执行拒绝，提交后只清理本次定位的包目录。内容与清理的持久化边界由[完整内容提交决定](./2026-10-05-skill-content-commit.md)拥有。

### 历史与编辑

`SkillVersion` 保存所属 Skill、版本标识、不可变目录引用、展示元数据、哈希及创建身份。版本标识为北京时间年月日加 UUID8，数据库唯一约束拒绝碰撞，不维护递增计数。历史包含根说明、依赖、辅助文件、空目录和执行位，不进入普通发现与运行投影，不提供历史内容编辑。

恢复确认覆盖当前内容，后端核对整包修订、历史字节和当前依赖权限；不自动创建备份。删除历史只清理无当前或历史引用的目录，删除当前使用的历史仍保留当前包。

专属 ZIP 仅用于首次导入。创建发布用例在 Agent 锁内拒绝已有绑定，返回 409，移除上传接口的 `expected_revision` 参数。已有内容通过统一编辑入口保存，避免整包替换意外移除辅助文件。替代方案是继续保留修订保护的覆盖上传，但它重复内容编辑能力并扩大误删范围，因此不采用。

Agent 入口提供不可编辑的 SKILL.md 预览，编辑跳转统一详情页。SkillDetailView 管理一份完整草稿，文件与依赖区域各自提供同一保存操作；历史页提供发版、恢复和删除。未保存草稿阻止发版和恢复，恢复请求及重载期间阻止新输入和离开。Agent 存在未保存配置或进行上传时拒绝跳转。专属包创建或首次导入的忙碌状态传递到父弹窗，直到内容重新读取结束，期间关闭、保存、打开其他 Agent、分组切换与路由离开均被阻止。前端身份统一使用字符串 agent_id，数字 id 仅作数据库身份；默认头像本地生成，上传图标优先。创建时附带专属包与 Context 的流程见[统一创建与配置决定](./2026-10-05-agent-create-context.md)。

### 运行边界

worker 从已授权 Run 的 Agent slug 派生绑定，客户端不能覆盖。Context、根内容、metadata 和 manifest 来自同一次准备；同 Run 保留已预加载根文本，中途新建绑定在下一 Run 加载。脚本和参考文件仍读取实时用户投影，不增加整包 Run 固定版本机制。重试重新准备沿用 fingerprint 冲突拒绝，后续 Run 包括 steer 和等待恢复读取当前内容。

APP 终端用户读取绑定复用 Key 所属账号的显式 Agent 范围，并限定为读取；普通依赖仍按实际终端用户授权解析。投影按 uid 隔离，不提供同一用户不同 Agent 之间的文件保密边界；自动激活仅针对当前 Agent 的绑定。

## 替代方案

- 仅使用系统提示词：适合简短规则，不能承载完整操作包与历史。
- 普通 Skill 加命名约定或另建专属编辑器：前者缺少绑定约束，后者重复文件、授权与修订机制。
- 每个版本换 slug 或冻结整包执行：改变现有运行准备和投影，采用固定当前绑定。
- 递增编号、手工版本名或恢复前自动备份：增加未请求的计数、表单或隐式版本。
- 自动保存并发版或全局重复保存入口：增加时序与失败语义，完整草稿在所属区域显式保存。

## 后果

管理者可以直接预览和维护指南，版本恢复与删除保持当前绑定。根说明消耗模型上下文，按需创建与短根控制成本。历史存储随完整包数量增长，管理员可以删除快照；依赖失效时恢复或扩大共享失败，当前内容保持不变。数据库引用与实际包损坏需要明确修复，不承诺自动恢复。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 绑定唯一、隐藏发现、继承授权及级联生命周期 | HTTP / PostgreSQL / 文件 | bound / Skill repository / Agent definitions | agent bound skill integration | 非所有者、依赖范围不足、修订冲突、提交失败 | Passed |
| 当前内容与历史完整包、恢复和引用清理 | HTTP / PostgreSQL / 文件 | versions / content | agent bound skill versions integration | 普通 Skill 历史、损坏历史、UUID 碰撞、并发清理 | Passed |
| 专属根自动加载且同 Run 保留初始文本 | worker / 模型协议 / manifest | Agent preparation / Skill runtime | bound skill deterministic worker E2E；replay 负控 unit | skills/preload 为空仍加载；运行中编辑不替换已加载根 | Passed |
| 统一预览、编辑草稿及前端身份 | Vue / DOM | Agent store / SkillDetailView / VersionPanel | 前端 unit 与实际浏览器 | 409 保留、脏草稿禁止恢复和离开、数据库 id 不用于路由 | Passed |

准确命令与本次提交的测试、独立 review 和 CI 结果记录在 PR。商业模型、第三方 MCP 连通性和生产备份恢复为 Not run；运行链路使用独立 deterministic replay 与本地 FastMCP。界面证据保存在仓库外，不将截图纳入提交。

首次 ZIP 导入的相关验证（4 项通过）：真实 HTTP 并发导入只接受一份包，已有绑定即使提交正确修订也返回 409，数据库修订与全部文件字节保持不变；原包替换测试转为通过统一内容编辑入口验证提交失败回滚、提交后清理失败及连续保存。运行入口为 `uv run --no-sync pytest test/integration/api/test_agent_bound_skill.py::test_bound_zip_import_is_create_only test/integration/api/test_agent_bound_skill.py::test_bound_skill_creation_upload_revision_visibility_and_delete test/integration/api/test_agent_bound_skill.py::test_package_compensation_respects_commit_point test/integration/api/test_agent_bound_skill_versions.py::test_committed_response_survives_following_save_prune -q -p no:cacheprovider`。
