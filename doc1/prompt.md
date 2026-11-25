# 实现 Yuxi 语音设置、翻译和绘画

在 `E:\pythonProject\Yuxi` 接入语音设置，并把 `E:\浏览器下载内容\cherry-studio-main` 中社区版的「翻译」「绘画」嵌入 Yuxi 一级导航。两页的内容区以 Cherry 源码及用户截图验收，保留控件位置、布局、文案、模板、空态和交互。Yuxi 的 Vue、FastAPI、登录和模型目录承担 Web 环境的适配。主 Agent 负责整体进度、共享接口、集成和最终验收。确定接口及文件归属后，可将语音、翻译、绘画分别交给子 Agent 实现并测试。主 Agent 审阅并合并共享路由、导航和模型目录的修改。仅在读回证据并验证通过后勾选本文件中的任务，在文末记录实际命令、结果和剩余问题。

下文第 1 至 4 节记录首版设计及检查。翻译和绘画的界面、功能边界和验收以文末「Cherry 工作区迁移」为准。

动手前读取根、`frontend/`、`backend/` 及适用的 `docs/AGENTS.md`，按 [架构](../ARCHITECTURE.md)、[Spec Loop](../docs/develop-guides/spec-loop.md)、[工程信任](../docs/develop-guides/engineering-trust.md)、[测试规范](../docs/develop-guides/testing-guidelines.md)和[设计规范](../docs/develop-guides/design.md)确认实际装配和仓库约定。任务类型为 `feature`。实现前按仓库规则建立 tracked `proposed` 决策，记录各语义 Owner、真实替代方案、取舍和验证矩阵。

## 1. 需求与边界

- **入口。** 登录用户在现有设置弹窗看到「语音」，并从一级导航打开同级的「翻译」和「绘画」。两个独立页面支持直接访问、刷新和正确的导航高亮。普通用户可使用三项功能，模型供应商管理沿用现有角色权限。
- **语音。** 用户可配置识别语言及朗读音色、语速，能在设置中试听与停止。启用听写后，主动点击聊天输入区的麦克风按钮，最终识别文本追加到当前草稿并可编辑。发送仍由用户触发。对已完成的助手回复提供手动朗读与停止。自动朗读默认关闭，用户启用后，每条新完成的权威最终回复最多自动朗读一次。
- **翻译。** 用户选择源语言或「自动检测」、目标语言和可用聊天模型，输入文本后提交，得到独立输出区中的译文，可复制。源语言明确时支持交换语言。空文本、无可用模型和请求失败均有明确反馈，失败后可直接重试。
- **绘画。** 用户选择可用生图模型，在能力允许时选择尺寸，输入提示词后生成一张图，能预览和下载实际图片。未配置、生成中、模型失效、超时和供应商错误均有明确反馈。
- **现有行为。** 保留导航已有入口的相对顺序和权限，尤其是「新对话」的最近会话行为。保留个人 Memory 设置、聊天提交及协作限制、模型供应商现有类型、Agent 的 `image-gen` Skill 和「网盘 → 我的成果」的正式 Run 产物发布。

### 首版假设

- 语音使用桌面浏览器的 Web Speech 能力。听写以实测支持 `SpeechRecognition` 或 `webkitSpeechRecognition` 的 Chromium 环境为验收基线，使用 HTTPS 或 localhost。音色由浏览器和系统提供，麦克风使用浏览器当前默认输入。偏好按浏览器和登录账号保存，设备间各自配置。识别可能由浏览器供应商远程处理，首次启用前明确说明，麦克风权限由用户操作触发。自动朗读仅使用可确认的本地音色。Electron、本地模型下载、系统级快捷键和设备选择器属于后续范围。
- 翻译首版只处理文本。绘画首版只处理文生图，通过一个经验证的 OpenAI Images 兼容协议接入直接返回图片字节的模型。独立页面的内容、结果和进行中请求限于本次页面使用，图片由用户下载。跨刷新历史、翻译文件及 OCR、PDF 排版翻译、参考图、局部编辑、后台生图和独立结果自动入网盘属于后续范围。
- 翻译原文与绘画提示词由服务端发送给管理员已配置的相应模型供应商。浏览器语音适配器自身只保存控制偏好，听写文本进入草稿后沿用现有草稿处理。新页面使用有明确时限的请求，用户可在失败后主动再试。脱离页面执行和恢复任务需要另行界定范围。

