# 文件 MIME 与 fd 复制复用

状态：implemented
类型：simplification
Owner：backend/yuxi/shared/files.py

## 问题

MinIO、Knowledge 和 Viewer 的响应媒体类型判断各自维护后缀规则；Workspace 与共享 Skill artifact 重复维护 fd 复制和部分写入循环。通用实现分散使 fallback 与传输限制容易漂移。

## 决策

### 实现方案

`shared/files.py` 的 `detect_media_type` 拥有签名优先、Office 标准媒体类型、系统后缀识别与 fallback。MinIO 上传、Viewer 下载和 Knowledge 下载使用此入口，显式上传类型及 Knowledge 元数据类型保持优先；这些调用只传文件名，保留原有不嗅探内容的行为。预览可展示类型与 parser 的扩展名能力判断保持独立。

`infrastructure/filesystem.py` 拥有已打开 fd 间的有界复制和完整写入。Workspace 与 Artifact 在各自授权、no-follow 普通文件打开之后调用公共复制；调用方仍拥有 fd 关闭、目标截断与失败清理。传输超限异常在基础设施定义，`workspace/errors.py` 保留当前消费者使用的导入入口，异常映射不变。复制逐块检查累计大小，单次最多读取剩余预算加一字节，源文件增长也不能绕过限额。完整写入处理短写，零进展显式失败。

## 替代方案

- keep：保留重复判断和循环，继续由多个位置维护相同规则。
- narrow（采用）：仅统一 MIME 返回值和 fd I/O；存储访问、权限、路径与 HTTP 错误映射保持在原 Owner。
- replace：统一下载/临时文件生命周期，需要跨入口策略与异步清理抽象，超出当前需求。
- remove：直接依赖标准库可解决部分 MIME 判断，不能覆盖现有签名规则、fallback 和有界复制契约。

## 后果

MIME 的系统映射随平台变化，共享 fallback 覆盖当前存储入口的格式。fd helper 不授予路径权限、不原子发布文件，调用方继续在自己的文件系统边界打开源与目标并清理失败产物。文件大小、唯一文件名与内容哈希函数保持原位置。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| MIME 签名、后缀和 fallback 共用入口，显式类型优先 | fallback 丢失、元数据被覆盖 | shared/files.py 与入口服务 | 下列相关 unit 命令，包含 test_shared_files、test_mime_consumers 与 Viewer service | 无系统 MIME 映射、扩展名与签名冲突、显式类型冲突 | Passed |
| fd 复制完整写入，超限拒绝且不关闭借用 fd | 部分写入丢字节、文件增长绕过限额 | infrastructure/filesystem.py | 下列相关 unit 命令，包含 test_fd_io 与 test_fd_copy_consumers，回读真实临时文件 | 部分写入、写入无进展、复制中源文件增长 | Passed |
| no-follow 与原有异常映射不变 | 抽取绕过链接拒绝、超限被映射成不存在 | Workspace、Artifact | 下列全量 unit 命令，包含 Workspace、sandbox 与 artifact 回归 | 源或目标 symlink、超限映射 413 | Passed |
| 真实 HTTP 与 MinIO 链路保存媒体类型及多块文件内容 | unit 无法证明真实装配 | MinIO、Viewer、Artifact、Knowledge | 下列 integration 命令，9 项均在 session cleanup fixture 报错，测试主体未运行 | 真实跨用户授权与 symlink 拒绝 | Not run |

```bash
docker compose exec -T api uv run --group test pytest test/unit/utils/test_shared_files.py test/unit/utils/test_fd_io.py test/unit/utils/test_mime_consumers.py test/unit/workspace/test_fd_copy_consumers.py test/unit/workspace/test_filesystem.py test/unit/services/test_viewer_filesystem_service.py test/unit/services/test_artifact_service.py -q
docker compose exec -T api uv run --group test pytest test/unit -m 'not slow' -q
docker compose exec -T api uv run --group test pytest test/integration/api/test_file_mime_fd_reuse.py test/integration/api/test_viewer_filesystem_security.py test/integration/api/test_skill_artifact_authorization.py -q
python3 scripts/verify_engineering_contracts.py
python3 -m unittest scripts.test_verify_engineering_contracts
cd docs && pnpm run build
git diff --check
```

相关 unit 为 100 passed；全量 unit 为 2388 passed、55 skipped。HTTP/MinIO integration 被环境中已有非终态测试 Turn 阻塞；保留清理 guard，不修改其他任务的运行状态。E2E 未执行，同一环境阻塞与证据缺口仍存在。unit 结果不替代真实 HTTP 或 E2E。

旧能力不存在：MinIO 私有 MIME helper、Knowledge/Viewer 的重复 MIME fallback、Workspace 私有完整写入函数与 Artifact 重复复制循环均被删除，源码搜索确认这些表面不存在。

重新引入条件：真实消费者需要不同的媒体类型策略或非 fd 传输语义，且提供独立契约与测试。
