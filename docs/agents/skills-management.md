# Skills 管理系统

Skill 将使用说明、提示词、工具依赖和领域资料组织为可复用目录。Agent 读取 `SKILL.md` 后按声明激活对应能力。

## 为什么需要 Skills

重复使用的 API 调用流程、外部服务操作和领域提示可以封装为独立 Skill。每个 Skill 保存说明文件、资源和依赖元数据，Agent 配置决定当前运行可见的 Skill 集合。

## 架构设计

Skills 系统分为平台共享与个人工作区两层。共享 Skill 采用「文件系统存内容，数据库存索引」；个人 Skill 只存在于当前用户 workspace，并使用 Redis 保存 5 分钟的元数据快照：

```
┌─────────────────────────────────────────────────────────────┐
│                      Skills 存储架构                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   /app/saves/skills/          数据库索引                    │
│   ├── skill-a/               ┌──────────────┐              │
│   │   ├── SKILL.md           │ skills 表    │              │
│   │   ├── tools/             │ - slug       │              │
│   │   └── prompts/           │ - name       │              │
│   └── skill-b/               │ - description│              │
│       ├── SKILL.md           │ - dir_path   │              │
│       └── ...                │ - source_type│              │
│                              │ - share_config              │
│                              │ - enabled     │              │
│                              │ - deps...     │              │
│                              └──────────────┘              │
│                                                             │
│   workspace/agents/skills/    Redis 临时索引                │
│   └── my-skill/              - 按 uid 隔离                 │
│       └── SKILL.md           - 5 分钟失效                  │
│                              - 安装/删除/刷新后立即更新     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 存储结构

- **文件系统**：`/app/saves/skills` 目录下，每个 Skill 占用一个子目录
- **数据库索引**：`skills` 表存储元数据（slug、name、description、来源、共享范围、启用状态、依赖关系等）
- **关联机制**：通过 `dir_path` 字段关联文件系统目录与数据库记录
- **个人工作区**：`workspace/agents/skills/<slug>` 保存当前用户个人 Skill，不创建数据库记录
- **个人缓存**：解析后的名称、slug 和描述按用户缓存到 Redis，默认 5 分钟失效

::: tip 两种存储边界
共享 Skill 必须通过系统导入并写入数据库；个人 Skill 可以由安装流程写入工作区，也可以在工作区中手动维护。手动修改后点击 Skills 页刷新，或等待最多 5 分钟重新解析。
:::

## 创建方式

系统提供以下方式创建或安装 Skills：

1. **推荐 Skill 安装**：在 Skills 管理页的推荐分组点击 `+`，系统会拉取对应远程来源并生成安装草稿
2. **ZIP / SKILL.md 上传**：上传后先解析为安装草稿，再选择安装到个人工作区或共享 Skill 库
3. **远程仓库安装**：填写 skills 仓库地址、ModelScope Skill 地址或合集地址，下载并解析后选择安装位置
4. **在线编辑**：对已有且可管理的 Skill 在线创建目录、编辑文件和维护依赖
5. **Agent 内安装**：主智能体可通过 `install_skill` 工具安装个人工作区 Skill；子智能体禁用该工具

个人 Skill 不解析 `tool_dependencies`、`mcp_dependencies` 或 `skill_dependencies`。需要平台依赖、共享范围或在线管理时，应选择共享安装。

## Skills 来源

Skill 包含提示词、工具依赖和元数据。以下项目可用于参考目录组织与提示词设计：

- **Anthropic 官方 Tools**：https://github.com/anthropics/skills 可以参考其 skills 的组织方式和提示词设计
- **ModelScope Skill 市场**：https://modelscope.cn/skills 支持单个 Skill 地址，也支持合集地址批量拉取
- **MiniMax-AI CLI**：https://github.com/MiniMax-AI/cli 文本、图片、视频、语音和音乐生成 + Web 搜索（可通过 `MiniMax-AI/cli` 远程安装）
- **社区 Skills**：各平台分享的 Agent 提示词模板
- **自定义开发**：根据业务需求自行开发

系统也会在启动时同步仓库内置 Skills。内置 `html-preview` 指导 Agent 在 Markdown 难以清晰表达指标、对比、流程、时间线或层级关系时，按需输出 `html:preview` 静态 HTML/CSS 围栏；普通 HTML 源码仍使用 `html` 代码块。该 Skill 不依赖额外工具，前端通过清洗后的 sandboxed iframe 渲染预览。

未显式配置 Skills 的 Agent 会按现有资源默认规则自动获得该 Skill。使用显式 Skills 允许列表的 Agent 需要选择 `html-preview` 才能使用；内置 `deep-research` 已声明该依赖，升级后仍可继续输出辅助可视化。

## 快速开始

### 创建你的第一个 Skill

一个标准的 Skill 目录结构如下：

```
my-awesome-skill/
├── SKILL.md              # 必选，Skill 的核心定义文件
├── tools/                # 可选，相关的工具脚本
│   └── helper.py
└── prompts/              # 可选，提示词模板
    └── system.md