## 2. 技术方案

### 已查证的实现

| 范围 | Yuxi 可复用入口 |
|---|---|
| 导航与布局 | `frontend/src/app/layouts/AppLayout.vue` 的 `mainList` 共用一级导航，`frontend/src/app/router/index.js` 定义 `AppLayout` 下的登录页面。页面复用 `frontend/src/shared/ui/PageHeader.vue`、现有样式变量及图标。 |
| 设置与聊天 | `frontend/src/modules/settings/ui/SettingsModal.vue` 有两套布局菜单、`availableTabs` 和惰性挂载。`frontend/src/modules/identity/model/user.js` 提供登录用户 `uid`。`backend/yuxi/modules/identity/preferences.py` 的现有用户配置只有 `enable_memory`。`frontend/src/modules/session/ui/SessionWorkspace.vue` 拥有会话草稿，通过 `AgentInputArea.vue` 的 `actions-right-extra` 加入输入操作。`RefsComponent.vue` 已根据最终阶段、执行状态和结果消息身份约束回复操作。 |
| 模型与 HTTP | `frontend/src/apis/base.js` 封装认证和请求。`frontend/src/apis/system_api.js` 的 `modelProviderApi.getV2Models` 读取登录用户可用的按类型模型目录。`frontend/src/modules/agents/ui/ModelSelectorComponent.vue` 使用该目录。后端 `backend/yuxi/modules/models/chat.py` 的 `load_chat_model` 支持从模型 spec 装配 LangChain 客户端并处理受支持供应商的可信用户头。 |
| 生图配置和文件边界 | `backend/yuxi/modules/models/providers/service.py` 当前只接受 `chat`、`embedding`、`rerank`。`providers/cache.py` 以 `provider_id:model_id` 分组已启用模型，凭据由 `providers/credentials.py` 在服务端解析。供应商管理 UI 位于 `frontend/src/modules/settings/ui/ModelProviderManagePanel.vue`。内置 `backend/yuxi/modules/extensions/skills/builtin/image-gen/SKILL.md` 是 Agent 沙盒示例。 `backend/yuxi/modules/workspace/services/deliverables.py` 的 `save_run_deliverables` 依赖真实 Thread/Run 归属。 |

Cherry Studio 源码定位以 `E:\浏览器下载内容\cherry-studio-main` 为起点。按以下入口追踪页面组件、样式、业务调用和同目录测试。

| 功能 | Cherry 源码入口 |
|---|---|
| 语音设置 | `src/renderer/pages/settings/VoiceSettings/VoiceSettings.tsx`、`DictationShortcutSettings.tsx`。语音运行时在 `src/renderer/services/voice/` 的 `DictationService.ts`、`SpeechPlaybackService.ts` 和 `AutoReadService.ts`。 |
| 翻译 | `src/renderer/pages/translate/TranslatePage.tsx`。同目录的 `TranslateSettings.tsx`、`components/TranslateLanguageBar.tsx`、`TranslateInputPane.tsx`、`TranslateOutputPane.tsx`、`TranslateHistory.tsx`、`translationFiles.ts` 和 `pdf/PdfTranslationView.tsx` 分别定义设置、语言工具栏、双栏、历史、上传和 PDF。调用链见 `src/renderer/hooks/translate/useTranslate.ts`、`src/main/services/translate/translateService.ts` 与 `src/main/services/PdfTranslationService.ts`。 |
| 绘画 | `src/renderer/pages/paintings/PaintingPage.tsx`。同目录的 `components/PaintingStrip.tsx`、`PaintingTemplateShowcase.tsx`、`PaintingComposer.tsx`、`PaintingSettings.tsx`、`PaintingPromptPresetsDialog.tsx`、`Artboard.tsx` 定义界面。生成和历史分别从 `model/generatePainting.ts` 与 `hooks/usePaintingHistory.ts` 追踪，供应商调用入口为 `src/main/ai/AiService.ts` 的 `generateImage`。原模板资源在 `resources/data/painting-templates/`。 |

### 计划新增

