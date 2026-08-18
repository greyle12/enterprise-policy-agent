# Day 28：请求关联与运行时可观测性

Day 22–27 已建立性能、缓存、并发、批处理和 Provider 背压证据，但这些能力仍分散在专项
报告与状态端点中。Day 28 增加统一 HTTP 请求关联、结构化访问日志、有界进程内指标以及
Prometheus 兼容导出，让一次 API 调用可以被安全定位和量化。

实现不新增第三方运行依赖，不部署 Prometheus 或 Grafana，也不把进程内计数描述成跨实例
生产监控。

## 1. Learn：日志、指标和链路追踪解决不同问题

| 信号 | 回答的问题 | Day 28 范围 |
|---|---|---|
| 日志 | 某一次请求发生了什么 | JSON 访问事件与稳定错误事件 |
| 指标 | 一段时间内系统整体如何 | 请求数、状态、并发和延迟直方图 |
| Trace | 一次请求跨服务经过了哪些 Span | 尚未实现 |

只有日志时，很难快速判断错误率和尾延迟；只有指标时，又无法关联某一次失败。Day 28 使用
`X-Request-ID` 把客户端响应、处理函数和日志连接起来，再用低基数指标观察整体趋势。

## 2. Build：实现内容

### 2.1 文件职责

| 文件 | 职责 |
|---|---|
| `app/observability/middleware.py` | 请求 ID、完整请求计时、路由模板提取和安全访问事件 |
| `app/observability/metrics.py` | 线程安全、有界路由键的 HTTP 计数与延迟直方图 |
| `app/observability/logging.py` | 单行 JSON formatter 和 Uvicorn 日志配置 |
| `app/observability/prometheus.py` | Prometheus text format 0.0.4 序列化 |
| `app/api/routes/observability.py` | JSON 状态与 `/metrics` 抓取端点 |
| `app/api/runtime_errors.py` | 带请求 ID、无异常正文的稳定 HTTP 500 |
| `scripts/verify_runtime_observability.py` | 完全进程内的 Day 28 安全与指标验收 |

### 2.2 请求 ID

每个 HTTP 请求都会获得一个关联 ID：

- 合法 `X-Request-ID` 最长 64 个字符，只允许字母、数字及 `._:-`；
- 缺失、空白、换行、超长或其他非法值会被替换为 `req_<32位十六进制>`；
- ID 写入 `request.state.request_id`；
- 所有正常、校验失败、Provider 过载和未处理异常响应都返回 `X-Request-ID`；
- 未处理异常响应正文也返回同一个 ID，便于用户向运维人员提供关联信息。

请求 ID 只是关联标识，不应放置账号、Token、问题正文或其他业务数据。

### 2.3 低基数 HTTP 指标

指标使用 FastAPI 匹配后的路由模板：

```text
/probe/employee-927  → /probe/{item_id}
未匹配的任意路径     → __unmatched__
```

原始路径参数、query string、请求体、响应体、客户端 IP 和 User-Agent 都不会成为标签。正常
路由键最多保存 64 个；超出后统一映射为 `__overflow__`，防止攻击者或错误动态路由耗尽内存。

延迟从进入 ASGI middleware 开始，到响应体发送完成或异常结束为止，使用固定累计 bucket：

```text
5ms, 10ms, 25ms, 50ms, 100ms, 250ms,
500ms, 1s, 2.5s, 5s, 10s, +Inf
```

记录内容包括：

- 已完成请求数；
- 当前与峰值 HTTP 并发；
- `1xx`–`5xx` 状态分类；
- 每个路由模板的累计请求、总延迟、平均值、最大值和直方图；
- 被合并到 overflow 的请求数；
- 指标内部不变量错误数。

健康检查、JSON 状态和 `/metrics` 自身不参与 HTTP 指标，避免探针流量污染业务趋势，也避免
每次抓取改变下一次抓取结果。

### 2.4 结构化日志与安全 500

`python -m app` 现在关闭 Uvicorn 原始 access log，避免默认日志写入带 query string 的 URL；
项目 middleware 改为输出单行 JSON：

```json
{
  "event": "http_request_completed",
  "request_id": "req_...",
  "method": "POST",
  "route": "/api/v1/policy-answers",
  "status_code": 200,
  "duration_ms": 18.42,
  "outcome": "success"
}
```

访问事件只使用白名单字段。未处理异常只记录 `error_type`，不记录 `str(error)`、Traceback、
请求正文或 Provider 响应正文；客户端收到固定 `internal_server_error` 和请求 ID。

