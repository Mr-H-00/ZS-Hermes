# 页面布局与暗色表面一致性

状态：implemented
类型：bug-fix
Owner：frontend/src/shared/ui/PageHeader.vue

## 问题

管理页在 768px 视口、展开侧栏时，固定行高和不可换行的标签遮挡右侧摘要及末尾标签。设置弹窗在长部门列表下超出视口，关闭按钮随内容离开可见区域。局部面板的装饰阴影、重复描边和暗色主按钮的低文字对比度也与现有视觉规范不一致。对话输入框的空态提示在浅色背景上过淡；评估基准生成表单过高，底部操作离开初始视口。

## 决策

### 实现方案

[共享页头](https://github.com/xerrors/Yuxi/blob/main/frontend/src/shared/ui/PageHeader.vue)负责标题、标签和操作区换行，由内容决定高度；管理页摘要使用文本间距组织指标。[设置弹窗](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/settings/ui/SettingsModal.vue)始终受视口高度约束，内部内容负责滚动；账户消息活动区复用设置标题样式。

[Ant Design 主题入口](https://github.com/xerrors/Yuxi/blob/main/frontend/src/shared/model/theme.js)显式设置 Button、Switch 主色，避免暗色算法再次降低主色亮度；主按钮普通状态的文字对比度由 4.18:1 提高至 5.41:1。按钮装饰阴影归零，键盘焦点轮廓保留。[共享样式](https://github.com/xerrors/Yuxi/blob/main/frontend/src/assets/css/antd.less)给暗色下拉菜单及 Popover 添加细轮廓，补足取消阴影后的浮层边界。普通卡片、后台任务、评估列表及认证页面沿用已有表面 token，以背景层级区分区域，删除装饰阴影和多余描边。

对话输入框的原生及 contenteditable 占位提示统一使用 `--gray-600`，在普通和聚焦状态保持可读。`BenchmarkGenerateModal` 参照现有 MCP 表单约束正文高度，表单内部滚动、标题和底部操作保持可见。Skill 安装窗的嵌套表面使用 `--border-surface`，保留行分隔；依赖选择按钮取消装饰阴影，浮层使用 `--shadow-popover` 并保留边框。

暗色 `--border-surface` 使用 `--gray-150`，悬停使用 `--gray-200`；菜单分隔线由 `colorSplit` 保持可见。设置侧栏、内容和账户卡片通过表面与深灰边框区分。业务表格使用实色背景、表头底色、外框与行分隔；设置表格显式统一 Ant Design 的行上边框与下边框，避免框架 border shorthand 覆盖分隔。透明背景仅用于 Markdown 渲染表格，包括其行与单元格。关闭状态的 Switch 使用深灰轨道和浅色标签。

实现仅涉及前端呈现，不改变权限、接口、持久化或任务执行。独立工作树基于已提交版本，不包含原目录尚未提交的功能。

## 替代方案

- 全局取消所有边框和阴影：输入、浮层及焦点会失去必要边界，不采用。
- 每页追加暗色覆盖：会产生多个外观事实源，使用已有主题入口和组件所属样式。
- 重做视觉体系：超出修复范围，不引入新配色、组件库和配置。

## 后果

窄桌面页头可增加高度，以保证标签和操作完整可达；设置长内容在弹窗内滚动。暗色普通卡片使用可辨认的深灰细边框，浮层保留细轮廓。支持宽度遵循前端约定，以 768、1024、1440px 为验收视口。

浏览器使用真实资源进行只读核对，缺失资源和边界状态采用合成数据。外部身份登录回调、真实 CLI 授权及模型、评估任务执行不在此次界面验证范围内。

## 验证

以下命令在独立工作树执行，结果通过。依赖复用时显式关闭 pnpm 的运行前依赖重装检查。

```bash
pnpm --config.verify-deps-before-run=false --dir frontend run lint:check
pnpm --config.verify-deps-before-run=false --dir frontend run test:unit
pnpm --config.verify-deps-before-run=false --dir frontend run build
python3 scripts/ci/verify_engineering_contracts.py
python3 -m unittest scripts.ci.test_verify_engineering_contracts
pnpm --config.verify-deps-before-run=false --dir docs run build
git diff --check
```

前端单测 963 项通过；工程契约单测 56 项通过。构建通过，保留已有的大 chunk 提示。只涉及 UI 且未提交代码，未运行后端 unit，不据此宣称后端链路通过。

在已登录独立 Vite 预览的浏览器会话中执行以下脚本；`<session>` 替换为对应会话名。设置、共享组件及会话侧栏脚本使用其自带合成场景，不写真实业务数据。

```bash
playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/pageHeaderLayout.js
playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/antDesignAppearance.js
playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/settingsStyles.js
playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/sessionSidebar.js
```

[页头回归](https://github.com/xerrors/Yuxi/blob/main/frontend/test/browser/pageHeaderLayout.js)检查每个标签的实际边界、命中位置及末尾入口；注入旧固定布局后，同一断言拒绝重叠。[外观回归](https://github.com/xerrors/Yuxi/blob/main/frontend/test/browser/antDesignAppearance.js)检查主按钮状态对比度、输入焦点、浮层边界和静态反馈主题；恢复旧阴影或移除浮层轮廓时，同一断言拒绝不合格样式。

[设置回归](https://github.com/xerrors/Yuxi/blob/main/frontend/test/browser/settingsStyles.js)覆盖 8 个分区、两种主题、三个宽度，共 48 组布局，另检查长列表、加载、空态和错误态；恢复无界弹窗后，同一边界断言拒绝视口溢出。会话侧栏回归覆盖历史、返回会话、草稿保留、菜单和主题布局。

逐页核对补充覆盖主入口、知识库及扩展详情、公开认证与错误页、评估详情：主入口 90 组、已有资源详情 36 组、公开页面 18 组、合成评估详情 6 组均未发现页面横向溢出。真实环境缺少评估基准数据，因此评估详情及空结果界面由合成数据验证，不代表真实评估任务链路通过。截图关闭动画以避免过渡帧误判；本地截图是辅助证据，回归断言由上述源码维护。

### 页面内状态验证

主入口数量包含 query 标签视图，不等于独立页面组件数量；跨主题、宽度重复运行不能计为新增页面。主入口截图及溢出检查不构成页面内部全部交互的覆盖证明。

`composerSurfaces.js` 在占位提示修复前以“空输入框占位文字对比不足”失败，修复后明暗主题、普通及聚焦状态的同一对比度断言通过。`benchmarkModalLayout.js` 验证两种主题和三个宽度下的表单内部滚动、标题及底部边界；恢复无界正文后，同一几何断言拒绝旧布局。

以下浏览器回归执行通过：模型供应商卡片与两类弹窗、API 集成、检索阅读、上传队列、Markdown 预览和编辑、定时任务历史与统计空态、审批失败恢复。对应现有脚本为 `modelProviderCards.js`、`apiIntegrationSurfaces.js`、`knowledgeQueryReading.js`、`knowledgeUploadQueue.js`、`markdownPreviewLayout.js`、`knowledgeMarkdownEditing.js`、`observabilityViews.js`、`sessionApprovalFailure.js`。固定预览端口的脚本使用本地副本指向独立 Vite；编辑测试的原生刷新确认使 CLI 提前返回后，从页面的 `window.markdownUi.verification` 回读最终结果。检索阅读脚本等待动画完成时忽略动画被正常取消产生的拒绝，后续颜色、布局和下载断言仍必须通过。

合成场景另检查知识库创建的类型、配置、权限步骤，评估基准上传与生成、新建评估、MCP 接入说明、新建及编辑 MCP、OAuth 高级配置、附件、反馈、Skill 安装长列表和后台失败作业，共 14 种弹窗或步骤状态，各覆盖两种主题及三个宽度。没有提交真实创建、上传、授权、安装或执行操作。正常界面的截图与 DOM 检查不能代表这些操作的后端成功路径，权限角色组合及所有数据组合也未穷举。

### 表格与分隔边界

设置回归的 48 组布局同时检查业务表格表头与行分隔，真实用户菜单组件检查两条分隔线。注入透明分隔线后，同一断言拒绝缺陷。`markdownPreviewLayout.js` 在两种主题与四种宽度检查 Markdown 表格、行及单元格背景透明，注入实色行背景后同一断言拒绝缺陷。`composerSurfaces.js` 检查普通卡片保留深灰边框且没有装饰阴影。共享控件回归验证关闭状态开关的标签对比度为 7.25:1。