**前端入口与语音。** 在 `mainList` 的现有普通功能组中加入「翻译」「绘画」，挂载 `/translate` 和 `/paintings`。页面壳放在 `frontend/src/pages/`，业务组件及请求状态归各自模块。在设置弹窗两套布局菜单和 `availableTabs` 增加个人 `voice` 标签及惰性加载面板。将浏览器语音调用和偏好放在前端语音模块，设置、聊天按模块公开入口消费。以非空认证 `uid` 隔离本机偏好，处理浏览器音色异步加载、拒绝权限、无音色和不支持。账号退出、会话切换或组件离开时终止相应操作，忽略过期回调。听写通过现有草稿更新路径并服从输入禁用条件。手动朗读读取最终答案的可显示正文。自动朗读复用已确认的 `result_run_id` 和 `output_message_id`，仅消费当前会话新完成的最终答复，跳过历史恢复、重放、思考及工具内容。

**翻译。** 在 `backend/yuxi/api/routers/` 加入认证 Web 路由并注册到现有 `/api` 路由集合。首版契约为 `POST /api/translate`，输入 `text`、`source_language`（含 `auto`）、`target_language`、`model_spec`，返回 `translated_text`。在翻译领域 service 校验有界文本、受支持语言对和当前可用聊天模型。固定提示词与用户原文使用不同消息字段，明确只输出翻译文本。通过 `load_chat_model(..., uid=current_user.uid)` 和异步模型调用保留现有供应商约定，成功后一次返回非空译文。前端复用模型目录，普通 API 调用放入 `frontend/src/apis/`，译文按纯文本处理。切换页面或开始新请求时正确处理旧响应。

**绘画。** 在现有供应商配置 JSON 中新增显式 `image_generation` 类型及对应 capability，沿用现有 model spec 和凭据解析。管理员在已有管理页手动添加兼容模型。对照 `providers/service.py` 的创建、更新、模型状态探针及 `providers/cache.py`，校验配置与类型，确保已有聊天、向量和重排模型选择不受影响。模型状态检查需对生图类型单独表达配置状态，避免使用聊天探针或为了探活生成付费图片。绘画模型目录只返回脱敏信息。

**生图调用。** 新增认证 Web 路由 `POST /api/paintings/generate`，接收 `model_spec`、有界 `prompt` 和允许的 `size`，返回单张经校验的图片字节及真实 MIME。绘画 service 编排用例，模型层适配器负责上游协议。以当前启用的服务端供应商配置解析 endpoint、能力和凭据，失效或不支持的模型在外部调用前拒绝。选用经验证可返回 `data[0].b64_json` 的 OpenAI Images 兼容契约，按选定模型支持的尺寸和字段调用 `/images/generations`。供应商临时 URL、主机路径、任意 URL 和客户端凭据不进入接口。限制上游响应、解码字节和像素，校验图片可解码及格式后使用同源认证响应。前端使用图片 Blob 预览和下载，在替换、离开或退出时释放对象 URL。

**错误与副作用。** 用现有领域错误和 HTTP 转换约定区分无效输入、不可用模型、超时、上游拒绝及无效响应。返回可理解的错误，日志避免记录原文、音频、提示词、完整上游响应、凭据及签名。待请求完成前禁用重复提交，超时允许手动发起新请求。两项 Web 请求均无需创建 Thread、Turn、Run 或队列作业，也无需调整数据库 Schema 或新增服务。若真实供应商能力或产品约束与这些假设冲突，由主 Agent 在实施前根据真实协议和风险收敛方案。

## 3. 实现任务

