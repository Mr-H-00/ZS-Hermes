# 网页搜索

Yuxi 可以为 Agent 注册 `web_search` 工具，从外部网页获取实时信息。当前支持豆包和 Tavily；搜索结果属于外部来源，不能自动替代知识库或人工核验。

## 配置供应商

在 `.env` 或容器环境中选择一个供应商并提供对应凭证：

```bash
WEB_SEARCH_PROVIDER=doubao
DOUBAO_SEARCH_API_KEY=<your-doubao-key>
```

或：

```bash
WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=<your-tavily-key>
```

`WEB_SEARCH_PROVIDER` 只接受 `doubao` 和 `tavily`。留空时，系统会按已配置的 API Key 自动选择；两个 Key 都配置时，优先选择豆包。没有有效 Key 时不会注册 `web_search` 工具。

密钥只放在受保护的环境变量或密钥管理器中，不要写进 Skill、Agent 用户环境、代码或日志。

## 让 Agent 使用

搜索工具在 API 和 worker 进程加载时注册。修改环境变量后，重新创建相关容器：

```bash
docker compose up -d --force-recreate api worker
```

进入“智能体”配置，确认 `web_search` 已在工具或 Skill 依赖中可用。内置 `deep-research` Skill 使用这个统一工具名；如果 Agent 使用显式 Skills 列表，需要选择对应 Skill。

## 验证

1. 打开智能体详情，确认工具列表中出现“网页搜索”。
2. 用一个需要最新网页信息的问题发起真实对话。
3. 在消息工具调用中确认使用的是 `web_search`，并检查返回的 URL、标题和摘要。
4. 对重要结论回到原网页核实，不要只依据搜索摘要。

没有工具时，检查 `WEB_SEARCH_PROVIDER` 拼写、对应 Key 是否存在，以及 API/worker 是否已经重新创建。调用失败时查看 worker 日志和供应商响应；不要把完整请求头或密钥贴出来。

实现入口：[网页搜索工具](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/toolkits/buildin/tools.py)。
