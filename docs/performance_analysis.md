# Day 22：性能瓶颈分析

## 1. 目标

Day 22 不先修改缓存、并发或模型参数，而是先建立可重复的性能证据：

```text
选择代表场景
→ 预热
→ 串行重复测量
→ 计算 p50 / p95 / 错误率
→ 检查固定预算
→ 对最慢候选使用 profiler
→ 再决定是否优化
```

本阶段使用三类工具：

| 工具 | 类型 | 用途 |
|---|---|---|
| `time.perf_counter_ns` | 单调高精度时钟 | 建立无 profiler 的延迟基线 |
| `cProfile` | Python 内置确定性 profiler | 定位项目函数累计耗时和调用次数 |
| `py-spy` / Scalene | 可选采样 profiler | 生成火焰图，区分 Python、原生代码和内存热点 |

## 2. 离线基准覆盖范围

默认基准名：

```text
enterprise_policy_agent_offline_performance
schema_version = 1.0
```

包含五个代表场景：

| 场景 | 实际覆盖 | 不包含 |
|---|---|---|
| `runtime_startup` | 五份制度解析、离线索引、业务规则、LangGraph 构建 | BGE 模型加载、数据库连接 |
| `policy_rag_answer` | 向量检索、上下文构造、S 引用校验 | 真实 BGE、真实 LLM |
| `agent_material_route` | 离线意图识别、LangGraph、真实材料规则 | 真实 LLM |
| `agent_approval_route` | 离线意图识别、LangGraph、真实审批规则 | 真实 LLM |
| `policy_research_hybrid` | 内部 RAG、Day 21 研究编排、固定 Web 结果 | 外部网络请求 |

离线替身固定为：

```text
Embedding：deterministic_hash_embedding_v1
LLM：固定返回包含 [S1] 的答案
Web：固定返回一条 W1 公开资料
```

因此报告会明确记录：

```json
{
  "network_calls": false,
  "live_llm_calls": false
}
```

这些替身不是为了伪造线上速度，而是为了在 CI 和不同开发轮次中稳定观察本项目的
Python 解析、规则、检索与编排开销。

## 3. 测量方法

### 3.1 预热

每个场景默认先执行一次 warm-up。预热结果不进入样本统计，降低首次导入、正则编译、
类初始化和内部缓存建立对基线的干扰。

### 3.2 串行测量

每个场景默认测量五次，并且串行执行。这样测得的是单请求延迟基线，不是并发吞吐量。

### 3.3 分位数

报告包含：

- `minimum_ms`；
- `average_ms`；
- `p50_ms`；
- `p95_ms`；
- `maximum_ms`；
- `error_rate`。

小样本 p50 / p95 使用 nearest-rank。五个样本时，p95 等于最慢样本，这比对五个样本做
线性插值更容易解释，也能让偶发慢请求进入门禁。

### 3.4 错误保护

测量失败时报告只保存异常类型，例如：

```json
{
  "succeeded": false,
  "error_type": "TimeoutError"
}
```

不会保存原始异常正文、URL、问题正文、Token 或密钥。

预热失败会立即安全停止，因为此时继续比较样本已经没有意义。

## 4. Day 22 默认性能预算

| 场景 | p95 上限 | 错误率上限 |
|---|---:|---:|
| `runtime_startup` | 750 ms | 0% |
| `policy_rag_answer` | 150 ms | 0% |
| `agent_material_route` | 250 ms | 0% |
| `agent_approval_route` | 250 ms | 0% |
| `policy_research_hybrid` | 250 ms | 0% |

这些预算是离线个人项目的回归护栏，不是生产 SLA。它们只适用于相同的离线场景定义；
不能拿来宣称真实 BGE、DeepSeek、Tavily 或公网 API 能在同一时间内完成。

CI 会运行同一基准并保存 JSON / Markdown 证据。任何场景 p95 超预算或出现错误，性能门禁
都会失败。

## 5. 运行无 profiler 基线

PowerShell：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.run_performance_benchmark `
  --warmups 1 `
  --iterations 5
