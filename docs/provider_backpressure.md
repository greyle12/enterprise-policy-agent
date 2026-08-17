# Day 27：LLM Provider 并发门禁与背压

Day 25 已用唯一键扇出证明：single-flight 只合并相同请求，无法限制不同请求同时进入模型
Provider。Day 26 处理了 Embedding/Reranker 的批量调用；Day 27 回到交互式 LLM 边界，增加
进程内统一并发上限、FIFO 有界队列、排队超时和安全的 503 过载响应。

功能默认关闭。`4 / 16 / 2s` 只是可见的起始配置，不是生产容量结论；启用前仍需根据真实
Provider 配额、延迟和错误率采集小流量基线。

## 1. Learn：背压不是无限等待

当进入速度长期高于 Provider 完成速度时，只增加异步 Task 会把压力转移到进程内存、连接池
和客户端等待时间。背压需要明确三个边界：

| 边界 | 回答的问题 | Day 27 行为 |
|---|---|---|
| `max_concurrency` | 最多同时执行多少次真实 LLM 调用 | 超出后进入 FIFO 队列 |
| `max_queue` | 最多允许多少个请求等待 | 队列已满时立即返回过载错误 |
| `queue_timeout_seconds` | 单个请求最多等待多久 | 超时后移出队列并返回稳定错误 |

有限队列让系统在容量不足时快速、可预测地失败。它不能增加 Provider 的实际吞吐，但可以避免
无界排队把短时拥塞放大为进程失稳。

### 1.1 为什么门禁放在缓存和 single-flight 之后

运行链路为：

```text
请求
→ Redis cache-aside
→ 相同 cache miss 的 single-flight
→ Provider 并发门禁
→ OpenAI-compatible LLM
```

因此：

- cache hit 不占用 Provider permit；
- 相同请求只有 single-flight leader 进入门禁；
- follower 等待 leader 的共享结果，不占用队列位置；
- 不同请求和缓存绕过请求仍受统一 Provider 容量边界保护。

若把门禁放在缓存外层，cache hit 和 follower 会错误占用稀缺 permit，降低有效容量。

## 2. Build：实现内容

### 2.1 文件职责

| 文件 | 职责 |
|---|---|
| `app/llm/concurrency.py` | FIFO 有界门禁、超时、取消、关闭排空、状态和指标 |
| `app/api/routes/provider_status.py` | `/api/v1/provider/status` 安全运维端点 |
| `app/api/provider_errors.py` | 将本地容量错误映射为不泄露请求数据的 HTTP 503 |
| `scripts/verify_provider_backpressure.py` | 完全离线的容量、溢出、超时、取消和关闭验收 |
| `tests/unit/test_provider_concurrency.py` | 并发竞态、FIFO、资源归还和生命周期测试 |
| `tests/unit/test_provider_cache_composition.py` | 验证 cache hit 与 follower 不消耗 Provider 容量 |

### 2.2 FIFO 与容量语义

当执行槽已满时，请求按照进入顺序持有独立 waiter。只有队首且存在空闲执行槽时才能启动。
队列长度检查、入队、出队和当前执行数修改都在同一个 `asyncio.Condition` 下完成，避免先检查
后入队造成超卖。

`max_queue=0` 表示不等待：执行槽满后立即拒绝。启用时同时存在的已接纳请求上限为：

```text
max_concurrency + max_queue
```

### 2.3 超时、取消与关闭

- 排队超时会从 FIFO 队列移除 waiter，并唤醒后继请求；
- 排队客户端取消只清理自己的 waiter；
- 执行中的客户端取消或上游异常都会在 `finally` 中归还执行槽；
- 应用关闭时先拒绝新请求和唤醒队列，再等待执行中请求结束，最后只关闭一次上游客户端。

队满、排队超时和关闭分别使用稳定错误码：

```text
llm_provider_overloaded
llm_provider_queue_timeout
llm_provider_limiter_closed
```

直接 API 调用返回 HTTP 503，响应只包含固定错误码和固定提示，不拼接原始问题或 Provider
异常正文。Agent 内部只读工具仍可由现有有界容错层分类和重试。

## 3. Measure：配置与可观测性

### 3.1 环境变量

