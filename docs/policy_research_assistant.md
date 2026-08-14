# Day 21：受控制度研究助手

## 1. 目标

Day 21 完成第 3 周的整合项目：把已有企业制度 RAG 与可选 Web Search 组合成
`PolicyResearchAssistant`。

研究助手用于回答“内部制度怎么规定，同时公开资料有什么参考”一类研究问题。它不是
新的审批入口，也不会让外部网页参与材料、审批、草稿或提交判断。

## 2. 设计边界

```text
当前问题
  ├── 内部制度 RAG（始终执行、业务依据） → S1 / S2 / ...
  └── Web Search（双重显式授权、仅供参考） → W1 / W2 / ...
```

外部搜索必须同时满足：

1. API 请求设置 `include_web=true`；
2. 服务端设置 `WEB_SEARCH_PROVIDER=tavily` 并提供 `TAVILY_API_KEY`。

任一条件不满足时，不会产生外部网络调用。

## 3. 为什么不直接并入事务办理 LangGraph

现有 LangGraph 保存草稿、确认、审批和提交等事务状态。公开网页不是企业内部制度，
不能成为这些高影响动作的规则来源。

Day 21 因此提供独立、无状态的研究 API：

- 复用同一个内部 `PolicyAnswerService`；
- 复用 Day 20 的有界重试、超时与安全错误；
- 与草稿 checkpoint、审批规则和提交幂等键隔离；
- 通过结构化 `source_policy` 明确来源权威边界。

## 4. API

### 4.1 只查内部制度

```http
POST /api/v1/research/answers
Content-Type: application/json
```

```json
{
  "question": "差旅住宿费如何报销？"
}
```

`include_web` 默认是 `false`。

### 4.2 显式补充外部公开资料

```json
{
  "question": "对比内部差旅凭证要求和公开规则",
  "include_web": true
}
```

响应关键字段：

```json
{
  "assistant": {
    "name": "policy_research_assistant",
    "version": "1.0"
  },
  "status": "completed",
  "internal_sources": [
    {"source_id": "S1", "document_title": "差旅报销管理制度"}
  ],
  "external_sources": [
    {"source_id": "W1", "title": "公开指南", "url": "https://example.gov.cn"}
  ],
  "source_policy": {
    "internal_policy_authoritative": true,
    "external_web_advisory": true,
    "external_web_used_for_workflow": false
  },
  "web_search": {
    "requested": true,
    "executed": true,
    "provider": "tavily",
    "status": "completed",
    "query_redacted": false,
    "query_truncated": false,
    "result_count": 1
  }
}
```

## 5. 状态语义

| `status` | 含义 |
|---|---|
| `completed` | 内部制度有引用依据，且请求的外部搜索也成功返回资料 |
| `partial` | 至少一个来源分支可用，但内部无依据、外部关闭、无结果或失败 |
| `unavailable` | 内部制度分支失败，且没有任何可用外部资料 |

外部搜索自己的 `web_search.status`：

| 值 | 含义 |
|---|---|
| `not_requested` | 客户端没有请求 Web Search |
| `disabled` | 客户端请求了，但服务端 Provider 未启用；没有网络调用 |
| `completed` | 搜索成功并返回至少一条资料 |
| `no_results` | 搜索成功但没有可用结果 |
| `failed` | 搜索在有界重试后仍失败 |

## 6. 数据外发保护

发送给 Web Search 的内容：

- 只来自当前 `question`；
- 不包含 Day 19 对话历史；
- 不包含内部制度检索结果；
- 不包含身份上下文、申请草稿或审批状态；
- 会脱敏常见 API Key、Token、Bearer Token、Password、密码和 Secret；
- 最多 500 字符。

API 只返回 `query_redacted` 和 `query_truncated`，不回显实际外发查询。

外部结果最多 5 条；每条标题最多 200 字符、摘要最多 600 字符。只接受 HTTP/HTTPS
URL，并按 URL 去重。

## 7. 提示注入边界

Tavily 返回的标题和摘要被视为不可信数据：

- 不会作为 system prompt；
- 不会再次送入 LLM 合成；
- 不会进入意图分类器；
- 不会进入办理 LangGraph；
- 不会改变内部制度回答或来源编号。

研究助手使用确定性模板分栏展示内部结论和外部摘要。

## 8. Tavily Provider

Day 21 直接使用 `httpx` 调用 Tavily Search HTTP API，不新增 SDK 依赖。请求使用：

```text
POST https://api.tavily.com/search
Authorization: Bearer <TAVILY_API_KEY>
```

并显式限制：

```json
{
  "topic": "general",
  "search_depth": "basic",
  "include_answer": false,
  "include_raw_content": false,
  "include_images": false,
  "max_results": 3
}
```

API 契约参考 [Tavily 官方 Search API 文档](https://docs.tavily.com/documentation/api-reference/endpoint/search)。

## 9. 配置

默认安全配置：

```dotenv
WEB_SEARCH_PROVIDER=disabled
TAVILY_API_KEY=
WEB_SEARCH_TIMEOUT_SECONDS=10
WEB_SEARCH_MAX_RESULTS=3
```

启用 Tavily：

```dotenv
WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=tvly-your-key
WEB_SEARCH_TIMEOUT_SECONDS=10
WEB_SEARCH_MAX_RESULTS=3
```

`WEB_SEARCH_MAX_RESULTS` 只允许 1 到 5。选择 `tavily` 但密钥为空时，应用会在启动前
拒绝无效配置。

外部搜索还受 Day 20 配置控制：

```dotenv
AGENT_SAFE_TOOL_TIMEOUT_SECONDS=65
AGENT_TOOL_MAX_ATTEMPTS=3
AGENT_RETRY_MIN_WAIT_SECONDS=0.1
AGENT_RETRY_MAX_WAIT_SECONDS=1.0
```

HTTP 429、5xx、连接错误和超时可以有界重试；不可信响应结构或 HTTP 4xx 不自动重试。

## 10. PowerShell 验收

完全离线专项验收，不需要 LLM Key、Tavily Key 或网络：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_policy_research
```

预期关键结果：

```text
"passed": true
"assistant_version": "1.0"
"local_only_did_not_call_web": true
"internal_sources": ["S1"]
"external_sources": ["W1"]
"query_redacted": true
"web_attempts": 3
"recovered": true
"sensitive_value_not_exposed": true
```

调用本地 API：

```powershell
$body = @{
    question = "对比内部差旅凭证要求和公开规则"
    include_web = $true
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/research/answers `
  -ContentType "application/json" `
  -Body $body
```

## 11. 当前非目标

Day 21 暂不实现：

- 抓取或保存网页全文；
- 外部资料的 LLM 二次总结；
- 外部网页缓存；
- 域名白名单或企业代理网关；
- Web 来源质量自动评分；
- 把外部资料写入企业知识库；
- 让公开网页参与审批或提交决策。