`LOG_LEVEL` 已成为受校验配置，支持：

```text
DEBUG, INFO, WARNING, ERROR, CRITICAL
```

大小写会自动规范化，未知值会阻止服务以错误配置启动。

## 3. Measure：端点与 Prometheus 指标

### 3.1 JSON 状态

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/observability/status |
  ConvertTo-Json -Depth 8
```

该端点适合开发调试，返回完整进程内路由快照。它不包含原始 URL、问题文本、凭据或异常正文。

### 3.2 Prometheus 抓取

```powershell
Invoke-WebRequest http://127.0.0.1:8000/metrics |
  Select-Object -ExpandProperty Content
```

Content-Type 为：

```text
text/plain; version=0.0.4; charset=utf-8
```

主要指标：

| 指标 | 类型 | 含义 |
|---|---|---|
| `enterprise_policy_agent_http_requests_total` | Counter | method/route/status_class 请求数 |
| `enterprise_policy_agent_http_request_duration_seconds` | Histogram | 路由延迟分布 |
| `enterprise_policy_agent_http_requests_in_flight` | Gauge | 当前 HTTP 并发 |
| `enterprise_policy_agent_http_requests_peak` | Gauge | 进程启动后的峰值并发 |
| `enterprise_policy_agent_http_route_overflow_requests_total` | Counter | 路由标签溢出请求 |
| `enterprise_policy_agent_llm_provider_in_flight` | Gauge | 当前真实 LLM 调用 |
| `enterprise_policy_agent_llm_provider_queued` | Gauge | Day 27 FIFO 等待数 |
| `enterprise_policy_agent_llm_provider_events_total` | Counter | 接纳、完成、拒绝、超时等事件 |

Prometheus 示例查询：

```promql
sum(rate(enterprise_policy_agent_http_requests_total{status_class="5xx"}[5m]))
/
sum(rate(enterprise_policy_agent_http_requests_total[5m]))
```

```promql
histogram_quantile(
  0.95,
  sum by (le, route) (
    rate(enterprise_policy_agent_http_request_duration_seconds_bucket[5m])
  )
)
```

仓库没有部署 Prometheus 服务，以上查询需要在真实监控平台中配置抓取后使用。

## 4. Test：Windows PowerShell 验收

### 4.1 安装与静态检查

```powershell
Set-Location D:\Ai_agent_program\demo1
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\python.exe -m ruff check .
```

### 4.2 Day 28 完全离线专项验收

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_runtime_observability
```

成功时关键结果为：

```json
{
  "passed": true,
  "schema_version": "1.0",
  "requests_total": 3,
  "tracked_route_keys": 2,
  "network_calls": false,
  "live_llm_calls": false
}
```

专项脚本验证合法/非法请求 ID、成功和失败请求、路由模板、500 脱敏、监控端点不自计数、
Prometheus 格式以及结构化日志白名单。

### 4.3 全量质量门禁

```powershell
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_ci_configuration
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_provider_backpressure
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_runtime_observability
& .\.venv\Scripts\python.exe -X utf8 -m scripts.run_golden_evaluation --mode offline
```

### 4.4 Docker 验收

Compose 镜像标签更新为 `enterprise-policy-agent:day28`。Docker Desktop Linux Engine 启动后：

```powershell
docker info
docker compose config --quiet
docker compose --progress plain build agent

if ($LASTEXITCODE -ne 0) {
    throw "Day 28 Agent 镜像构建失败，请保留输出"
}

docker compose up --detach --wait
docker compose ps
Invoke-WebRequest http://127.0.0.1:8000/metrics |
  Select-Object -ExpandProperty Content
```

## 5. Improve：当前边界

Day 28 已完成安全的单进程运行时观测基础，但仍未实现：

- Prometheus Server、Grafana Dashboard 和告警规则；
- 多 worker、多容器或多机器的指标聚合；
- 日志采集、集中存储、检索和保留策略；
- OpenTelemetry Trace、Span 和跨服务上下文传播；
- CPU、内存、事件循环延迟和连接池指标；
- 身份认证、网络白名单或独立管理端口；
- SLO、错误预算和基于真实流量的告警阈值。

`/metrics` 虽不包含请求正文和凭据，生产部署仍应通过内网、反向代理或监控网络限制访问。
进程重启会清空全部计数；多个 Uvicorn worker 各自维护独立指标，不能直接当作全局值。
