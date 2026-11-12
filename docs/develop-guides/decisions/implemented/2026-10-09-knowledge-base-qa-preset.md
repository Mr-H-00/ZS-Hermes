# 知识库问答预置角色

状态：implemented
类型：feature
Owner：backend/yuxi/modules/agents/presets/knowledge_base_qa.py

## 问题

用户需要直接选择一个已预加载知识库技能的问答智能体。

## 决策

### 实现方案

新增 `knowledge-base-qa` 角色，名称为“知识库问答”，复用 `ChatbotAgent`，将 `skills` 和 `preload_skills` 均设为 `["knowledge-base"]`。默认提示词、知识库选择与授权沿用现有运行时。preset discovery 经初始化服务与 repository 首次落库，已有同 slug 配置继续保留。

## 替代方案

手动配置智能体不能满足内置入口需求；新增执行后端或复制技能到提示词会增加重复维护，故采用现有 preset 与预加载机制。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 自动发现知识库问答并启用、预加载知识库技能 | 模块遗漏或只启用未预加载 | preset 与 discovery | `docker compose exec api uv run --no-sync --group test pytest test/unit/agents/test_builtin_discovery.py test/unit/agents/test_context_auth.py test/unit/agents/skills/test_skill_runtime.py -q` | 删除角色或预加载配置会使断言失败 | Passed |

## 后果

复用现有预加载和权限机制，无新增 guard 或权限扩展；完整模型问答效果不由配置测试证明。

验证命令与观察范围：

- 上述相关单测：55 passed。
- `docker compose exec api uv run --no-sync --group test pytest test/unit -m "not slow" -q`：2563 passed、55 skipped；跳过项不计为通过。
- `docker compose exec api uv run --no-sync --group test pytest test/integration/services/test_builtin_discovery.py -q`：3 passed，覆盖真实 PostgreSQL 落库、配置保留与 HTTP 可见性。
- 运行实例数据库只读回查：`knowledge-base-qa` 已落库，名称、后端、内置标记与两项技能配置符合定义。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`：通过，后者 64 tests。
- `cd docs && pnpm run build`：通过，包含相对链接检查。
- 标准 `uv run --group test` 命令在测试收集前因容器 `yuxi.egg-info` 写入权限失败，验证使用 `--no-sync` 复用已安装依赖。
- Ruff 检查未执行成功：容器未安装 `ruff`。真实模型问答未执行；本变更复用现有执行链路，不宣称模型回答质量已验证。
