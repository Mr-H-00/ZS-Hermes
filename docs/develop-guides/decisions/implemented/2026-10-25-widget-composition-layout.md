# 对话组件的嵌套宽度与列布局

状态：implemented
类型：bug-fix
Owner：frontend/src/shared/ui/WidgetRenderer.vue

## 问题

合法组件组合出现布局缺陷：全宽卡片中的默认子卡片和列表仍受 440px 限制，列表重复外框；卡片和列表项的横向 padding 叠加，标题默认 20px、sm 为 18px，信息卡片显得过松。显式 flex:1 覆盖列的换行基准，flex:0 将内容列压为零宽。嵌套表单还通过后代选择器改变所有祖先卡片的宽度和间距。

## 决策

### 实现方案

WidgetRenderer 区分独立容器与嵌套容器：嵌套默认卡片填满父容器，嵌套列表不承担外框，显式 sm/lg 卡片保留尺寸上限。WidgetNode 的 flex 属性保留 Row 对 Col 的换行基准，非扩展列使用内容宽度；Form 与其他容器共用嵌套布局边界，表单外观只属于最近的 Card，允许中间存在 Row/Col。入口仍为 MarkdownPreview 挂载的实际组件树，不改变 JSON 协议、会话动作或持久化。内置 chat-widgets Skill 同步说明布局语义。

嵌套列表的普通项与可点击项共用 12px 垂直间距，横向不重复缩进；独立列表保留自身内边距。Title 默认及 md 为 16px，sm 为 14px，lg 为 18px，其他档位按同一紧凑比例收小，正文和说明字号保持现有层次。

该决定补充[对话组件决定](./2026-10-12-chat-widgets.md)，收窄[桌面表单布局决定](./2026-10-17-widget-desktop-form-layout.md)中的表单外观作用范围。

## 替代方案

- 只修改模型提示：历史消息仍然存在合法但失效的组合，不能修复渲染器。
- 删除所有最大宽度：会改变独立卡片与显式尺寸的用途。
- 增加布局协议字段：当前组合已能表达意图，无需扩展模型与维护面。

## 后果

历史消息的嵌套默认卡片使用可用宽度，窄容器中的等分列改为多行。独立默认容器保持 440px 上限，显式 sm/lg 尺寸与图片高度限制继续生效。仅内层拥有表单时，外层不再获得表单外观。Skill 更新沿既有发布与投影机制生效。

标题尺寸映射同时影响历史消息中的显式 Title.size；变化属于信息卡片的统一呈现调整，协议枚举保持不变。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 嵌套默认容器使用可用宽度，独立及显式尺寸保留 | CSS 级联与容器嵌套 | WidgetRenderer | widgetCompositionLayout 浏览器尺寸、表面样式与截图 | 修改前相同用例因 440px 限制与重复外框失败 | Passed |
| 等分列可换行，非扩展列保持内容宽度 | flex 属性映射与 CSS 装配 | WidgetNode / WidgetRenderer | widgetLayout unit 与浏览器列宽、坐标及标签边界 | 修改前 unit 失败，浏览器检测到不换行、零宽与重叠 | Passed |
| 表单样式止于最近 Card | 组件树归属 | WidgetNode / WidgetRenderer | unit、浏览器外层卡片宽度及样式 | 修改前相同浏览器用例因外层宽度、间距改变失败 | Passed |
| 嵌套列表左右对齐，标题呈现紧凑层次 | padding 继承、可点击项及显式字号 | WidgetNode / WidgetRenderer | 浏览器测量标题 left 坐标与实际字号，明暗截图 | 调整前六组 density 用例均因缩进叠加、默认及显式字号过大失败 | Passed |

`playwright-cli -s=<session> run-code --filename=frontend/test/browser/widgetCompositionLayout.js` 在真实 Vite 页面运行正式 Markdown/Vue 组件，1440、1024、768px 视口与明暗主题共 54 组组合检查通过。根 Form 的额外六组用例在补齐通用容器类前，因内部卡片、列表仍为 440px 及重复外框失败。脚本不创建会话、不发送消息，不将构造组件树的结果计为真实模型生成或持久化验证。浏览器回归作为独立审查的直接证据，现有 frontend workflow 执行 unit、lint 与 build。

| 实际命令 | 结果 |
|---|---|
| 宿主 frontend：`pnpm run lint:check`、`pnpm run test:unit` | Passed，852 tests |
| `docker compose exec -T frontend pnpm run lint:check`、`pnpm run build` | Passed，build 含 typecheck |
| `docker compose exec -T frontend pnpm run test:unit` | 843 passed、9 failed；容器 eslint-boundaries.js 为旧镜像文件，未挂载的规则与宿主仓库哈希不同，当前仓库配置由宿主完整 unit 验证 |
| `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow'` | Passed，3273 passed、122 skipped、7 subtests passed；跳过项不计能力通过 |
| Skill 中五个 widget 示例逐个调用 `parseWidget` | Passed |
| `python3 scripts/verify_engineering_contracts.py` | Passed |
| `python3 -m unittest scripts.test_verify_engineering_contracts` | Passed，66 tests |
| docs：`pnpm run build` | Passed，包含相对链接检查 |

真实模型选择与运行中已有 Skill 投影的更新未执行；该边界不影响历史消息的前端渲染修复。
