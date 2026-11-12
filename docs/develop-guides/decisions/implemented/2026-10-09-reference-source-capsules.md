# 回答来源胶囊与悬浮预览

状态：implemented
类型：feature
Owner：frontend/src/modules/workspace/ui/MarkdownPreview.vue

## 问题

数字角标、常驻整段底色和重复状态文案增加阅读噪声，来源需要依赖浏览器原生 title 才能辨认。用户要求参考站点名胶囊与可切换来源卡片的呈现。

## 决策

### 实现方案

Markdown 装饰器将同一回答块末尾的引用合并为一个中性胶囊，显示首个来源的域名或文件名及额外数量。MarkdownPreview 管理胶囊的悬浮、聚焦和点击，独立来源预览组件显示标题、证据、知识库行号和来源切换。

仅在查看来源时轻量高亮对应正文。单来源点击保留原文弹窗或网页打开行为，多来源可在卡片内选择。卡片按视口定位，支持键盘、触屏与关闭，不修改持久引用或后端调用。

## 替代方案

- 只调整数字角标颜色：无法直接辨认来源，多个角标仍分散注意力。
- 始终显示全部来源卡片：占用正文空间，不适合长回答。

## 验证

以下为来源任务在其基线上的原始验收；合入当前 main 的验证由[Turn 来源标注记录](2026-10-09-turn-reference-annotations.md#验证)拥有。

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 来源可辨认，同段多来源合并 | 数字标记、多胶囊堆叠 | Markdown 装饰器 | unit、真实浏览器 | 同段四个来源与长文件名 | Passed |
| 悬浮、聚焦与点击能访问正确来源 | 移向卡片时消失、键盘无法打开 | 来源预览 / MarkdownPreview | browser、lint/build | 末页焦点、Escape、触屏 | Passed |
| 不破坏既有引用与预览 | 错误行号、隐藏后残留、窄屏越界 | session/workspace/knowledge UI | unit、browser | SVG/HTML 复用、隐藏/恢复、低高度内部滚动 | Passed |

- `docker compose exec frontend pnpm run test:unit`：516 passed；相关 11 项 unit 覆盖同块合并、跨块归属、未命中范围、来源 store 和 Markdown 原始行号。`pnpm run lint:check` 与 `pnpm run build` 通过。
- `playwright-cli -s=turn-refs run-code --filename=frontend/test/browser/sourceCapsules.js`：以真实 HTTP 与 PostgreSQL 的持久会话验证 `arxiv.org +3`、长文件名截断、同组四来源切换、知识文件第 8–12 行、Escape、浅深色、390px 手机宽度与 320×200 卡片内部滚动。来源关系是显式保存的固定 oracle；该脚本验证 UI，不验证模型语义。
- `playwright-cli -s=turn-refs run-code --filename=frontend/test/browser/turnReferences.js`：真实 HTTP 独立模型 wire 重放与数据库持久化验证等待、文件定位、网页跳转、隐藏/恢复和刷新。`turnReferenceBoundaries.js` 复验原始第 5/19 行、SVG、嵌套与顶层同内容 HTML iframe 复用，以及 Dify 片段入口。
- 独立 Reviewer 使用真实组件 DOM 复验首末页焦点、低高度按钮可达、暗色背景、立即聚焦不滚动原文和根 class 保留。原生 disabled 导致翻页丢焦点的负向案例改为 aria-disabled 和边界限制；卡片高度受视口限制，内部滚动不触发原文滚动关闭。完整页面的低高度案例等待胶囊聚焦引起的原文滚动结束，再检验卡片内部滚动，避免混淆两种行为。
- `python3 -m unittest scripts.test_verify_engineering_contracts`：64 passed；`git diff --check` 通过。来源任务基线的工程检查因既有 decision 类型字段失败；当前 main 已修正该字段，合并后的工程检查通过，见上述验收 Owner。

## 后果

胶囊标签可能很长，需要截断而保留卡片完整标题。悬浮预览需要允许指针与键盘移动到卡片，并在原文重新渲染或滚动后关闭，避免指向已替换节点。

本次不改变后端持久引用、权限或独立模型调用。浏览器验证使用桌面 Chromium，未覆盖其他浏览器或真实触屏硬件。
