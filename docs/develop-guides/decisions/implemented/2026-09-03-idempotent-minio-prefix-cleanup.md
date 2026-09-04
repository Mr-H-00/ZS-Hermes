# 知识库对象前缀清理保持幂等

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/storage/minio/client.py

## 问题

删除知识库时会清理 `knowledgebases` 和 `kb-images` 中对应知识库 ID 的对象前缀。
如果某个桶尚未创建、被外部删除或存储卷发生更换，MinIO 会返回 `NoSuchBucket`，
导致知识库删除流程中断，数据库记录无法完成清理。

## 决策

`adelete_objects_by_prefix()` 将 `NoSuchBucket` 视为该桶没有可清理对象，返回已删除数量
`0` 并继续调用方流程。其他 S3 错误仍然抛出 `StorageError`，避免隐藏真实的存储故障。

## 替代方案

- 在每次删除知识库前创建缺失桶：拒绝，会产生无意义的存储桶并增加删除副作用。
- 在知识库删除流程中单独跳过 `kb-images`：拒绝，会把同一存储语义复制到业务层。
- 保持现状：拒绝，空的或已丢失的可选图片桶会继续阻塞知识库元数据删除。

## 后果

知识库删除对缺失对象桶具有幂等性；如果桶本身被意外删除，删除操作不会伪装成恢复
存储数据，后续上传仍会通过现有的 `ensure_bucket_exists()` 创建桶。

## 验证

- 单元测试覆盖 `NoSuchBucket` 返回 `0` 的负向回归场景。
- 真实 MinIO integration 尚未执行：当前 Docker Desktop Docker API 不可连接。
