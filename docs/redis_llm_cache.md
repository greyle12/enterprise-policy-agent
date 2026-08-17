# Day 23：Redis LLM 响应缓存

Day 22 已经用可重复基准确认：真实模型调用会是端到端路径中最昂贵、最不稳定的外部步骤。
Day 23 在不改变 Agent 业务状态机的前提下，为“完全相同的 LLM 请求”增加可选 Redis
响应缓存，目标是减少重复请求的模型调用次数和等待时间。

这不是 Redis 会话状态，也不会缓存审批提交、SQLite 状态、申请草稿、审计记录或 Web
Search 结果。

## 1. Learn：先理解缓存边界

本实现采用 cache-aside：

```text
LLM 请求
→ 判断是否允许缓存
→ 计算不可逆摘要键
→ Redis GET
→ 命中：直接返回文本
→ 未命中：调用原 LLM
→ 只把成功、非空且大小合规的文本按 TTL 写入 Redis
```

缓存位于统一 `LLMClient` 边界，因此制度回答与意图分类可以共享同一套规则；工具执行、
提交、会话记忆和研究助手不需要知道 Redis 的存在。

Redis 是性能优化，不是业务正确性的来源。Redis 不可用时，系统记录缓存错误并调用原
LLM；FastAPI readiness 仍由应用组件和 SQLite 决定。

## 2. Build：实现内容

### 2.1 文件职责

| 文件 | 职责 |
|---|---|
| `app/cache/models.py` | Provider、状态和进程内指标快照 |
| `app/cache/backends.py` | 禁用后端、异步 Redis 后端、值和键边界 |
| `app/cache/llm.py` | LLM 装饰器、缓存键、绕过、降级和生命周期 |
| `app/api/routes/cache_status.py` | 只暴露安全状态和计数器 |
| `app/api/schemas/cache_status.py` | `/api/v1/cache/status` 响应契约 |
| `scripts/verify_llm_cache.py` | 完全离线的 Day 23 专项验收 |

### 2.2 精确键与自动失效

缓存摘要的规范化输入包括：

```text
缓存协议版本
OpenAI-compatible Base URL 与模型名称的身份摘要
有序的完整 role/content 消息序列
```

最终 Redis 键为：

```text
<LLM_CACHE_NAMESPACE>:<64 位 SHA-256>
```

原始问题、制度上下文和模型地址不会出现在 Redis 键中。消息、系统提示、模型或 Base URL
任意变化都会生成新键；修改 `LLM_CACHE_NAMESPACE` 的版本段可以执行人工全量失效。

制度内容本身位于发送给 LLM 的完整消息中，所以制度内容变更也会自然改变摘要。

### 2.3 写入与绕过规则

| 情况 | 行为 |
|---|---|
| 完全相同、合规的请求 | 允许读写缓存 |
| 消息包含 `api_key=...`、Bearer、密码等凭据形态 | 不访问 Redis，直连 LLM |
| 请求超过 `LLM_CACHE_MAX_REQUEST_BYTES` | 不访问 Redis，直连 LLM |
| LLM 返回空文本或超大文本 | 不写缓存 |
| LLM 抛出异常 | 不写错误或负缓存，原样传播模型异常 |
| Redis GET 失败 | 只调用一次原 LLM，不再尝试本次 SET |
| Redis SET 失败 | 返回已经取得的 LLM 成功答案 |

这组规则避免把凭据形态内容写入 Redis，也避免缓存故障扩大为 Agent 故障。

### 2.4 TTL、内存与部署

默认 TTL 为 600 秒。Compose 中 Redis 使用：

- 固定官方镜像 `redis:8.10.0-alpine`；
- 128 MiB `maxmemory`；
- `allkeys-lru` 淘汰策略；
- 禁用 RDB 与 AOF，因为响应缓存可以丢失和重建；
- 只将端口绑定到宿主机 `127.0.0.1`；
- 只读根文件系统、临时 `/data`，仅保留官方入口降权所需的
  `CHOWN`、`SETGID`、`SETUID` capabilities。

Compose 内的 Agent 通过 `redis://redis:6379/0` 访问缓存。本机 Python 通过
`redis://127.0.0.1:6379/0` 访问同一个 Redis 服务。

