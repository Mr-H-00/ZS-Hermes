# 图片回退使用已启用且授权的 OCR 工具

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/runtime/middlewares/model_input.py

## 问题

ImageInputCompatibilityMiddleware 声明 tools 会让 create_agent 无条件向模型注册 OCR 工具，绕过智能体的工具选择。模型拒绝图片时，回退若读取授权过滤前的工具集，也可能再次生成已被隐藏的 OCR 调用。

## 决策

### 实现方案

中间件不声明工具，OCR 与其他工具共同使用现有配置和 Skill 装配。chatbot/graph.py 将图片中间件放在 RuntimeAuthorizationMiddleware 之内，读取与模型调用相同的 request.tools；模型明确拒绝图片时，只有本次请求已启用 OCR 才生成 OCR tool_call，否则返回不可用说明。实际工具执行继续经过授权和审批边界。

after_model 仍先复查授权，再产生审批等待点；网络重试保持在授权与图片处理之外，每次重试重新授权。同步和异步图片回退使用相同工具判定。

## 替代方案

- 保留无条件注册、只在工具执行时拒绝，会向模型展示不可用能力并产生无意义调用。
- 再从配置或权限快照独立判定会重复解释授权，无法保证与本次实际请求一致。

## 后果

OCR 未启用或被运行时授权隐藏时，图片回退明确说明当前能力。启用且可用时保留既有回退。其他供应商错误继续抛出，不将坏图片或网络错误解释为模型不支持图片。

## 验证

同步／异步 unit 覆盖启用和禁用两种工具集。生产中间件装配的组合测试独立记录最终模型请求工具，与回退生成的调用比较，覆盖注册了工具但授权过滤掉它的情况。授权、重试、工具异常隔离和构图的既有 unit 同时验证。相关 unit 集合 79 passed。独立进程恢复旧装配后，组合用例 1 failed、1 passed，失败对应模型工具已被授权隐藏但回退仍生成调用。未调用外部模型供应商或真实 ARQ Agent 链路。

关联修复：[xerrors/Yuxi PR #1093](https://github.com/xerrors/Yuxi/pull/1093)。
