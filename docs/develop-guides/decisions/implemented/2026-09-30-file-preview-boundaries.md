# 文件预览与 Office 转换职责拆分

状态：implemented
类型：architecture
Owner：backend/yuxi/infrastructure/file_preview.py

## 问题

文件预览模块同时拥有公共文件结果、下载使用的 MIME 识别和 LibreOffice 转换。调用方难以区分浏览器展示策略与通用文件能力，HTTP 响应层也从具体实现模块导入结果契约。

## 决策

### 实现方案

`shared/files.py` 拥有 `PreviewResult`、MIME 识别和各入口共用的 30 MB 原文件预览预算；`infrastructure/file_preview.py` 拥有展示类型判断、文本截断和内容准备；`infrastructure/office_conversion.py` 拥有受支持格式判断、LibreOffice 调用、超时和转换异常。Office 转换工具仅接收文件名与字节，不依赖预览策略、业务存储或 HTTP。

Workspace、Knowledge 和 Agent artifact 用例在已有授权读取后调用内容准备或 Office 转换，再由 HTTP 响应层消费共享结果。Workspace 与 MinIO 缓存继续由各自模块拥有。各预览读取入口把预算传给存储边界，格式转换不承担输入大小策略。MinIO 原文件读取使用显式字节上限，元数据大小仅用于提前拒绝，Office 与普通文件统一依赖实际读取上限；派生 PDF 的转换与缓存行为保持不变。保留现有 DOCX/PPTX 支持范围、错误文案、`OFFICE_PREVIEW_TIMEOUT_SECONDS` 环境变量及默认值，不新增命令行入口或兼容转发模块。

## 替代方案

- 保持单模块：改动最少，但公共契约、展示策略与进程副作用继续混合。
- 整体搬到 `shared/files.py`：消除部分引用，但共享契约会承担 LibreOffice 和临时文件执行。
- 提供独立 CLI：支持额外入口，但当前消费者均为后端 Python 用例，增加无明确需求的维护表面。

## 后果

公共文件契约和 Office 转换可以独立调用。各读取入口仍显式传入共同预算，存储实现执行限量读取；底层没有隐含的预览专用上限。Office 缓存命中继续读取已有派生 PDF，预算约束原文件输入，生成的 PDF 大小行为保持既有语义。

内部导入路径与符号名称改变，公开 HTTP、存储路径与缓存策略保持不变。MinIO 原文件大小低估或缺失时，超限输入在实际读取处拒绝，不进入转换或缓存发布。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 公共文件契约与内容准备不依赖转换进程 | 模块拆分后仍加载具体转换实现 | shared/files.py、infrastructure/file_preview.py | test/unit/config/test_preview_owner_boundaries.py | 在解析的依赖集合中注入 subprocess 或转换模块导入，边界断言拒绝 | Passed |
| 转换工具独立处理支持格式、PDF 输出与失败 | 搬迁遗漏错误分支或改变格式范围 | infrastructure/office_conversion.py | test/unit/utils/test_office_conversion.py；真实 LibreOffice 探针 | 非支持格式、缺少执行器、超时、非零退出、缺失或无效 PDF | Passed |
| 超限原文件在实际读取处拒绝 | 无界读取后才检查，或 Office 分支绕过检查 | infrastructure/minio/client.py、modules/knowledge/preview.py | test/unit/storage/test_minio_bounded_download.py、test/unit/knowledge/test_office_pdf_preview.py；真实 MinIO 探针 | 上限加一字节、低估或缺失元数据，验证无转换缓存且源对象未变 | Passed |
| 预览与下载的 HTTP 内容和协议保持一致 | 结果契约迁移遗漏真实路由 | Workspace、Knowledge、artifact 用例及 api/responses/files.py | test/integration/api/test_file_preview_limits.py 及既有 HTTP 预览测试 | 实际 30 MB 加一字节对象、元数据大小为零、原始字节回读与授权拒绝 | Not run |

实际执行：

- `docker compose exec -T api uv run --group test pytest test/unit -m 'not slow' -q`：2354 passed、55 skipped；包含文件预览、MIME、转换、MinIO 清理与超限负向案例。
- 修改范围的 Ruff check 与 format 检查、`git diff --check`、`cd docs && pnpm run build`：通过。
- `python3 -m unittest scripts.test_verify_engineering_contracts`：64 项通过。`python3 scripts/verify_engineering_contracts.py`：被并行任务提案 `proposed/2026-09-30-file-mime-fd-reuse.md` 的 Owner 与证据结果字段格式错误阻塞。
- `docker compose exec -T api uv run python -`：真实 DOCX/PPTX 经 LibreOffice 转为 PDF，使用 pypdf 回读页面文本并归一化字形间空白；真实 MinIO 九字节对象在八字节上限下拒绝、九字节上限下完整返回，源字节保持一致，临时对象已删除并回查不存在。
- `docker compose exec -T api uv run --group test pytest test/integration/api/test_file_preview_limits.py test/integration/api/test_workspace_runtime_cache_api.py test/integration/api/test_viewer_filesystem_router.py test/integration/api/test_chat_router.py::test_thread_artifact_preview_http_preserves_raw_download -q`：命令失败；共享 cleanup fixture 检测到已有非终态测试 Turn，十项测试均在 setup 阶段报错，HTTP 业务断言未执行。不清理其他任务的运行来绕过该检查。

真实 MinIO 和 LibreOffice 探针不能替代被阻塞的 HTTP integration。无 worker、FIFO 或任务发布行为变更，未执行 Agent 主链路 E2E。

