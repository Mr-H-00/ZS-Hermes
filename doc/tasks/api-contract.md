# HTTP API 契约

状态：已完成

目标：把知识库创建、更新、入库、重切、查询的请求和响应字段补齐到同一套契约里。

## 最小任务
- [x] 梳理并固定 `create / update / documents / reslice / query / query-params` 的 DTO。
- [x] 让 `parent_child` 和 `embedding_features` 贯穿请求、响应和序列化。
- [x] 让查询配置响应支持 `visible_when`。
- [x] 补 API integration，覆盖正常回读和参数拒绝。
- [x] 补一个负向用例：非 BGE-M3 传 sparse 配置被拒绝。

## 完成标准
- [x] 前端和后端对同一字段有同一份解释。
- [x] 不满足条件的配置不能落库。
- [x] 旧接口调用不需要改动就能继续工作。
