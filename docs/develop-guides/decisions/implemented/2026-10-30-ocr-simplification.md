# OCR 配置、解析结果与知识文件执行归属收敛

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/documents/service.py

## 问题

OCR 配置在适配器、文档服务与前端重复解释。解析协议中的截断和缺字段响应可能被发布为成功正文。知识任务先保存参数再领取文件执行权，使失败的竞争者能改变当前解析的参数记录；索引发布没有与解析发布一致地核验任务租约。

## 决策

### 实现方案

documents 拥有引擎配置绑定和有效选项解析，复用 system 的持久配置、脱敏及环境优先级，并向前端提供配置目录。前端默认选择来自服务端，保存成功后更新共享状态；基本设置保存期间禁用数字输入、显示保存反馈并阻止重入，失败后保留旧值并恢复交互；配置存在与服务可用分别展示。保留已有引擎 ID 和配置键，不新增动态连接管理或配置迁移。

parser 继续将本地文件转换为完整 Markdown 目录；引擎负责自身协议完整性，拒绝明确截断或缺失必要结果结构，合法空白结果仍允许。本地模型初始化串行化，图像直接传入引擎，删除无消费者参数、不可达分支和临时图片往返。

知识文件领取事务同时确定执行参数，worker 不预先修改文件。解析和索引发布按一致锁序复核 Job lease 与 File owner；产物发布、索引代次、撤销和授权仍归各自现有 Owner。共享解析服务不解释知识库、作业或索引状态。

## 替代方案

- 保留现状：维护重复配置与参数解释，不能解决并发记录失真。
- 缩窄现有模块：复用当前配置、解析产物、任务租约和文件代次，是采用的方案。
- 替换为通用插件与轮询框架：没有减少现有协议差异，增加维护表面。
- 删除产物、路径与执行权边界：会破坏当前知识库、附件和工具消费者，拒绝。

## 后果

连接地址与凭据由 documents 的 Option 绑定读取，适配器消费已解析的值；DeepSeek 继续由模型供应商模块拥有凭据，适配器不再自行读取同名环境变量。部署级模型目录和 MinerU 传输超时保留既有环境配置。新增引擎需要声明协议适配器、能力元数据及配置绑定，知识库、附件与工具消费者不增加引擎分支；同协议多连接的动态管理不在该决定范围内。

普通解析和索引任务的参数在领取文件时合并，worker 不再预写；重新解析的撤销事务保留自身的参数持久化边界。索引发布除文件归属外，还要求作业处于 running、执行者一致且租约有效；等待文件锁后重新检查时钟。畸形或截断的外部响应显式失败，不能用空结果掩盖协议故障；MinerU 归档存在空白正文文件时允许发布，缺正文文件仍失败。引擎缓存只在装配期间持锁，网络解析和模型推理保持在锁外。

## 验证

- 后端完整 unit：`docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow" -q --tb=short -p no:cacheprovider`，3397 passed、125 skipped；跳过项不计为通过。
- PostgreSQL/HTTP：`test/integration/services/test_chunk_config_boundary.py` 16 passed；`test/integration/services/test_knowledge_projection_integrity.py` 20 passed。使用隔离 Compose 的 PostgreSQL、Redis 与独立测试 Schema，回读参数、状态、索引代次及 outbox。
- OCR 及产物 E2E：`test/e2e/test_ocr_config_center_e2e.py` 与 `test/e2e/test_document_parsing_artifacts_e2e.py`，5 passed。经真实 HTTP、worker、数据库、对象和文件回读验证 RapidOCR 文本、MinerU 协议参数、正文/图片完整目录，以及 Input 消费后取消或归档 Turn、再显式删除附件的清理；MinerU 使用 HTTP 协议回放服务。两文件由 `.github/workflows/system-tests.yml` 显式执行。
- 负向验证：旧实现的截断/缺字段响应、临时图像往返、参数预写和失租发布均由对应案例拒绝。冷缓存去锁变体返回不同实例，测试失败；将租约检查移到文件锁之前的变体在真实等锁场景失败。前端恢复旧组件后，迟到默认、空选择和健康标签测试失败；移除选择事件、重挂载条件、输入禁用、保存锁或容器样式绑定时，相应真实模板负向测试失败。
- 前端：`docker compose exec -T frontend pnpm run lint:check`、`test:unit`、`build` 通过，unit 为 972 passed；完整运行期间调整了基本设置的容器样式绑定，最终版本的相关 7 项定向测试另行通过。真实浏览器验证配置目录、默认值保存及后端回读；注入 HTTP 500 验证保存失败保留旧值，健康协议替身验证“已配置，未验证”标签；延迟基本设置请求验证输入禁用、重复按键只发送一次，失败后恢复原值和交互。验证后恢复配置并移除替身。
- 静态检查：工程契约验证器、其 56 项 unittest、后端 Ruff 检查/格式/导入检查、`git diff --check` 通过。文档执行 `node node_modules/vitepress/bin/vitepress.js build`，构建及相对链接检查通过。
- 独立 Reviewer 完成需求、源码、测试与文档审查，冷缓存装配竞争、租约测试同步、基本设置保存期间重复提交及 MinerU 空白正文误判均已修正，复审没有阻断问题。

旧能力不存在：生产代码搜索 `_ocr_processor_kwargs`、`resolve_ocr_task_params`、`OCR_OPTION_KEYS` 和 `update_file_params` 无结果；RapidOCR 临时 PNG 包装、PP-Structure 不可达输入包装和多余健康预检、Paddle 私有未读参数及前端默认值中转状态已删除。有持久协议消费者的旧字段过滤继续保留。

重新引入条件：出现明确消费者、独立验证及负责生命周期的 Owner 后单独裁决。动态多连接管理需独立说明持久配置身份、授权和迁移。

未验证范围：真实云端 OCR 与 GPU 服务的识别质量、性能和供应商线上协议；本地结果不替代外部服务探针，也不代表远程 CI 已执行。