- [x] **确认契约与方案。** 核对了现有装配和相关测试。以 Edge 的 Web Speech API 和 GPT Image 兼容的单图 PNG 协议为基线。主 Agent 负责共享装配，语音、翻译与绘画各自实现和验证。 [决策记录](../docs/develop-guides/decisions/implemented/2026-11-19-voice-translation-paintings.md)
- [x] **接入一级路由与页面。** `AppLayout.vue` 和路由表已接入两个独立页面。在 Edge 确认普通用户直达、刷新、菜单高亮和配置管理权限，以及匿名访问转登录。
- [ ] **实现语音设置和聊天消费。** 功能已接入设置与会话，定向测试和真实浏览器设置、原生朗读通过。真实麦克风识别并将非空文本写入草稿仍待验证。
- [x] **实现翻译。** 已接通 Web 路由、service、聊天模型调用、前端 API 与页面。pytest 验证了权限、输入、供应商协议、异常与连接释放。Edge 实际复制了模拟供应商的译文，已配置的聊天供应商也返回了短句的真实译文。
- [ ] **实现绘画。** 管理配置、绘画 API 和页面已实现。确定性上游中验证了普通用户模型选择、生成、预览、下载及失败场景。真实生图供应商仍待验证。
- [ ] **集成与验收。** 主 Agent 在同一 Compose 环境验证三项入口和关键回归，回读 HTTP、DOM、草稿与下载文件证据。按仓库要求更新决策状态和必要使用说明。若进入提交环节，先读取贡献指南，由不继承实现上下文的 Reviewer Agent 检查需求、diff、测试与规范，修复实质问题后复验。完成条件为本文件各项结果可复现且未验证范围有据可查。

## 4. 验证与交付

| 场景 | 操作与预期结果 |
|---|---|
| 导航与角色 | 在真实页面用普通账户打开设置及两个一级页面，验证直达、刷新、高亮和管理员入口权限。匿名访问业务路径触发登录，匿名调用新 API 返回未认证且上游没有请求。 |
| 语音 | 在目标 HTTPS 或 localhost 浏览器中实际试听和识别一段短句。草稿保有原文且追加最终识别文字，未产生聊天提交。保存偏好后刷新仍有效，切换账号读取该账号自己的偏好。验证拒绝权限、浏览器不支持、识别过程中切换会话。新完成回复在启用自动朗读时只读一次，刷新历史、工具内容和其他回复均不触发。涉及分支可用浏览器替身做确定性回归，同时记录真实语音的浏览器、平台和结果。 |
| 翻译 | 以固定短句及两个目标语言调用真实应用 HTTP，验证模型请求携带正确语言和原文，结果出现在输出区并可复制。测试空白、越界输入、已禁用或非聊天模型、超时及空响应。用可控上游验证实际 wire 内容，并对配置好的聊天模型做一次真实短文本翻译。 |
| 绘画 | 管理员保存生图模型，普通用户用短提示词生成并下载一张图。核对上游请求模型、尺寸和参数，解码文件并与预览核对。验证非生图模型、已禁用模型、越界参数、上游超时、畸形 base64、非图片和超大响应，不出现伪成功或被旧响应覆盖的结果。用确定性上游图片覆盖边界，再用配置好的兼容供应商做一次真实生成。 |
| 现有路径 | 复测设置惰性挂载和 Memory、模型目录与供应商配置保存、聊天草稿和发送、Agent 正式成果发布、浅色和深色、768px 及以上关键视口，留存核心页面及至少一个失败或空状态的截图。 |

从仓库根目录通过 Compose 验证。以 `docker compose --profile container ps` 核对环境，按测试规范启动缺少的依赖，API 代码改动后按开发拓扑重启相应服务。先运行实际新增及直接受影响的 pytest，再扩大到仓库要求的范围。API 验证使用真实 HTTP 和真实认证，同时核对状态码以及实际返回的正文、图片或协议。

```bash
docker compose exec api uv run --no-sync --group test pytest test/unit -m "not slow"
docker compose exec api uv run --no-sync --group test pytest test/integration/api
docker compose exec frontend pnpm run lint:check
docker compose exec frontend pnpm run test:unit
docker compose exec frontend pnpm run build
python3 scripts/ci/verify_engineering_contracts.py
python3 -m unittest scripts.ci.test_verify_engineering_contracts
git diff --check
```

相关测试入口包括 `backend/test/unit/services/test_model_selectors.py`、`test_model_provider_service.py`，`backend/test/unit/models/test_model_cache.py`，`backend/test/integration/api/test_model_provider_uid_header.py`，`backend/test/e2e/test_workspace_deliverables_e2e.py`，以及 `frontend/test/unit/settings_lazy_mount.test.js`、`modelProviderSaveActions.test.js`、`modelDirectory.test.js`。按改动面选取测试。浏览器验证参考 `frontend/test/browser/settingsStyles.js` 和 `composerSurfaces.js`。相关文档改动用相对链接检查和 `docs/` 下的 `pnpm run build` 验证。

