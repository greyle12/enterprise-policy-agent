# Day 24：异步 LLM single-flight

Day 22 用离线基准确认 LLM 是最昂贵的外部 I/O；Day 23 为完全相同的合规请求增加了
Redis cache-aside。Day 24 继续处理一个只有在并发下才会出现的问题：缓存为空时，多个相同
请求可能同时读取到 miss，然后重复调用同一个 LLM。

Day 24 使用进程内异步 single-flight，把同一缓存键的并发未命中请求合并为一次上游调用。

## 1. Learn：async 不等于自动去重

`async def` 让一个线程在等待网络 I/O 时可以处理其他协程，但不会自动识别“这些协程正在
做同一件事”。没有协调时，12 个同时到达的相同问题仍可能产生 12 次模型调用：

```text
12 个相同请求
→ 12 次 Redis GET
→ 12 个 cache miss
→ 12 次 LLM 请求
→ 12 次 Redis SET
```

single-flight 使用缓存摘要作为不含原始问题的协调键：

```text
12 个相同请求
→ Redis GET 均未命中
→ 第 1 个请求创建 leader Task
→ 其余 11 个请求成为 follower
→ 只执行 1 次 LLM 请求和 1 次 Redis SET
→ 12 个请求收到同一结果
```

不同摘要不会互相等待，因此不同问题仍然可以并发执行。

## 2. Build：实现内容

### 2.1 文件职责

| 文件 | 职责 |
|---|---|
| `app/cache/singleflight.py` | 有容量上限的异步任务注册表、角色和关闭逻辑 |
| `app/cache/llm.py` | 在合规 cache miss 后调用 single-flight，并记录指标 |
| `app/cache/models.py` | single-flight 状态与进程内指标 |
| `app/api/routes/cache_status.py` | 暴露安全的运行状态，不暴露缓存键或问题正文 |
| `scripts/verify_async_singleflight.py` | 完全离线并发专项验收 |
| `tests/unit/test_async_singleflight.py` | 去重、取消、异常、容量和关闭测试 |

### 2.2 leader、follower 与 overflow

| 角色 | 行为 |
|---|---|
| `leader` | 为某个摘要创建共享 `asyncio.Task`，调用 LLM 并写一次缓存 |
| `follower` | 通过 `asyncio.shield()` 等待已有 Task，不重复调用 LLM |
| `overflow` | 注册表已达到不同键上限，新键独立执行，不阻塞在无关请求后面 |

默认最多跟踪 128 个不同的在途键。该上限约束的是进程内注册表大小，不是全局 Provider
并发上限。overflow 选择保持服务可用，并通过指标暴露；Day 25 再依据负载测试决定是否需要
Provider 并发门禁和排队超时。

### 2.3 取消和异常语义

follower 等待共享 Task 时使用 `asyncio.shield()`：

- 一个 HTTP 客户端断开，只取消它自己的等待；
- 共享 LLM 调用继续服务其他等待者；
- leader 成功后仍只写一次缓存；
- 上游异常会传播给当时所有等待者；
- 失败 Task 会从注册表删除，下一次请求可以重新尝试；
- 应用关闭时主动取消仍在运行的共享 Task，再关闭 Redis 和 LLM 客户端。

这不会吞掉模型异常，也不会写入负缓存。

### 2.4 安全边界

single-flight 只在以下条件全部满足时启用：

1. Redis 缓存 Provider 已启用；
2. 消息通过 Day 23 的格式、大小和敏感内容检查；
3. 已生成不可逆 SHA-256 摘要；
4. Redis 未返回缓存命中。

敏感内容、超大请求、非法消息和明确关闭的缓存仍直接调用上游，不进入共享注册表。注册表
只保存摘要和 Task，不保存额外的原始问题副本。

## 3. Configure：配置与观测

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `LLM_SINGLEFLIGHT_ENABLED` | `true` | 是否合并同进程内相同 cache miss |
| `LLM_SINGLEFLIGHT_MAX_KEYS` | `128` | 同时跟踪的不同摘要上限，范围 1–4096 |

`GET /api/v1/cache/status` 新增：

| 字段 | 含义 |
|---|---|
| `singleflight_enabled` | 当前缓存 Provider 是否启用了协调 |
| `singleflight_max_keys` | 进程内注册表容量 |
| `singleflight_in_flight` | 查询状态时仍在运行的共享 Task 数 |
| `metrics.coalesced` | 已成功复用 leader 结果的 follower 数 |
| `metrics.singleflight_overflows` | 因不同键容量已满而独立执行的请求数 |

这些值都是单个 FastAPI 进程的运行时状态，重启后归零，不等同于多实例监控。

## 4. Test：Windows PowerShell 验收

### 4.1 安装更新后的项目

```powershell
Set-Location D:\Ai_agent_program\demo1
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
& .\.venv\Scripts\python.exe -m pip check
```

### 4.2 Day 24 完全离线专项验收

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_async_singleflight
```

脚本同时发起 12 个相同请求，另外验证取消隔离和不同键并发。它不访问 Redis、真实 LLM
或网络。成功时关键输出为：

```json
{
  "passed": true,
  "concurrent_requests": 12,
  "upstream_calls": 1,
  "coalesced_requests": 11,
  "cache_writes": 1,
  "network_calls": false,
  "live_llm_calls": false
}
```

### 4.3 全量质量门禁

```powershell
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_llm_cache
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_async_singleflight
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_agent_performance
```

### 4.4 运行时状态

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/cache/status |
  ConvertTo-Json -Depth 5
```

并发请求结束后，正常状态通常满足：

```text
singleflight_enabled = true
singleflight_in_flight = 0
metrics.coalesced >= 0
metrics.singleflight_overflows = 0
```

## 5. Improve：当前边界与 Day 25

Day 24 明确没有实现：

- 跨 FastAPI 进程或跨容器的分布式 single-flight；
- Redis 分布式锁；
- 不同请求之间的全局 LLM 并发上限；
- 排队长度、排队超时和拒绝策略；
- 生产压测或真实 Provider 吞吐量基线；
- 语义相似请求合并。

Day 25 应使用受控负载测试测量并发吞吐、p95、上游调用放大率和错误率，再决定 Provider
并发上限、排队超时及多实例方案，不能只凭感觉设置并发数字。

## 6. 关联知识

- coroutine：调用异步函数后产生、需要被等待的协程对象；
- Task：由事件循环调度、可以被多个等待者观察结果的协程执行单元；
- `asyncio.Lock`：只保护很短的注册表读写，不包住网络 I/O；
- `asyncio.shield`：隔离等待者取消与共享 Task 取消；
- `asyncio.gather`：并发启动并收集多个独立协程的结果；
- cache stampede：大量请求在同一缓存项失效或不存在时同时打到上游。

参考：

- Python `asyncio` Task：<https://docs.python.org/3/library/asyncio-task.html>
- Python 异步同步原语：<https://docs.python.org/3/library/asyncio-sync.html>