### 2.5 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_CACHE_PROVIDER` | `disabled` | 本机默认关闭；Compose 覆盖为 `redis` |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | 支持 `redis://` 或 `rediss://` |
| `REDIS_TIMEOUT_SECONDS` | `0.25` | 缓存连接与读写超时，最多 5 秒 |
| `LLM_CACHE_TTL_SECONDS` | `600` | 1–86400 秒 |
| `LLM_CACHE_NAMESPACE` | `enterprise-policy-agent:llm:v1` | 安全字符组成的键前缀和版本 |
| `LLM_CACHE_MAX_REQUEST_BYTES` | `262144` | 允许缓存的最大消息总字节数 |
| `LLM_CACHE_MAX_VALUE_BYTES` | `262144` | Redis 单条响应上限 |

`REDIS_URL` 可能包含凭据，因此状态 API、指标和日志都不返回该值。

### 2.6 状态与指标

运行后检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/cache/status |
  ConvertTo-Json -Depth 5
```

状态：

| `state` | 含义 |
|---|---|
| `disabled` | 明确关闭缓存，是正常配置 |
| `available` | Redis `PING` 成功 |
| `degraded` | 配置了 Redis，但探针失败；LLM 仍可直连 |

指标为当前 FastAPI 进程启动后的计数：

- `hits`：命中并直接返回；
- `misses`：Redis 正常返回未命中；
- `writes`：成功写入；
- `bypasses`：Provider 关闭、敏感/超大请求或响应不合规；
- `errors`：实际请求路径中的 Redis 读写或缓存数据错误。

状态探针本身不会增加 `errors`，这些计数重启后归零，不是持久化监控系统。

## 3. Test：Windows PowerShell 验收

### 3.1 更新依赖

```powershell
Set-Location D:\Ai_agent_program\demo1
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
& .\.venv\Scripts\python.exe -m pip check
```

### 3.2 Day 23 完全离线专项验收

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_llm_cache
```

它不会连接 Redis、调用真实 LLM 或访问网络，而是验证：精确命中、请求变更失效、600 秒
TTL、键中无原始问题、敏感内容绕过、Redis 故障直连、指标和资源关闭。

成功时顶层输出：

```json
{
  "passed": true,
  "network_calls": false,
  "live_llm_calls": false
}
```

### 3.3 全量代码门禁

```powershell
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_agent_performance
```

### 3.4 Compose 实际运行

确保 `.env` 已有可用 LLM 配置，然后运行：

```powershell
Set-Location D:\Ai_agent_program\demo1
docker compose config --quiet
docker compose up --build --detach --wait
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/health/ready |
  ConvertTo-Json -Depth 5
Invoke-RestMethod http://127.0.0.1:8000/api/v1/cache/status |
  ConvertTo-Json -Depth 5
```

只启动 Redis、供本机 Python 调试：

```powershell
docker compose up --detach redis
$env:LLM_CACHE_PROVIDER = "redis"
$env:REDIS_URL = "redis://127.0.0.1:6379/0"
& .\.venv\Scripts\python.exe -X utf8 -m app
```

## 4. Improve：当前边界与下一步

Day 23 明确没有实现：

- Redis 会话状态或分布式 checkpoint；
- 多实例共享命中率指标；
- 分布式 single-flight / 防缓存击穿锁；
- 语义相似缓存；
- 真实 LLM 成本与延迟节省基线；
- Redis ACL、TLS、托管服务认证和集中监控；
- 对 Redis 中响应正文的应用层加密。

Redis 内的响应正文仍是可读文本，只是键经过哈希。因此生产部署必须使用受限网络、认证、
TLS、最小权限与数据分类策略；高度敏感场景应继续关闭缓存，或增加加密和更严格的内容
分类。

Day 24 已在这套缓存边界之上增加单进程 async single-flight，但没有把它描述成分布式锁。
Day 25 已增加完全离线的并发负载测试，记录吞吐、p95、错误率、上游调用率和放大率；
真实 Provider 基线仍需明确授权后小流量执行。不能用提高预算或吞掉异常来伪造优化结果。
完整边界见 `docs/async_llm_singleflight.md` 与 `docs/async_concurrency_load.md`。

## 5. 参考资料

- Redis 官方 redis-py 指南：<https://redis.io/docs/latest/develop/clients/redis-py/>
- Redis 官方错误处理建议：<https://redis.io/docs/latest/develop/clients/redis-py/error-handling/>
- redis-py 连接文档：<https://redis.readthedocs.io/en/stable/connections.html>
- LiteLLM 缓存配置：<https://docs.litellm.ai/docs/proxy/caching>