Python 新增或实质修改的模块必须通过 mypy。当前仓库已有 Ruff 配置，尚无项目级 mypy 命令。从 `backend/` 使用项目环境临时加载固定版本的 mypy，将下方命令末尾追加本次 Python 文件路径并记录实际命令。基线报错另外记明，新增错误在原处修复。Ruff 在含 dev 依赖的环境执行。

```bash
uv run --no-sync --with mypy==1.19.1 mypy --follow-imports=silent --check-untyped-defs
uv run ruff check yuxi
uv run ruff format yuxi --check
uv run ruff check --select I yuxi
```

### 实际检查记录

| 命令或实测步骤 | 结果及证据 | 剩余问题 |
|---|---|---|
| 前端定向 `node --test --test-concurrency=2 test/unit/sessionVoice.test.js test/unit/agentRunEvents.test.js test/unit/settings_lazy_mount.test.js` | Passed，27 项通过。验证旧会话回调、账号偏好、权限拒绝、新完成结果与旧终态。 | 设备麦克风实录仍待验证。 |
| Edge 页面和浏览器 API | Passed，验证了登录、一级导航、翻译复制、绘画失败提示、可解码的 PNG 下载、两个页面离开后重置、768px 深色布局及语音设置默认值。浏览器原生朗读引擎发出开始和结束事件。 | 听写启动成功，合成麦克风样本返回空转写，真实麦克风到草稿尚待验证。 |
| `docker compose exec -T api uv run --no-sync --group test pytest -p no:cacheprovider -q --tb=short test/unit/services/test_translation.py test/integration/api/test_translate_api.py test/integration/services/test_session_title_generation.py` | Passed，60 项通过。使用 TCP、JWT、PostgreSQL 和 Redis。覆盖译文中的供应商推理标记和自动标题回归。已配置聊天供应商也通过最终 HTTP 路由返回干净的短句译文。 | |
| `docker compose exec -T api uv run --no-sync --group test pytest -p no:cacheprovider -q test/unit/models/test_image_generation.py`，以及 `test/unit/models/test_chat_tool_text_content.py test/unit/services/test_model_selectors.py test/integration/api/test_paintings_api.py` | Passed，10 项生图测试和 51 项真实 HTTP 集成及模型回归通过。核对了 PNG 格式、尺寸、像素及上游请求参数。 | 当前部署未配置 GPT Image 兼容模型，真实生图供应商尚待验证。 |
| 供应商及目录定向检查 | Passed，供应商/cache/route 与绘画合并定向批次 59 项通过，三个模型 UI 定向文件 17 项通过。 | |
| `mypy --follow-imports=silent --check-untyped-defs` | Passed，本次改动的应用 Python 文件通过定向检查，供应商服务文件对比 HEAD 有相同的 14 个既有诊断。 | |
| `uvx ruff@0.16.4 check yuxi`、`format yuxi --check`、`check --select I yuxi` | Passed，三个后端范围检查均通过。 | |
| `pnpm run lint:check`、`pnpm run build` | Passed，前端 ESLint、类型检查与生产构建通过。构建报告既有 CSS 与 chunk 大小警告。 | |
| `pnpm run test:unit` | Passed，宿主环境 1095 项通过。Compose 挂载上的初次执行出现 Vite SSR 超时，改用项目宿主依赖复验。 | |
| Compose `pytest test/integration/api/test_workspace_deliverables_api.py test/e2e/test_workspace_deliverables_e2e.py` | Passed，8 项通过，包含 worker 自动保存和重放。初次执行时 PostgreSQL 正在恢复，服务恢复后重跑通过。 | |
| 后端全量 `pytest test/unit -m 'not slow'` | Linux 本地文件系统执行，3959 项通过、11 项跳过、3 项失败。修正测试包的文件权限与脚本挂载后，两项独立复跑通过。Compose 原始挂载执行中的 worker 探针超时，在 Linux 本地文件系统通过。 | `test_locked_python_packages_use_official_registry_and_files` 仍失败，已在 HEAD 复现 `filelock` 锁定地址使用镜像。 |
| `python scripts/ci/verify_engineering_contracts.py`、`python -m unittest scripts.ci.test_verify_engineering_contracts`、文档 `pnpm run build`、`git diff --check` | Passed，工程契约检查和 56 个自测在 Linux 容器通过，文档构建和补丁检查通过。 | |