```

其中 `SKILL.md` 是每个 Skill 必须包含的核心文件，它采用 Markdown + Frontmatter 格式：

```markdown
---
name: My Awesome Skill
slug: my-awesome-skill
description: 这是一个用于处理特定任务的技能
---

# Skill 使用说明

这里是技能的详细使用文档，Agent 会读取这部分内容来了解如何使用这个技能。

## 功能列表

1. 功能一：xxx
2. 功能二：yyy

## 使用示例

当用户 xxx 时，可以调用此技能...
```

**Frontmatter 字段说明：**

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | Skill 展示名称，可使用更易读的名称（如 `Word / DOCX`） |
| `slug` | 否 | Skill 唯一标识，必须是小写字母、数字、短横线的组合，且不能连续短横线（如 `my-skill`）。未填写时兼容旧格式，系统会使用 `name` 作为 slug，此时 `name` 也必须满足 slug 规则 |
| `description` | 是 | Skill 的功能描述，会在 Agent 配置时展示 |

### 导入 Skill

可以通过以下方式导入或安装 Skill：

**方式一：从推荐列表安装**

1. 在系统设置的「Skills 管理」页面查看「推荐」分组
2. 未安装的推荐 Skill 会以普通 Skill 卡片样式展示，右侧显示 `+`
3. 点击推荐卡片或 `+` 后，系统会使用该 Skill 的远程来源拉取内容
4. 拉取成功后会弹出安装草稿，选择个人工作区或共享 Skill 后完成安装

已安装的推荐 Skill 不会继续显示在「推荐」分组中。

**方式二：通过 ZIP 包或 SKILL.md 上传**

1. 将 Skill 目录打包成 ZIP 文件（注意：ZIP 的根目录就是 Skill 目录）
2. 在系统设置的「Skills 管理」页面，点击「上传 Skill」
3. 上传 ZIP 文件或单个 `SKILL.md`
4. 系统解析上传内容并返回安装草稿
5. 选择安装位置后完成安装；选择共享 Skill 时继续确认共享范围，也可以放弃草稿

系统会自动：
- 校验 ZIP 内容和路径安全性
- 检查 slug 冲突：共享安装沿用全局冲突处理，个人安装同用户冲突时明确失败且不改写 slug
- 解析 SKILL.md 的 frontmatter；只有共享安装会写入数据库
- 按当前用户角色校验可选择的共享范围

**方式三：从远程来源安装**

管理员可以在「设置 → 基本设置 → Skill 配置 → 远程来源白名单」中配置允许远程安装 Skill 的来源域名；对应系统配置项为 `remote_skill_source_policy.allowed_hosts`。
该策略保存在 PostgreSQL `config_options` 中，默认允许 `github.com` 和 `modelscope.cn`，只做精确域名匹配，不自动放行子域名；保存空列表时，远程 Skill 安装会被禁用。运行时以数据库配置为准，不依赖 `base.toml`、环境变量或 Redis 配置快照。

1. 在 Skills 管理页面点击「远程安装」
2. 在“按仓库拉取”中填写来源，例如：
   - `anthropics/skills`
   - `https://github.com/anthropics/skills`
   - `https://modelscope.cn/skills/@anthropics/pdf`
   - `https://modelscope.cn/collections/MiniMax/MiniMax-Office-skills`
3. 点击“拉取技能”获取该来源中可发现的 Skills 列表
4. 单个 Skill 地址通常会自动选中；仓库或合集地址可在列表中勾选一个或多个 Skills
5. 点击“解析并确认”，系统返回安装草稿；选择个人工作区或共享 Skill 后正式安装

也可以切换到“全局搜索发现”，输入关键字检索 skills.sh 上的开源 Skills，再选择结果安装。

