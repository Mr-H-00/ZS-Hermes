# Milvus 数据库选择使用显式连接别名

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/knowledge/implementations/milvus.py

## 问题

Milvus 知识库和图向量存储都使用自定义 connection alias 连接 Milvus，但数据库枚举、
创建和选择操作没有传入该 alias。删除知识库时图向量存储初始化会访问默认连接，
在未创建默认连接的进程中记录 `should create connection first` 警告。

## 决策

Milvus 主知识库与图向量存储初始化数据库时，均向 `db.list_database()`、
`db.create_database()` 和 `db.using_database()` 传入各自的 `connection_alias`。缺失图
collection 和缺失知识库 collection 仍按原有幂等删除语义处理。

## 替代方案

- 创建或复用全局默认连接：拒绝，会让不同 Milvus 使用方共享隐式状态。
- 只修复图向量存储：拒绝，主知识库初始化存在相同 alias 漏传问题。
- 删除数据库选择逻辑：拒绝，会改变当前多 database 部署契约。

## 后果

Milvus 数据库操作绑定到当前组件自己的连接别名，删除知识库时不再因为默认连接缺失产生误导性
warning。真实 Milvus 连接失败仍由连接调用和后续 collection 操作暴露。

## 验证

- 单元测试覆盖主知识库和图向量存储初始化时数据库操作携带显式 `using` alias。
- 真实 Milvus integration 需在 Docker 可连接环境中执行。
