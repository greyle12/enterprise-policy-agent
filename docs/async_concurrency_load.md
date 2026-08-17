# Day 25：异步并发负载与吞吐证据

Day 24 已证明相同 cache miss 可以通过 single-flight 合并，但“12 个相同请求只调用一次
LLM”仍然只是并发正确性契约，不是吞吐量或生产 SLA。Day 25 增加受控负载测试，分别测量
热点键、混合热点和唯一键扇出，输出端到端 p95、吞吐、错误率、上游调用率与放大率。

所有默认验收均完全离线，不连接 Redis、真实 LLM 或公网，也不会消耗模型额度。

## 1. Learn：并发正确不等于容量足够

### 1.1 延迟与吞吐是两个维度

- 延迟回答“单个客户端等了多久”；
- p95 表示 95% 请求在该时长以内完成；
- 吞吐回答“单位时间内完成多少请求”；
- 并发数表示同一时刻允许多少客户端任务进入执行区。

Day 25 的请求计时从所有任务同时收到开始信号时开始，因此包含客户端并发信号量中的等待。
当 24 个唯一请求以并发 12 执行时，后 12 个请求需要等待第一批释放位置，p95 会体现这段
排队时间，而不是只记录 Provider 内部的固定延迟。

### 1.2 single-flight 只合并相同键

三种请求分布的差异为：

| 场景 | 24 个请求对应的唯一键 | 预期上游调用 | 观察重点 |
|---|---:|---:|---|
| `hot_key_burst` | 1 | 1 | 相同请求合并与后续 cache hit |
| `mixed_hotset` | 4 | 4 | 每个键各一个 leader，不同键保持并发 |
| `unique_key_fanout` | 24 | 24 | 无法去重时的 Provider 并发扇出 |

single-flight 不是全局限流器。唯一键场景中，上游峰值可以达到客户端配置并发；这正是后续
评估 Provider 并发门禁时必须看到的数据。

### 1.3 两个上游指标

```text
上游调用率 = 上游调用数 / 客户端请求数
上游调用放大率 = 上游调用数 / 唯一请求键数
```

- 调用率越低，表示缓存和请求合并节省的模型调用越多；
- 放大率的理想值是 `1.00x`；
- 放大率大于 `1.00x` 表示同一唯一键产生了重复上游调用；
- 调用率不能单独判断正确性：唯一键场景的合理调用率就是 100%。

## 2. Build：实现内容

### 2.1 文件职责

| 文件 | 职责 |
|---|---|
| `app/performance/concurrency.py` | 并发起跑、客户端并发边界、逐请求计时与场景汇总 |
| `app/performance/concurrency_offline.py` | 三种隔离的缓存/LLM fixture 和固定异步 I/O |
| `app/performance/models.py` | 负载样本、场景结果和总报告模型 |
| `app/performance/reporting.py` | 原子写入 JSON / Markdown 并发报告 |
| `scripts/run_concurrency_load_test.py` | 可调请求数、并发和离线 I/O 的报告入口 |
| `scripts/verify_concurrency_load.py` | 固定 24 请求、并发 12 的 Day 25 专项验收 |
| `tests/unit/test_concurrency_load.py` | 并发上限、排队计时、错误脱敏和资源关闭测试 |

### 2.2 同时起跑与有界客户端任务

Runner 先创建全部 Task，再用同一个 `asyncio.Event` 放行。每个请求在进入
`asyncio.Semaphore` 之前开始计时，因此报告包含排队；进入信号量后维护当前活跃数和峰值，
并在 `finally` 中归还位置。

场景异常不会中断其他样本。报告只记录 `ValueError` 等异常类型，不记录异常正文，避免把
Provider 返回、问题内容或凭据写进长期性能证据。

### 2.3 场景隔离

每个请求分布都创建独立的：

- 内存缓存协议替身；
- 固定延迟的异步 LLM 协议替身；
- `CachedLLMClient`；
- single-flight 注册表和指标。

场景结束后先读取指标，再关闭所有资源。因此热点场景写入的缓存不会让唯一键场景意外命中。

## 3. Measure：报告与质量门禁

每个场景记录：

- 请求数、唯一键数和客户端并发；
- 最小值、平均值、p50、p95 和最大延迟；
- 总耗时与 requests/second；
- 错误数量、错误率和稳定错误类型；
- 客户端与 Provider 峰值并发；
- 预期/实际上游调用、调用率与放大率；
- cache hit 和 single-flight follower 数。