系统会在后端：
- 只接受管理员白名单中的 HTTPS 来源；GitHub `owner/repo` 简写按 `github.com` 校验
- 在不继承全局或用户环境变量的一次性 Sandbox 中执行 `npx skills`，Kubernetes Sandbox 不挂载 ServiceAccount token
- 通过 Sandbox 文件 API 提取对应 Skill，严格校验返回的相对路径，并限制文件数、目录深度和总大小；个人确认写入 workspace，共享确认写入 `/app/saves/skills` 与数据库

来源白名单用于限制产品允许的远程仓库，并不等同于 Sandbox 网络出口防火墙。

::: tip ModelScope 合集适合批量安装
ModelScope 合集地址可以作为远程来源填写，例如 `https://modelscope.cn/collections/MiniMax/MiniMax-Office-skills`。拉取后在列表中勾选需要的 Skills，再统一解析为安装草稿。
:::

**方式四：在线编辑已有 Skill**

在 Skills 管理页面，你可以：
- 新建目录或文件
- 在线编辑文本文件（支持 .md、.py、.js、.json 等格式）
- 直接在网页上修改 SKILL.md 内容

只有具备 `can_manage` 权限的用户才能编辑文件、依赖、共享范围和启用状态。

::: tip 安装位置决定管理能力
个人工作区适合公开、平台无关或用户自定义 Skill；共享 Skill 适合需要工具、MCP、Skill 依赖以及部门或全局共享的能力。
:::

## 依赖系统

Skill 可以声明工具、MCP 服务和其他 Skill 依赖；运行时根据依赖类型决定加载时机。

### 依赖类型

每个 Skill 可以声明三类依赖：

| 依赖类型 | 说明 | 加载时机 |
|----------|------|----------|
| `tool_dependencies` | 需要的内置工具 | 激活后按需加载 |
| `mcp_dependencies` | 需要的 MCP 服务 | 激活后按需加载 |
| `skill_dependencies` | 依赖的其他 Skill | 会话启动即生效 |

### 渐进式加载机制

Skill 加载分为三个阶段：

**阶段一：会话启动**

当 Agent 会话启动时，系统会：
1. 在创建 Graph 前读取已过滤的 `context.skills` 列表
2. 递归展开 `skill_dependencies`，派生 `_prompt_skills` 和 `_readable_skills`
3. 将 `_prompt_skills` 对应的技能说明注入到系统提示词中

配置某个 Skill 后，其依赖 Skill 会立即进入提示词。共享与内置 Skill 投影到沙盒只读路径 `/home/gem/skills`，个人 Skill 直接使用 `/home/gem/user-data/workspace/agents/skills/<slug>`。

**阶段二：技能激活**

当 Agent 通过 `read_file` 读取共享路径 `/home/gem/skills/<slug>/SKILL.md` 或个人工作区路径 `/home/gem/user-data/workspace/agents/skills/<slug>/SKILL.md` 时，系统将该操作视为 Skill 激活，并执行：
1. 验证该技能在可见列表中
2. 将其添加到 `activated_skills` 列表
3. 后续的模型调用会使用激活列表来加载依赖

**阶段三：按需加载**

每次模型调用时，系统会：
1. 检查 `activated_skills` 中的技能
2. 收集这些技能的 `tool_dependencies` 和 `mcp_dependencies`
3. 动态将需要的工具和 MCP 服务添加到可用工具集中

会话启动阶段只注入 Skill 说明。工具和 MCP 依赖在 Skill 激活后按需加入模型请求，从而控制初始工具 schema 的规模。

### 依赖声明示例

假设我们有三个 Skills：

- **base-skill**：基础技能，无依赖
- **advanced-skill**：依赖 `base-skill`
- **pro-skill**：依赖 `advanced-skill`

当在 Agent 配置中只选择 `pro-skill` 时：
1. 启动阶段：`_readable_skills` = [`pro-skill`, `advanced-skill`, `base-skill`]（自动展开依赖链）
2. Agent 首次调用任何 skill 时：所有三个 Skill 都可读
3. 当 Agent 读取 `pro-skill/SKILL.md` 时：触发激活，工具和 MCP 依赖被加载

## 权限管理

数据库中的共享与内置 Skills 使用 `source_type`、`share_config` 和 `enabled` 控制来源、共享范围和启用状态。个人工作区 Skill 由认证用户目录天然隔离，不携带 `share_config`，避免与数据库“指定用户共享”语义混淆。

