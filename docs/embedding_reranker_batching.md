# Day 26：Embedding 与 Reranker 批处理优化

## 1. 目标

Day 26 解决模型推理链路中常见的 N+1 调用问题：

```text
32 条文本逐条处理
→ 32 次 Python / Provider 调用
→ 每次都承担输入准备、调度和结果转换的固定开销

32 条文本一次列表处理，batch_size = 8
→ 1 次 Python / Provider 调用
→ 模型内部拆成 4 个逻辑推理批次
→ 输出数量和顺序必须与逐条基线完全相同
```

本阶段覆盖两类适合批处理的工作负载：

- 离线建立制度索引时的文档 Embedding；
- 一条查询对应多个候选制度的 cross-encoder Reranker 打分。

交互式 LLM 请求可能拥有不同 system prompt、会话历史、工具状态和流式响应，Day 26 不把
这些请求强行拼成批次。

## 2. 两层 batch 概念

报告区分两个容易混淆的指标：

| 指标 | 含义 | 默认 32 条、batch size 8 |
|---|---|---:|
| Provider 调用 | 应用调用 `encode()` 或 `predict()` 的次数 | 逐条 32，批量 1 |
| 内部批次 | 模型按 batch size 执行的逻辑推理批次数 | 逐条 32，批量 4 |

一次传入 32 条不代表模型把 32 条无界塞入显存。`BGEEmbeddingProvider` 和
`BGERerankingProvider` 都把 `batch_size` 交给底层模型，由模型分批推理，同时只在应用边界
发生一次调用。

这能摊薄：

- Python 函数和对象转换开销；
- tokenizer / collator 的重复调度；
- CPU/GPU kernel 调度和 Provider 固定开销；
- 逐条结果拼接开销。

它不会保证线性加速；batch 太大还可能增加尾延迟或触发内存不足。

## 3. Embedding 路径

现有接口已经是 batch-first：

```python
vectors = provider.embed_documents(retrieval_texts)
```

`BGEEmbeddingProvider` 将完整文本列表和配置的 `batch_size` 一次交给
`SentenceTransformer.encode()`。以下写法虽然结果相同，但会产生 N 次应用层调用：

```python
vectors = [
    provider.embed_documents([text])[0]
    for text in retrieval_texts
]
```

Day 26 把两种写法放入同一离线对照实验，使用输出 SHA-256 和逐项顺序同时验证等价性。

## 4. Reranker 批量接口

新增 `app/rag/reranking.py`，核心接口为：

```python
scores = provider.score(query, candidate_documents)
```

它一次构造所有 `(query, document)` pairs，并要求：

- 每个候选恰好返回一个有限数值分数；
- 返回顺序与候选输入顺序一致；
- 候选 ID 唯一；
- `top_k` 必须大于零；
- 分数相同按照原始候选顺序稳定排序；
- 空候选集合不调用模型。

`BGERerankingProvider` 默认模型名为 `BAAI/bge-reranker-v2-m3`，但只有显式创建真实 Provider
时才加载模型。Day 26 测试和 CI 全部注入离线 cross-encoder 替身，不下载真实模型。

本阶段建立了 Reranker Provider、候选模型与稳定排序契约；Phase 27 已产生 RRF Hybrid 候选，
但尚未把真实 reranker 自动接入正式问答链路。Phase 28 接入时还需确定候选池大小、top-k 和相关性收益，
不能仅凭离线吞吐报告改变线上排序。

## 5. 离线对照实验

固定专项使用：

```text
每场景条目数 = 32
batch size = 8
模拟每次 Provider 固定开销 = 1.5 ms
模拟每个内部批次开销 = 0.25 ms
```

覆盖：

| 场景 | 逐条 Provider 调用 | 批量 Provider 调用 | 批量内部批次 |
|---|---:|---:|---:|
| `embedding_documents` | 32 | 1 | 4 |
| `reranker_candidates` | 32 | 1 | 4 |

质量门禁要求：

- 两个场景都被执行；
- Provider 调用为 `32 → 1`；
- 内部批次为 `32 → 4`；
- 输出摘要完全相同；
- 输出或排名顺序完全相同；
- 不连接网络或真实模型。

fixture 还应观察到批量吞吐更高，但跨机器绝对耗时不作为正确性门禁。

## 6. 指标公式

```text
Provider 调用减少率
= 1 - 批量 Provider 调用数 / 逐条 Provider 调用数

吞吐
= 处理条目数 / 执行秒数

吞吐加速比
= 批量吞吐 / 逐条吞吐
= 逐条耗时 / 批量耗时
```

默认调用减少率为：

```text
1 - 1 / 32 = 96.875%
```

离线 fixture 故意模拟固定调用开销，使批量路径的差异能够稳定复现。该速度不包含真实
tokenizer、BGE 权重、CPU/GPU、内存带宽或生产请求，因此不能当作真实模型 SLA。

## 7. 运行专项验收

PowerShell：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_embedding_reranker_batching
```

关键输出：

```json
{
  "passed": true,
  "item_count": 32,
  "configured_batch_size": 8,
  "network_calls": false,
  "live_model_calls": false
}
```

两个场景都应包含：

```text
sequential_provider_calls = 32
batched_provider_calls = 1
sequential_internal_batches = 32
batched_internal_batches = 4
outputs_equivalent = true
order_preserved = true
```

## 8. 生成结构化报告

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.run_batch_optimization `
  --items 32 `
  --batch-size 8 `
  --call-overhead-ms 1.5 `
  --batch-latency-ms 0.25
```

生成：

```text
artifacts/performance/agent-batch-optimization-report.json
artifacts/performance/agent-batch-optimization-report.md
```

CI 执行相同命令并保存两份报告，整个过程不读取 `.env` 密钥。

## 9. batch size 如何选择

batch size 不是越大越好，应在目标机器上比较：

- 真实吞吐；
- 单批峰值内存；
- p95 / p99；
- 文本长度分布；
- CPU 线程和 GPU 利用率；
- OOM 与错误率。

建议从 `8 / 16 / 32` 开始，在相同文本集合和相同模型上测试。输入文本长度差异很大时，
应同时考虑按长度分桶，减少 padding 浪费。任何生产参数都必须来自真实模型证据，不能从
Day 26 的固定离线延迟推导。

## 10. 当前边界

Day 26 已完成：

- BGE Reranker 批量 Provider 接口；
- 候选输入、输出数量、有限分数和稳定排序校验；
- Embedding 与 Reranker 逐条/批量等价性实验；
- Provider 调用数、内部批次、吞吐和加速报告；
- 完全离线专项脚本、单元测试和 CI 证据。

仍未完成：

- 真实 BGE Embedding / Reranker 基准；
- Reranker 接入正式检索链路及黄金相关性评测；
- GPU mixed precision、动态 padding 和长度分桶；
- 多请求动态 batching 服务；
- 多进程或多实例共享的 Provider 全局配额；
- 真实模型 OOM 保护和自适应 batch size。

Day 27 已基于 Day 25 的并发扇出证据增加单进程 LLM Provider 背压，并明确 FIFO 排队容量、
超时、取消和过载拒绝语义；它保护交互式 LLM 调用，不替代真实 BGE batch size 基线。详见
`docs/provider_backpressure.md`。
