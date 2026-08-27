# 内容审查

Yuxi 可以在 Agent 运行前后检查内容。关键词检查适合快速拦截已知词，LLM 审查适合补充更复杂的语义判断，但会增加模型调用和响应时间。

## 配置

Web 设置中的内容审查区只对 `superadmin` 显示。超级管理员进入“设置 → 基本设置”后配置。直接读取 `/api/system/config` 只需要登录，更新配置的接口才需要管理员认证；通过 API 修改内容审查时，应遵循同样的权限管理策略。

| 配置项 | 作用 |
| --- | --- |
| `enable_content_guard` | 开启内容审查总开关 |
| `enable_content_guard_llm` | 在关键词检查之外启用 LLM 审查 |
| `content_guard_llm_model` | 选择审查使用的模型 |

LLM 审查只有在总开关开启且配置了审查模型时才会参与。审查模型应与普通对话模型分开评估其延迟和费用。

## 检查时机

开启总开关后：

1. 用户输入在进入 Agent 前先做检查；
2. 流式输出期间检查关键词；
3. 输出结束后，在启用 LLM 审查时检查完整内容。

内容被拦截时，运行会返回内容审查错误。它不是模型连接失败，也不能只根据页面的 HTTP 状态码判断审查是否发生。

## 维护关键词

关键词文件位于：

```text
backend/package/yuxi/config/static/bad_keywords.txt
```

一行填写一个关键词，从首列开始的 `#` 行会被忽略；带有前导空格的 `#` 不属于注释格式。API 和 worker 在进程启动时读取这份文件；修改后重新创建或重启相关容器：

```bash
docker compose up -d --force-recreate api worker
```

先在测试环境验证误拦截，再把词表用于生产。词表本身不是完整的内容安全策略，仍需结合业务规则、权限和人工复核。

## 验证和排查

用测试账号分别发送普通内容和应被拦截的测试内容，检查运行结果和 API/worker 日志。LLM 审查还要确认：

- `content_guard_llm_model` 是可用的聊天模型；
- 模型供应商凭证存在；
- API/worker 可以访问模型服务；
- 响应延迟符合预期。

不要在日志或 Issue 中贴出包含真实敏感数据的原文。配置项由 [`options.py`](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/config/options.py) 定义，检查逻辑由 [`guard.py`](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/utils/guard.py) 和聊天服务负责。
