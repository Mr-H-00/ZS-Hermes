# 配置规范化

状态：已完成

目标：把 BGE-M3、Parent-Child、向量融合和 RRF 的配置入口统一成可校验、可回读的最小规则。

## 最小任务
- [x] 定义 `additional_params`、`processing_params`、`query_params.options` 的最终字段结构。
- [x] 实现 BGE-M3 sparse、Parent-Child、向量融合、RRF 的显隐与默认值合并。
- [x] 把任务级参数覆盖知识库默认值的优先级写死。
- [x] 补单测覆盖合法值、越界值和非 BGE-M3 拒绝 sparse 的分支。

## 完成标准
- [x] 后端只暴露最终生效配置，不泄露未生效字段。
- [x] 非法组合在校验层直接失败。
- [x] 旧配置仍能被稳定读取。
