# MinerU 解析器在 405 时回退到任务接口

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/knowledge/parser/mineru.py

## 问题
MinerU 解析配置同时存在自托管地址和官方云端地址。自托管解析器直接调用 `POST /file_parse`，而将官方云 API 的 `/extract/task` 地址填入该配置时，旧实现会继续拼接出无效的 `/extract/task/tasks` 地址并返回 `405 Method Not Allowed`。

## 决策
解析器识别 `mineru.net` 地址时，将 `/extract/task` 归一化为官方 API base，并通过已有的 `mineru_official` 批量上传协议解析；API Key 从官方配置项读取。其他地址继续尝试自托管同步 `POST /file_parse`，明确返回 405 时回退到同一服务的 `/tasks` 并轮询结果。

## 替代方案
1. 保持现状，只把 405 传回调用方。
2. 仅改错误提示，不做接口回退。
3. 只拒绝官方云端地址并要求用户手工迁移配置。

## 后果
同步接口仍是自托管首选，任务接口只在 405 时兜底。官方云端地址兼容转发到已有官方解析器并记录迁移建议，避免历史配置立刻失效；官方解析器负责不同的认证、上传和结果查询协议，长期配置仍推荐使用独立的 `mineru_official` 引擎。

## 验证
- 新增单元测试覆盖同步接口 405 后回退 `/tasks` 的负向场景。
- 新增单元测试覆盖官方云端地址归一化、官方协议转发和 API Key 读取。
- 现有解析结果仍走 zip 解包和 Markdown 提取流程。