最终交付列明实际修改、每项通过的证据、支持的浏览器及生图配置，以及环境或凭据不足导致的未验证项。将 `Passed`、`Inspected`、`Not run` 与 `Inferred` 分开记录，供应商替身结果仅作为确定性协议证据。

## Cherry 工作区迁移

从本文件第 2 节列出的 Cherry 源码入口追踪每个控件、布局和操作。一级入口仍使用 Yuxi 的 `AppLayout.vue` 和登录路由。内容区对照用户截图验证尺寸、控件顺序、间距、空态文案、主题和交互。桌面进程调用通过对应的浏览器能力和 Yuxi 后端接入。

- [x] **翻译布局和正文功能。** `frontend/src/pages/TranslateView.vue` 挂载 `frontend/src/modules/translation/ui/`。顶部语言与模型操作、原文与译文双栏、上传区、右侧设置和历史按 Cherry 页面组织。正文支持复制、收藏、历史复用、自定义语言、语音及 Markdown。后端 `/api/translate`、`/detect`、`/extract` 使用现有聊天模型和文档解析，保留认证及失败反馈。Edge 已对照布局并实际操作文本上传、翻译、复制、设置与历史。
- [x] **绘画布局和主流程。** `frontend/src/pages/PaintingsView.vue` 挂载 `frontend/src/modules/paintings/ui/`。保留原模板资源、左侧绘画记录、画板和底部创作栏。新增 `/api/paintings/render` 以已启用模型的能力校验尺寸、参考图和参数，由浏览器预览并下载校验后的图像。Edge 已观察生成图片、旋转缩放、下载、刷新后恢复历史及窄屏布局。
- [ ] **版式 PDF 的部署验收。** `TranslationPdfPane.vue` 提供 PDF 双栏模式，`backend/yuxi/modules/translation/pdf.py` 调用可选 BabelDOC Stream。已配置的 OCR 用于扫描件及图片，未启用 BabelDOC 时可翻译正文。用实际 BabelDOC、受授权模型和非扫描 PDF 回读下载文件的页数、译文和版式后勾选。

绘画当前对接管理员配置的 GPT Image 兼容模型和硅基流动 Qwen Image / Qwen Image Edit，单次生成一张。历史随浏览器账号保存，Windows 全局听写快捷键和桌面应用后台任务属于原生应用的能力。

### 本轮检查记录

| 检查 | 结果 |
|---|---|
| 绘画 `pytest test/unit/models/test_image_generation.py test/integration/api/test_paintings_api.py` | 13 项协议单测通过，真实 HTTP 集成在 PostgreSQL 恢复服务后单独重跑通过。实测 Qwen Image 文生图返回 1664×928 PNG，Qwen Image Edit 参考图调用返回 1024×1024 PNG。 |
| Edge 前端交互 | 正文翻译通过真实 Yuxi API 和受控上游验证，复制文本与 PNG、历史收藏与复用通过。受控 PDF API 响应覆盖原文预览、正文降级、版式预览、下载及刷新后历史恢复。受控延迟生图验证同一账号切换一级页面后可继续等待结果，结果在页面隐藏时返回也可恢复。 |
| 翻译后端 pytest | 13 项单测通过。修正测试执行器的 CLI 参数声明后，PDF HTTP 回归尚无最终结果。隔离安装真实 BabelDOC 时 Docker Engine 中断，真实保版式输出尚未验收。 |
| `pnpm run test:unit`、`pnpm run lint:check`、`pnpm run build` | 前端 1095 项单测通过，ESLint、TypeScript 与构建通过。 |
| 后端 `ruff check yuxi`、`ruff format yuxi --check`、`ruff check --select I yuxi` | 通过。 |
| 定向 `mypy --follow-imports=silent --check-untyped-defs` | 两个 API 路由、翻译和绘画业务模块及生图适配共 8 个 Python 源文件通过。 |
| `scripts/ci/verify_engineering_contracts.py` 和 `scripts.ci.test_verify_engineering_contracts` | Linux 容器执行，工程检查和 56 项自测通过。 |
| `docs/` 下的 `pnpm run build`、`git diff --check` | 通过。 |