| 字段 | 说明 |
|------|------|
| `source_type` | `builtin`、`upload`、`remote` 或 `personal` |
| `share_config` | 仅共享与内置 Skill 使用；v2 配置分别声明 `read_scope` 和 `manage_scope` |
| `enabled` | 是否允许在 Agent 配置与运行时使用 |

访问与管理规则：

| 用户 | 可见 / 可用 | 可管理 |
|------|-------------|--------|
| 超级管理员 / 管理员 | 可查看可管理或已启用且可访问的 Skills | 可管理所有非内置 Skills；可启停内置 Skills |
| 普通用户 | 可查看已启用且对自己可访问的 Skills，只能把新 Skill 安装到个人工作区 | 可管理自己创建的非内置 Skills |
| 内置 Skills | 默认全局共享并启用 | 管理员可启停；不允许删除或直接编辑文件 |
| 个人工作区 Skills | 只对当前用户可见，文件与缓存按 uid 隔离 | 当前用户可预览、删除和手动刷新 |

共享范围限制：

- `global`：所有用户可访问
- `department`：指定部门用户可访问
- `user`：指定用户可访问
- 只有管理员可以把 Skill 安装到平台共享 Skill 库；普通用户安装固定进入个人工作区，不创建数据库记录，也不配置共享范围

旧版单层共享范围会在 PostgreSQL 启动迁移中复制为读取和管理范围；运行时接口仅接受 v2 配置。

管理员和普通用户在创建或编辑 Agent 时，都只能从自己可访问且启用的 Skills 中选择能力。个人 Skill 与共享 Skill 同 slug 时，个人 Skill 整项覆盖共享版本；共享版本的工具、MCP 和 Skill 依赖不会继续加载。删除个人版本后，用户仍有权访问的同名共享版本会在下一次运行恢复生效。

## 运行时行为

### Agent 如何使用 Skills

1. **提示词注入**：系统在每次模型请求时动态注入可用 Skills 的描述（请求级注入，避免污染 runtime context）
2. **文件访问**：共享与内置 Skill 从只读 `/home/gem/skills/<slug>/...` 读取；个人 Skill 直接从工作区读取
3. **工具调用**：当 Agent 需要使用某个 Skill 时，会先读取对应的 SKILL.md 了解使用方法

### 文件操作限制

共享与内置 Skill 的运行时 `/home/gem/skills` 路径有以下限制：
- **只读**：Agent 只能读取文件内容
- **禁止写入**：不能创建、修改或删除文件
- **路径安全**：所有路径都经过安全校验，防止目录穿越攻击

::: tip 只读不等于不可执行
`/home/gem/skills` 对 Agent 是只读的，但沙盒命令工具仍可执行其中的脚本。Skill 应写清依赖、运行方式和产物位置；脚本产生的文件应保存到 workspace 或 outputs，Skill 目录禁止写入。
:::

个人 Skill 位于 `/home/gem/user-data/workspace/agents/skills`，属于用户可写工作区。运行时不会再把它复制到线程 `/home/gem/skills` 目录，因此工作区中的修改会直接成为后续读取内容。

### 会话隔离

每个 Agent 会话都有独立的 Skills 可见集：
- 不同会话可以配置不同的 Skills
- 同一会话内修改 `context.skills` 会重建共享与内置 Skill 的只读投影
- 个人 Skill 元数据最多缓存 5 分钟；安装、删除和 Skills 页手动刷新会立即更新缓存
- 每次构建运行时只同步最终生效的共享与内置 Skill；个人版本同名覆盖时直接使用工作区路径

## 维护建议

### Skill 命名规范

- `slug` 使用小写字母、数字和短横线，不能连续短横线
- `slug` 应具有描述性，如 `weather-query`、`sql-reporter`
- `name` 用于展示，可比 `slug` 更自然，例如 `Word / DOCX`
- 避免过长的 `name` 和 `slug`

### 依赖管理建议

- **保持依赖链简洁**：层级不宜过深，一般 1-2 层为宜
- **避免循环依赖**：系统会检测并阻止循环依赖
- **明确依赖必要性**：只在真正需要共享能力时才建立依赖

### SKILL.md 编写技巧

```markdown
---
name: example-skill
description: 简短描述技能功能
---

# 技能名称

这里是详细的使用说明...

## 何时使用

描述在什么场景下应该使用这个技能...

## 使用方法

1. 第一步...
2. 第二步...

## 示例

```
具体的使用示例...
```
```
