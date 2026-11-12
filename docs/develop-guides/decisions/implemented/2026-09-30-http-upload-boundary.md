# HTTP 上传对象的适配边界

状态：implemented
类型：architecture
Owner：backend/yuxi/api/uploads.py

## 问题

HTTP 上传适配需要与附件、工作区用例及对象存储分离，避免业务输入依赖 FastAPI，并保留大文件的限量流式写入与隔离。

## 决策

UploadFile 的消费限定在 API 层。文件授权、目标位置、批次规则、内容校验和最终保存由业务服务与 owning filesystem boundary 执行。全面移除服务中的 HTTPException、改变公开端点与上传协议不在本决定范围内。

### 实现方案

`api/uploads.py` 将小文件限量读成 bytes，路由显式提取文件名与内容类型交给附件服务或 MinIO 图片工具。附件与图片业务仍在产生存储副作用前检查大小，各业务 Owner 拥有自己的限制常量。

工作区与 Viewer 上传复用 FastAPI 已暂存的二进制文件，API 按实际文件长度校验并复位，通过 `shared/files.py` 的 FileInput 交给服务。请求拥有输入流，服务只在请求生命周期内消费，不保存或关闭流，也不接收宿主路径。服务继续执行目录授权、批次规则与冲突映射；Workspace 在 no-follow 边界累计检查实际读取大小，先写同目录临时文件，再原子发布，失败时回收未发布内容。Workdir 把浏览 scope 映射到同一边界。原有受信任路径复制入口继续服务内部消费者，复用流式写入实现。线程消费通过 await_io 等待完成，取消后才把输入流生命周期交还请求。

## 替代方案

- 保留跨层 UploadFile：实现方便，但业务输入继续依赖 HTTP 框架。
- 在路由完成存储：混合协议适配与业务授权。
- 全部转成 bytes：工作区单文件允许 100 MiB，增加峰值内存。
- 再创建服务暂存副本：增加磁盘写入及清理义务；请求的 SpooledTemporaryFile 已满足输入暂存需要。

## 后果

小文件采用显式值参数，大文件保留中立借用流输入。普通服务无需取得临时宿主路径，工作区上传减少一份临时磁盘副本。既有路径复制能力保留给当前内部消费者。

API 在写入前拒绝包含超限文件的整批请求，Viewer 不再为这种批次留下前序合法文件；Viewer 的其他逐文件发布语义保持不变。Viewer 超限返回 400。取消等待当前写入线程完成，已发布的文件仍遵循原有逐文件副作用语义。

## 验证

- Passed：`docker compose exec -T api uv run --group test pytest test/unit -m "not slow" -q`，2305 passed、55 skipped；覆盖 HTTP multipart 适配、真实大小与伪造 size、边界限量、失败清理、no-clobber、symlink 和取消时的输入流所有权。跳过项保留测试原有条件，不计为通过。
- Passed：`docker compose exec -T api uv run --group test pytest test/integration/api/test_upload_boundary.py test/integration/api/test_viewer_filesystem_router.py test/integration/api/test_viewer_filesystem_security.py -q`，12 passed；真实 HTTP 回读 4 MiB 二进制文件、MinIO 附件与图片、头像持久字段，验证冲突、目录/链接边界和跨用户拒绝。首轮曾被环境中的非终态测试 Turn 阻挡，最终复跑完成。
- Passed：`docker compose exec -T api uv run --group test pytest test/e2e/test_agent_lifecycle_e2e.py::test_attachment_survives_run_runtime_recreation -q`，1 passed；附件经实际 Run 后，释放并重建沙盒仍可读取并由 Artifact 回读。
- 本轮复验：相关 unit 与上述 12 项 HTTP integration 通过；附件生命周期 E2E 为 1 failed、1 error，清理 Agent 超时且 teardown 检测到测试 Turn 仍为 running。该失败的根因未确认，本轮不将 E2E 计为通过。
- 静态边界 oracle 位于 `backend/test/unit/routers/test_upload_boundary.py`，任何非 API 层 UploadFile 使用及旧基础设施上传模块都会失败；负向行为由真实超限、虚假 metadata、中途读取失败、已有文件及 symlink 案例证明。
- Not run：全量 integration/E2E 与外部 OCR、真实模型探针；本决定改变上传输入适配，验证覆盖相关上传链路与确定性附件生命周期。
