# MinIO 首次并发上传的 Bucket 创建幂等

状态：implemented
类型：bug-fix
Owner：backend/yuxi/infrastructure/minio/client.py

## 问题

空对象存储中两个上传同时检查到 Bucket 不存在，先创建者成功，后创建者收到 `BucketAlreadyOwnedByYou`。客户端把后者转换为上传失败，导致一次批量上传只有部分文件保存。

## 决策

### 实现方案

只在 `make_bucket` 的异常边界接受 `BucketAlreadyOwnedByYou`，继续已有策略设置和对象上传。该结果证明 Bucket 已归当前账号所有。存在检查、其他创建错误和策略设置失败仍通过 `StorageError` 上报，不接受 `BucketAlreadyExists` 或权限错误。

修复放在 MinIOClient，知识库、附件和图片的上传共同使用该边界；不增加进程锁、分布式锁或启动期创建清单。

## 替代方案

- 进程锁不能覆盖多个 API/Worker，分布式锁增加没有必要的共享状态。
- 启动时预建 Bucket 不能保证后续动态 Bucket 或存储重置后的正确行为。
- 接受所有创建错误会掩盖权限、所有者与网络失败。

## 后果

创建幂等由存储服务的明确响应证明，可以覆盖多个客户端和进程，不依赖本地锁。私有 Bucket 不增加公开策略；公开 Bucket 即使由另一请求创建，仍经过现有策略配置。用户已有对象和 Bucket 不做迁移。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 两个首次上传都保存正确字节 | 存在检查与创建竞态 | MinIOClient | 真实 MinIO 并发上传、重新下载对象 | 两个独立客户端都检查到不存在，后创建者实际收到 BucketAlreadyOwnedByYou；旧实现稳定失败 | Passed |
| 幂等创建仍执行公开策略，其他错误拒绝 | 捕获范围过宽或提前返回 | MinIOClient | unit 故障注入 | 创建 AccessDenied/BucketAlreadyExists、策略 AccessDenied、存在检查错误 | Passed |
| 知识库 HTTP 双文件上传保存完整对象 | 只看 HTTP 200 未核对存储 | knowledge/management 上传入口 | HTTP 并发上传后逐个回读对象，核对大小与哈希 | 只返回成功但未写对象会在下载断言失败 | Passed |

- 修复前运行新增 7 项 unit：3 failed、4 passed；真实 MinIO 竞态 integration 在第二个上传结果处失败，错误为 BucketAlreadyOwnedByYou。测试仅同步真实检查与创建时序，不伪造存储响应。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit/storage/test_minio_bucket_creation.py test/unit/storage/test_minio_public_images.py test/unit/storage/test_minio_bounded_download.py test/unit/storage/test_minio_image_content.py test/unit/utils/test_mime_consumers.py -q -p no:cacheprovider`：35 passed。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_minio_bucket_creation.py test/integration/api/test_upload_boundary.py::test_concurrent_knowledge_uploads_preserve_both_files -q -p no:cacheprovider`：2 passed。

新测试使用独立临时 Bucket 并清理对象，不重置用户的知识存储。HTTP 验证使用现有 Bucket，空桶首次竞态由独立客户端 integration 覆盖；未为测试删除用户的知识 Bucket。仅接受创建动作明确返回的当前所有者成功结局，不重试其他错误。