Day 25 的确定性质量门禁检查：

```text
所有请求成功
AND 实际上游调用 == 每个场景的唯一键数
AND 客户端峰值 <= 配置并发
AND Provider 峰值 <= 配置并发
```

这里没有设置固定吞吐或 p95 上限，因为不同电脑和 CI Runner 的调度速度不同。报告用于同一
环境中的趋势比较；不能通过复制其他机器的绝对数字定义本机失败。

### 3.1 当前工程决策

离线证据确认：

- 热点请求会被正确合并；
- 不同键仍能并发；
- 唯一键会将上游峰值推到客户端并发上限。

但固定延迟 fixture 无法模拟真实 Provider 的 429 配额、Token 生成速度、连接池和公网
抖动。因此 Day 25 不凭模拟结果写死一个全局 LLM 并发数字。当前决策是先保留可重复的负载
工具，再在用户明确同意消耗额度时采集小流量真实基线，之后才设置 Provider 并发门禁、队列
长度和排队超时。

## 4. Test：Windows PowerShell 验收

### 4.1 安装更新后的项目

```powershell
Set-Location D:\Ai_agent_program\demo1
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
& .\.venv\Scripts\python.exe -m pip check
```

### 4.2 Day 25 完全离线专项验收

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_concurrency_load
```

成功时关键字段为：

```json
{
  "passed": true,
  "request_count": 24,
  "configured_concurrency": 12,
  "network_calls": false,
  "live_llm_calls": false
}
```

三个场景的关键上游调用应分别为：

```text
hot_key_burst      = 1
mixed_hotset       = 4
unique_key_fanout  = 24
```

### 4.3 生成 JSON 与 Markdown 报告

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.run_concurrency_load_test `
  --requests 24 `
  --concurrency 12 `
  --provider-latency-ms 15
```

输出文件：

```text
artifacts/performance/agent-concurrency-load-report.json
artifacts/performance/agent-concurrency-load-report.md
```

### 4.4 全量质量门禁

```powershell
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_async_singleflight
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_concurrency_load
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.run_concurrency_load_test `
  --requests 24 `
  --concurrency 12 `
  --provider-latency-ms 15
```

### 4.5 Docker 实际验收

补丁不修改本地 PyTorch wheel 或 Dockerfile。确认 Docker Desktop Linux Engine 已启动后：

```powershell
docker info
docker compose config --quiet
docker compose --progress plain build agent

if ($LASTEXITCODE -ne 0) {
    throw "Day 25 Agent 镜像构建失败，请保留输出"
}

docker compose up --detach --wait
docker compose ps
```

## 5. Improve：Day 26/27 进展与剩余边界

Day 25 已完成的是安全、完全离线、可在 CI 重复的并发负载证据，不是生产压测。仍未实现：

- 真实 DeepSeek/OpenAI-compatible Provider 的授权小流量基线；
- 固定 QPS 或持续数分钟的 soak test；
- 跨 FastAPI 进程聚合指标；
- 多进程或多实例共享的 Provider 全局配额；
- Redis 分布式 single-flight；
- Prometheus / OpenTelemetry 指标；
- Embedding 和 Reranker 批处理。

Day 26 已完成 Embedding/Reranker 批处理优化：建立批量接口与等价性契约，并比较逐条和
批量执行的 Provider 调用、内部批次与吞吐；没有把 LLM 请求拼成不安全的大批次。详见
`docs/embedding_reranker_batching.md`。

Day 27 已在 cache 和 single-flight 之后增加单进程 LLM Provider 并发门禁、FIFO 有界队列、
排队超时、取消清理和安全 503。默认保持关闭，示例参数不代替真实 Provider 基线；详见
`docs/provider_backpressure.md`。

## 6. 关联知识

- Little's Law：并发、吞吐和平均响应时间之间的容量关系；
- backpressure：下游容量不足时限制进入速度，而不是无限堆积 Task；
- semaphore：限制同一时间进入临界 I/O 区域的协程数量；
- p95：关注尾部请求体验，不能只看平均值；
- load shape：热点键、混合热点和唯一键代表不同的真实流量分布；
- coordinated omission：负载工具漏记排队时会低估尾延迟；Day 25 从排队前开始计时以避免它。

参考：

- Python `asyncio` 同步原语：<https://docs.python.org/3/library/asyncio-sync.html>
- Python `asyncio` Task：<https://docs.python.org/3/library/asyncio-task.html>
