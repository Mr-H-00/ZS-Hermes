# OpenCode 会话路由

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agents/models.py

## 问题

OpenCode Go 缺少 x-opencode-session 请求头时返回 MissingSessionID，阻断模型调用。

## 决策

模型加载器仅为 OpenCode 与 OpenCode Go 附加 Yuxi User-Agent 和稳定的 x-opencode-session。主模型、动态模型和摘要器传入当前 Thread ID；无 Thread 的独立操作使用模型实例级随机 ID。调用方其他请求头保留，不改变凭据或已保存配置。

## 替代方案

全局 ID 会混用不同对话的路由，每次请求随机生成则失去会话连续性。Thread ID 与独立模型实例分别拥有对应会话。

## 后果

会话头只发送给 OpenCode 系列供应商，其他供应商协议和持久化保持不变。

## 验证

真实 SDK 与 HTTP MockTransport 测试覆盖流式/非流式请求头、同 Thread 稳定性、独立模型隔离、其他供应商不加头，以及调用方已有请求头保留。主 Agent、子 Agent 与摘要模型装配测试验证 Thread ID。测试位于 test_model_selectors.py 与 test_summary_graph_config.py。
