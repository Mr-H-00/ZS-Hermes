# OCR 解析目录整体复制

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/workspace/workdir.py

## 问题

OCR 工具按资源清单逐个调用沙盒上传接口，并自行处理取消与回收，掩盖了“把完整解析目录保存到当前 Workdir”的用例。解析产物位于 worker 本地，Project Workdir 已通过 UserWorkspace 与沙盒共享挂载。

## 决策

Workdir 提供从服务临时目录整体复制的能力，复用 Workspace 的 fd-relative、no-follow 边界。目录复制保留嵌套文件和空目录，在 Workspace 父目录的服务私有临时目录准备完整产物，再通过 Linux `renameat2(RENAME_NOREPLACE)` 原子发布；已有目标即使为空也不覆盖。失败回收私有半成品，取消先等待复制结束再回收。OCR 工具调用一次目录复制，返回同一 runtime Markdown 路径，不逐文件调用沙盒上传。

回收校验本次创建目录的 device/inode；其他操作将目录改名或替换后，保留其文件，不扫描工作区追回目录。Markdown 预览在复制前完成，目录复制是工具发布前的最后一次等待。下载和预览取消时等待本地 I/O 完成后释放临时目录。

## 替代方案

- 保留逐文件沙盒上传：共享挂载下产生不必要的远端传输，并让工具拥有文件生命周期细节。
- 在工具里直接使用宿主路径与 shutil.copytree：绕过 owning filesystem boundary。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 一次调用复制完整目录 | 遗漏图片、附属文件或空目录 | Workdir / Workspace | unit 回读全部文件，真实 sandbox 回读共享目录 | 资源不在 ParseResult.resources 中仍完整保留 | Passed |
| 文件归属与回收保持正确 | 跟随符号链接、误删已有或替换目录、取消残留 | Workspace / Workdir | 真实文件 unit | symlink、目标已存在或被替换、复制中途失败与取消、下载及预览取消 | Passed |

- `docker compose exec -e PYTHONDONTWRITEBYTECODE=1 api uv run --no-sync pytest test/unit/toolkits/test_ocr_parse_file_tool.py test/unit/workspace/test_filesystem.py test/unit/workspace/test_workdir.py -q -p no:cacheprovider`：35 passed。
- `docker compose exec -e PYTHONDONTWRITEBYTECODE=1 api uv run --no-sync pytest test/integration/services/test_project_workdir_provisioner.py -k ocr_directory_copy -q -p no:cacheprovider --timeout=90`：1 passed。真实工具解析 DOCX，独立回读宿主目录与沙盒中的 Markdown、相对图片路径和 PNG 像素。
- 后端完整 unit：2280 passed、55 skipped，完整命令与 fixture 修正范围由[文档解析验证](2026-09-30-document-parsing-artifacts.md#验证)记录。

旧能力不存在：OCR 发布路径不再逐文件调用 upload_authorized_file_from_path。

重新引入条件：解析产物与目标不再共享文件系统时，由文件边界拥有明确的目录传输协议。

## 后果

整体复制依赖现有 Linux 容器、共享挂载及同文件系统 staging；不引入新的传输后端、配置或兼容分支。沙盒只挂载 Workspace，不能访问它的父目录或复制半成品。
