# 后端 Python 行宽与阅读布局

状态：implemented
类型：process
Owner：backend/pyproject.toml

## 问题

120 行宽让普通调用出现只折开括号、多个参数仍挤在一行的布局。代码需要足够的横向空间，也需要按语义分段。

## 决策

### 实现方案

Ruff 将后端 Python 最大行宽设为 140，整个 `backend/` 使用同一格式器。清楚的短调用保持一行；需要折行时按参数分行，用尾逗号保留有意义的参数分组。长 SQL、说明字符串与 prompt 在自然边界使用等价的字面量连接，保持内容不变。格式器原有行为与配置项不增加其他开关。

[贡献指南](../../contributing.md#后端)拥有仅适用于后端 Python 的换行、空行与注释约定，`backend/AGENTS.md`指向该规则。不同语义阶段留空行；必要注释说明非显然的事务时序、Owner、路径边界或调用义务。Review 核对调用布局与这些约束，Ruff 验证格式和行宽。

## 替代方案

保留 120 会继续产生短调用折行；取消行宽会放任横向阅读成本。全局忽略尾逗号会把清楚的数据与参数分组一起收紧，采用默认 Ruff 行为并修正具体不自然的调用。

## 后果

机械排版产生较大差异，以单独提交供审阅。140 是上限，阅读时仍根据表达式复杂度保留分组。规则只覆盖 `backend/` Python；行为、字符串与参数顺序保持一致，docstring 内容也保持一致。

## 验证

- 全部 700 个 tracked 后端 Python 文件与 `07925b96` 逐个比较完整 AST：0 个差异，docstring 与其他字符串都按完整值比较，无归一化例外。调用布局扫描未发现只折开外层括号、多个参数仍挤在一行的调用。
- `backend/.venv/bin/ruff format backend --check`：707 个文件无需改动；140 行宽下生产代码 Ruff lint 与导入排序均通过。检查范围包含本地未跟踪的 Python，工作区改动只来自受版本控制的源码。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow"`：140 行宽版本 2593 passed、55 skipped、7 subtests passed。
- 工程契约检查通过，检查器单测 64 tests 通过；`pnpm --dir docs run build` 与相对链接检查通过。生产多参数调用布局检查未发现只折外层括号、参数仍挤在一行的情况。全新独立 Reviewer 核对完整格式差异、Owner、排版约定及 AST 等价性后通过。