```

生成：

```text
artifacts/performance/agent-performance-report.json
artifacts/performance/agent-performance-report.md
```

报告按实测 p95 排出 `bottleneck_candidates`。排名第一表示“下一步应优先分析”，不表示
已经证明某个函数需要优化。

## 6. 使用 cProfile

`cProfile` 属于 Python 标准库，不需要安装额外包：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.profile_agent_performance `
  --warmups 1 `
  --iterations 5 `
  --top 20
```

生成：

```text
artifacts/performance/agent-performance.cprofile
artifacts/performance/agent-cprofile-hotspots.json
artifacts/performance/agent-cprofile-hotspots.md
```

结构化热点报告只保留 `app/` 下的相对路径，排除 profiling 驱动脚本后按 cumulative time 排序。
原始 `.cprofile` 可能包含本机绝对路径，因此不会提交 Git 或上传 CI Artifact。

`cProfile` 会增加运行开销，所以：

- 无 profiler 报告用于检查预算；
- cProfile 报告用于定位函数；
- 不比较二者的绝对耗时。

## 7. 可选：py-spy 火焰图

安装可选 profiling 依赖：

```powershell
& .\.venv\Scripts\python.exe -m pip install -e ".[dev,profiling]"
```

让 py-spy 启动被测 Python 子进程，可避免仅附加现有 PID 时常见的权限问题：

```powershell
& .\.venv\Scripts\py-spy.exe record `
  --rate 100 `
  --output artifacts\performance\py-spy.svg `
  -- `
  .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.run_performance_benchmark `
  --warmups 3 `
  --iterations 50
```

py-spy 官方支持 `record -- python ...` 形式并输出 SVG 火焰图：

<https://github.com/benfred/py-spy>

若 Windows 安全策略仍阻止读取子进程内存，应在本机确认终端权限；不要为完成普通
Day 22 验收而修改系统安全策略。内置 cProfile 已能完成必需验收。

## 8. 可选：Scalene

Scalene 2.x 使用 `run` 和 `view` 子命令。PowerShell：

```powershell
& .\.venv\Scripts\python.exe -m scalene run `
  -o artifacts\performance\scalene-profile.json `
  --- -m scripts.run_performance_benchmark `
  --warmups 3 `
  --iterations 50

& .\.venv\Scripts\python.exe -m scalene view `
  --html `
  artifacts\performance\scalene-profile.json
```

Scalene 可进一步区分 Python、原生代码、系统时间和内存活动。当前官方项目与命令说明：

<https://github.com/plasma-umass/scalene>

## 9. 如何阅读结果

建议按以下顺序判断：

1. 先检查 `error_rate`，失败样本不能用低延迟掩盖；
2. 再检查 p95 是否超过固定预算；
3. 查看最慢场景及其预算占用率；
4. 用 cProfile 的 cumulative time 找到该场景中的调用热点；
5. 用 py-spy / Scalene 验证是否为真实 CPU、原生库、I/O 或内存问题；
6. 只对重复出现、能稳定复现且影响目标场景的热点做优化；
7. 优化后在同一机器、同一配置下重新生成报告比较。

常见解释示例：

```text
runtime_startup 最慢
→ 先检查制度是否重复解析、规则对象是否重复构建、LangGraph 是否每请求编译

policy_rag_answer 最慢
→ 区分 query embedding、向量搜索、上下文构造和 LLM 等待

agent_*_route 最慢
→ 检查 checkpoint、memory、图路由和确定性规则

policy_research_hybrid 最慢
→ 区分内部 RAG 与外部 Provider 等待，不把网络等待误判为 Python CPU 热点
```

## 10. 当前范围之外

Day 22 暂不实现：

- 真实 BGE 模型性能基线；
- 真实 LLM / Tavily 延迟与 Token 成本；
- 多并发、吞吐量、背压和负载测试；
- API 全链路网络延迟；
- 跨机器性能比较；
- 缓存、批处理、连接池或并发优化；
- Prometheus、OpenTelemetry 或分布式 trace；
- 自动接受新的更慢基线。

Day 22 的结论是“先建立证据并定位”，不是“已经完成生产性能优化”。