| 环境变量 | 默认值 | 限制 | 含义 |
|---|---:|---:|---|
| `LLM_PROVIDER_LIMIT_ENABLED` | `false` | 布尔值 | 是否启用进程内门禁 |
| `LLM_PROVIDER_MAX_CONCURRENCY` | `4` | `1..256` | 同时执行的真实 LLM 调用上限 |
| `LLM_PROVIDER_MAX_QUEUE` | `16` | `0..4096` | FIFO 等待请求上限 |
| `LLM_PROVIDER_QUEUE_TIMEOUT_SECONDS` | `2` | `(0, 60]` | 单个请求最大排队秒数 |

默认关闭用于保持升级兼容，也避免在没有真实 Provider 基线时把示例数字伪装成容量结论。

### 3.2 状态端点

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/provider/status |
  ConvertTo-Json -Depth 5
```

状态值：

| `state` | 含义 |
|---|---|
| `disabled` | 门禁关闭，请求兼容直通 |
| `available` | 已启用且仍有执行或排队容量 |
| `queuing` | 已有请求按 FIFO 等待 |
| `saturated` | 执行槽和等待队列都已满 |
| `closed` | 应用正在关闭或已经关闭 |

端点提供 `in_flight`、`queued`、配置边界以及以下进程内计数：

- `requests`、`accepted`、`started`、`completed`；
- `bypassed`、`failed`、`rejected`、`timed_out`、`cancelled`；
- `peak_in_flight`、`peak_queued`、`average_wait_ms`。

这些字段不包含 prompt、响应、API key、Base URL 或 Provider 异常正文，也不是跨进程聚合指标。

## 4. Test：Windows PowerShell 验收

### 4.1 安装更新后的项目

```powershell
Set-Location D:\Ai_agent_program\demo1
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
& .\.venv\Scripts\python.exe -m pip check
```

### 4.2 Day 27 完全离线专项验收

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_provider_backpressure
```

成功时关键结果为：

```json
{
  "passed": true,
  "capacity_metrics": {
    "requests": 5,
    "accepted": 4,
    "completed": 4,
    "rejected": 1,
    "peak_in_flight": 2,
    "peak_queued": 2
  },
  "network_calls": false,
  "live_llm_calls": false
}
```

脚本还验证排队超时、排队取消、关闭资源和关闭门禁时的兼容直通，全程不连接 Redis、公网
或真实 LLM。

### 4.3 全量质量门禁

```powershell
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_ci_configuration
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_llm_cache
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_async_singleflight
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_provider_backpressure
```

### 4.4 Docker 实际验收

补丁只把 Compose 镜像标签更新为 `enterprise-policy-agent:day27`，不修改 Dockerfile 或本地
PyTorch wheel。Docker Desktop Linux Engine 启动后执行：

```powershell
docker info
docker compose config --quiet
docker compose --progress plain build agent

if ($LASTEXITCODE -ne 0) {
    throw "Day 27 Agent 镜像构建失败，请保留输出"
}

docker compose up --detach --wait
docker compose ps
```

要观察门禁行为，先在 `.env` 显式设置 `LLM_PROVIDER_LIMIT_ENABLED=true`，再重建 Agent 容器。

## 5. Improve：如何选择真实参数

真实参数应从授权的小流量 Provider 基线开始，至少观察：

- Provider 429 / 5xx 和连接错误率；
- LLM 完整响应 p50、p95 与生成长度；
- `queued`、`peak_queued`、`average_wait_ms` 和超时率；
- FastAPI worker 数和每个 worker 的独立门禁；
- 上游官方并发、RPM、TPM 与连接池限制。

Little's Law 可用于初步核对：稳定状态下的并发约等于吞吐乘以平均响应时长，但最终值必须用
真实流量验证。扩大队列不会提高吞吐，只会提高可等待请求数和尾延迟。

Day 27 仍未实现：

- 多进程或多实例共享的全局 Provider 配额；
- Redis/数据库分布式 permit；
- 按租户、模型或优先级隔离的多个队列；
- Prometheus / OpenTelemetry 指标导出和告警；
- 生产 soak test、自动调参和自适应并发；
- 真实 Provider 基线。

因此本实现应描述为“单进程统一 LLM 容量边界”，不能描述为跨实例生产级全局限流。
