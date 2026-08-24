# Phase 33：真实 BGE Candidate Window 消融与实验协议

## 1. 本阶段解决什么问题

Phase 28 已把 BGE Cross-Encoder 接入正式检索链路，Phase 31/32 已能计算
Recall@K、MRR 和 nDCG，但 `candidate_k=20` 仍只是工程默认值。Candidate window 太小会在
RRF 后过早丢失相关证据；太大则让 Cross-Encoder 处理更多 query-document pairs，增加 p95、
CPU/GPU 时间和显存压力。

本阶段增加一个受控实验：固定语料、judgments、授权身份、最终 Top-5、Embedding、Reranker
和运行环境，只改变 Hybrid/Reranker 的 candidate K，同时报告 Recall@5、MRR@5、nDCG@5、
p50 和 p95。报告给出 Pareto 前沿，但不会自动修改生产配置。

## 2. 在 RAG Pipeline 中的位置

```text
Authorization Filter
→ Vector + BM25 candidate retrieval
→ RRF candidate window（本阶段唯一自变量）
→ BGE Reranker
→ fixed Top-5
→ Context Builder / LLM
```

它是 retrieval-only 实验，不调用答案生成 LLM。实验复用现有
`AccessControlledPolicyRetriever`，因此权限过滤仍发生在 Vector/BM25 候选和相似度计算前；
不会建立第二套 Retriever，也不会绕过 Prompt Injection 与污染证据隔离边界。

## 3. 输入与输出

输入包括：

- 版本化 JSONL judgments 与制度目录；
- `offline` 或 `bge` 运行模式；
- candidate K 列表与当前默认值；
- warm-up 次数、测量重复次数和三项质量阈值；
- BGE 模型 identity、device、Embedding/Reranker batch size。

输出包括：

- `artifacts/evaluation/retrieval-candidate-sweep-report.json`：机器可读实验记录；
- `artifacts/evaluation/retrieval-candidate-sweep-report.md`：质量、延迟、门禁和 Pareto 表格；
- CLI 退出码：`0` 为默认窗口通过，`1` 为默认窗口质量门禁失败，`2` 为参数、数据或运行错误。

每个点的 `query_samples = cases × measured_repetitions`。Warm-up 会执行完整检索，但不进入
统计。p50/p95 使用 nearest-rank，避免小样本插值掩盖真实慢查询。

## 4. 架构设计

### 4.1 一次构建，多窗口复用

CLI 先用最大 candidate K 构建一次已有 Retriever 和索引，然后让
`CandidateWindowExperimentRunner` 对同一个对象依次测量不同窗口。这样避免把重复文档解析、
Embedding 或索引构建混入单查询延迟，也保证每个点使用同一授权集合和相同模型实例。

### 4.2 固定 Top-5 与三指标门禁

candidate K 是进入 Reranker 的候选数量，最终返回数量始终为 5。质量门禁只检查当前默认
`candidate_k=20` 的 Hybrid 与 Reranked 通道是否同时达到：

- Recall@5 ≥ 0.80；
- MRR@5 ≥ 0.80；
- nDCG@5 ≥ 0.80；
- 查询错误数为 0。

较小窗口失败是有价值的消融结果，不应让整个命令误报实现故障。运行错误或默认窗口失败才使
总门禁失败。

### 4.3 Pareto 前沿

Hybrid 与 Reranked 分开计算非支配点。若点 A 的三项质量都不低于点 B、p95 不高于点 B，且
至少一项严格更好，则 A 支配 B。未被任何同通道点支配的窗口进入 Pareto 前沿。

Pareto 不是自动推荐：小样本计时抖动、固定语料偏差、GPU warm-up 和业务 SLA 都需要人工复核。
因此报告只记录候选，不写入 `.env` 或生产配置。

### 4.4 Offline 与真实 BGE 的边界

`offline` 使用确定性词法向量和词项重排，适合 CI 验证实验接线、指标、退出码、安全顺序和报告
schema。它不等价于 BGE，也不能证明真实语义质量、显存占用、吞吐或 SLA。

`bge` 复用 Phase 31 的真实 `BGEEmbeddingProvider` 和 `BGERerankingProvider`。首次运行可能
下载模型，应在固定模型缓存、固定硬件、空闲负载和记录软件版本的环境执行。

## 5. 使用方法

完全离线方法验证：

```powershell
python -X utf8 -m scripts.run_retrieval_candidate_sweep `
  --mode offline `
  --candidate-k 5 10 20 40 `
  --default-candidate-k 20 `
  --warmups 1 `
  --repetitions 3
python -X utf8 -m scripts.verify_retrieval_candidate_sweep
```

固定 CPU 环境的真实 BGE 实验：

```powershell
python -X utf8 -m scripts.run_retrieval_candidate_sweep `
  --mode bge `
  --candidate-k 5 10 20 40 `
  --default-candidate-k 20 `
  --warmups 1 `
  --repetitions 3 `
  --device cpu `
  --embedding-batch-size 32 `
  --reranker-batch-size 32
```

GPU 实验把 `--device` 改为 `cuda`，并在显存允许时单独调整两个 batch size。跨机器的绝对延迟
不能直接比较；应保留 JSON 报告中的模型 identity、device、batch size、数据/语料 SHA-256、
Python、操作系统和机器架构。

## 6. 为什么选择这个方案

替代方案包括手写多个命令后人工拼表、网格搜索框架、贝叶斯优化或 Optuna。当前变量只有一个且
候选集合很小，显式 sweep 更容易审计、复现和解释，也不增加生产依赖。等变量扩展到 RRF
`rank_constant`、HNSW `ef_search`、batch size 和多模型组合后，再引入实验跟踪或优化框架更合理。

## 7. 生产环境仍然不足

- 当前 20 条、30 个 judgments 规模小，且不是双人完整 pool judging；
- 没有真实匿名线上 Query 分布、置信区间或多次独立进程重复；
- 未采集 GPU 显存、功耗、吞吐和并发排队延迟；
- pgvector 当前仍为 exact search，尚未纳入 HNSW `m`、`ef_construction`、`ef_search`；
- 报告没有进入 MLflow/W&B 等长期实验追踪系统；
- 尚未定义基于业务 SLA、成本和质量共同审批配置变更的发布流程。

## 8. 面试官可能追问

**为什么 candidate K 增大后 Recall 可能反而下降？**

最终 Top-5 固定。更多候选会改变 RRF 候选集合和 Cross-Encoder 的竞争关系；模型排序误差可能把
原本靠前的相关证据挤出 Top-5，所以质量不保证单调。

**为什么不能只看 Recall@5？**

Recall 只关心相关证据是否出现；MRR 关注第一个相关证据的位置，nDCG 利用 G1/G2/G3 判断高价值
证据是否更靠前。企业制度问答的上下文窗口有限，三者表达不同风险。

**为什么授权必须在向量相似度之前？**

后过滤会让无权限记录参与 ANN/精确候选和排序，还可能因 Top-K 被占用导致授权结果召回下降。
本项目先根据可信身份得到 authorized IDs，再在该集合内计算相似度、BM25、RRF 和 Reranker。

**如何选最终窗口？**

先排除质量不达标点，再结合固定硬件下的 p95、吞吐、显存和业务 SLA 审阅 Pareto 点；用扩大后的
独立测试集复验，走配置变更与回滚流程，而不是让实验脚本自动写生产参数。

