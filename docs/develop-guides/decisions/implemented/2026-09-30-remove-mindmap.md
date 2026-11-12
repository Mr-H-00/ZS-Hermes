# 移除知识库思维导图

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/knowledge/manager.py

## 问题

思维导图能力横跨知识库详情界面、普通与 Public API、Agent 工具、知识库持久化字段及文件删除联动，形成独立的维护面。

## 决策

知识库详情、普通与 Public API、Agent 工具、生成逻辑、响应投影和 ORM 字段全部移除。现有数据库中的旧列不再由 Yuxi 映射或读取，本决定不修改数据库 Schema。知识图谱和其他知识库工具保持现有能力。历史 changelog 和归档决策保留为历史记录。

### 实现方案

知识库路由删除导图 endpoint 与文件变更联动，KnowledgeBase Manager/read model/serializer 删除导图字段，模型不再定义这三列；不新增 Schema 迁移。知识库工具注册和 Skill 移除 `get_mindmap`。Web 删除导图 API、弹窗、组件、渲染器、Markmap 依赖和主题变量。测试只覆盖保留的知识库能力；活动说明移除当前功能描述。

## 替代方案

- 保留：避免兼容变化，但继续承担完整功能面。
- 只移除 UI：保留 API、Agent 工具和数据库状态，未达到整体移除目标。
- 窄化能力：仍维护独立的生成、存储和调用链，当前没有明确 consumer 要求。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 前后端及 Agent 入口均不可用 | 仍注册页面、工具或 endpoint | Web、API 路由与工具注册 | 活动源码搜索；Public API integration 负向案例 | 搜索到任一活动入口即失败；已删除路由返回 404 | Inspected；integration 未运行 |
| 运行时不再读取导图字段 | ORM、read model 或响应继续暴露导图字段 | Knowledge 模型与 Manager | 模型和服务源码搜索 | 活动模型或响应仍有导图字段即失败 | Inspected |
| 当前说明不再声称能力存在 | README、Skill 或 API 文档保留现状描述 | 活动文档 | 活动文档搜索 | 命中当前功能说明即失败 | Inspected；docs build 未运行 |

旧能力不存在：当前运行时、API、工具、UI、ORM/read model 和活动文档均不再包含 mindmap 能力。旧数据库遗留列不被映射或读取。

重新引入条件：出现明确用户场景与负责维护完整前后端、持久化和验证链路的提案。

## 后果

旧数据库可能仍保留不再映射的导图列；运行时不读取这些列，也不执行兼容或数据迁移。知识图谱继续由独立的图谱能力拥有。历史 changelog 与归档决策保留为历史事实，不表示运行时仍支持。
