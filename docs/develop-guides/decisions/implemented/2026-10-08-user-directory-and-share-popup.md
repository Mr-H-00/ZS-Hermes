# 用户目录默认范围与共享选人浮层

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/identity/repositories/users.py

## 问题

通用用户列表与共享候选列表混入 APP 终端用户；用户管理分页已有类型筛选，但其他目录查询未应用系统用户范围。创建智能体时，选人浮层挂在共享面板的滚动容器内，会撑大滚动范围并被裁切。

## 决策

用户目录与目录计数默认只读有效 human 用户。用户管理分页保留显式 end_user、all 筛选。按指定身份读取、唯一性检查和 Public API 身份解析保留完整身份空间。

### 实现方案

UserRepository 在列表分页前应用 user_kind 过滤，旧用户列表与共享候选 HTTP 入口继承此范围。创建账号的用户名冲突检查使用精确查询，避免类型过滤或分页遗漏占用名称。AgentEditModal 将嵌套选人浮层挂到外层 popover 表面，脱离共享内容的滚动区；选人列表保留自己的滚动。

## 替代方案

前端过滤不能保证其他消费者或分页正确。全局过滤所有 User 查询会破坏终端用户鉴权和唯一性检查。移除共享面板的高度限制会使小屏长内容越界。

## 后果

系统用户列表与共享候选遵循相同类型范围，用户管理分页的显式查询及部门可见范围保持有效。UID 占用检查、指定身份读取和 Public API 仍覆盖终端用户；无 Schema 或数据迁移。共享面板保留高度限制，选人浮层不参与其滚动尺寸。

## 验证

- Repository 负向用例在恢复无类型过滤时失败；修复后验证目录分页、计数、带 endusr_ 前缀的系统用户，以及终端用户的精确身份和唯一性查询。
- 真实 HTTP/PostgreSQL 用例从 Public API 创建终端用户，再遍历普通用户列表与共享候选，对照数据库中的有效 human 集合；显式 end_user/all 分页仍可读取来源，终端用户占用名称仍返回冲突。相关测试 4 项通过。
- 浏览器读取共享面板 clientHeight/scrollHeight：旧挂载点从 234/234 变为 234/317，新挂载点保持 234/234。375px 视口打开浮层后在屏幕内，选人交互保持父面板打开。
- 后端 unit 使用 `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow" -q -o cache_dir=/tmp/yuxi-share-pytest`：2518 通过、55 跳过。标准 uv run 同步因容器无法更新时间戳 yuxi.egg-info 失败，复用已安装依赖执行；未将跳过项计为通过。
- 前端 lint、492 项 unit、build、文档 build、工程契约及其 64 项测试通过；受影响 Python 文件 Ruff 检查通过。
