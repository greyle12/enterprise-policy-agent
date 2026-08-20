# Phase 27：Hybrid Search 与 RRF

## 1. 本阶段解决什么问题

Phase 26 已经具备两个独立检索通道：

- Vector Search：擅长语义、同义表达和口语化问题；
- BM25：擅长制度编号、表单编号、专有名词和原词匹配。

单独选择任何一路都会丢掉另一种信号。Phase 27 用 Reciprocal Rank Fusion（RRF）把两路 Top-K
名单合并为一个去重排名，并让正式 `PolicyAnswerService` 使用 Hybrid Search。

本阶段不接 BGE Reranker。RRF 负责召回融合，Phase 28 的 cross-encoder 负责对融合后的候选做
更精细的 Query-Document 相关性判断。

> 当前状态更新：Phase 28 已将可选 BGE Reranker 接入 RRF 候选之后；本文其余内容保留
> Phase 27 的融合层设计和消融边界。

## 2. Pipeline 位置

```text
Trusted Identity
→ authorized chunk IDs
→ Vector top-20 ─┐
                  ├→ RRF → fused top-5 → Prompt Guard → Context → LLM
→ BM25 top-20  ──┘
```

两个子检索都收到同一个授权 ID 集合。RRF 只能看到已经授权的两个排名，不能对全库融合后再过滤。

## 3. 输入和输出

### 输入

`PolicyRetriever.search_hybrid(...)` 接收：

- `query`：用户问题；
- `top_k`：最终融合结果数量，默认 5；
- `candidate_k`：每个子检索召回窗口，默认至少 20；
- `rank_constant`：RRF 平滑常数，默认 60；
- `allowed_chunk_ids`：调用方已经计算好的授权 Chunk 白名单。

### 输出

仍然返回已有 `PolicyRetrievalResult`，但：

```python
retrieval_method = RetrievalMethod.HYBRID
```

并记录 `retrieval_signals`：

- 来源是 Vector 还是 BM25；
- 在该来源中的 1-based rank；
- 该来源原始分数；
- 本次 RRF contribution。

这样下一阶段可以解释候选从哪里来，而不把不同尺度的原始分数误当成可比较值。

## 4. RRF 如何工作

对文档 `d`，RRF 分数为：

```text
RRF(d) = Σ 1 / (k + rank_r(d))
```

- `r` 是一个检索通道；
- `rank_r(d)` 是文档在该通道中的排名，从 1 开始；
- 文档不在某个通道的候选列表中时，该通道贡献为 0；
- `k` 默认 60，用于平滑相邻名次的差异。

示例：某 Chunk 在 Vector 排第 2，在 BM25 排第 1：

```text
1 / (60 + 2) + 1 / (60 + 1)
```

同一 Chunk 在两路出现时只输出一次，但获得两次贡献。

## 5. 为什么不直接相加原始分数

Vector Search 的余弦相似度和 BM25 的词频相关分数没有共同量纲：

- 余弦分数通常落在有限区间，但分布受 Embedding 模型影响；
- BM25 分数没有固定上界，受语料规模、词频、分词和长度影响；
- 换模型、换语料或换 Tokenizer 后，手工权重可能失效。

直接执行：

```text
0.5 × cosine + 0.5 × BM25
```

并不代表两种信号权重相等。RRF 使用名次而不是原始分数，适合作为不依赖分数标定的融合基线。

替代方案包括：

- min-max 或 z-score 归一化后加权；
- 通过标注数据学习权重；
- Learning to Rank；
- Query Router 按问题类型动态选择检索器；
- Weighted RRF。

当前项目尚无 Retrieval Evaluation 标注集，因此先选择标准、确定、容易解释的未加权 RRF。

## 6. 候选窗口为什么大于最终 top-k

默认每路取最多 20 个候选，再融合为最终 5 个。若两路都只取 5 个：

- 一路排名稍低但另一路也命中的 Chunk 可能根本进不了融合池；
- Phase 28 Reranker 没有足够候选进行纠错；
- 深层 Recall 被过早截断。

候选窗口也不能无限增大，因为它会增加：

- 当前内存或 pgvector 精确索引的扫描和排序开销；
- Phase 28 cross-encoder 推理成本；
- PostgreSQL 连接、授权 CTE 和持久化数据库的查询成本。

`candidate_k=20` 是当前 199 Chunk 小语料的工程默认值，不是经过 Recall@K/MRR 调优后的最终结论。

## 7. 正式检索链路接入

Phase 27 时，`PolicyAnswerService` 的最小检索 Protocol 从 `search()` 调整为 `search_hybrid()`；
Phase 28 又统一为 `search_reranked()`。因此当前正式制度问答执行：

```text
user input guard
→ AccessControlledPolicyRetriever.search_reranked
→ RRF fused candidates
→ optional Cross-Encoder reranker
→ evidence prompt-injection guard
→ context builder
→ LLM
→ citation validation
```

原有 `search()` 和 `search_keywords()` 仍然保留，用于测试、诊断、消融比较和下一阶段评测。

## 8. 安全设计

`AccessControlledPolicyRetriever.search_hybrid()` 与 `search_reranked()` 都只计算一次授权集合，并把
同一不可扩大的白名单传给 Vector 与 BM25；Reranker 只能接收它们产生的授权候选：

```text
Authorization Filter
→ Vector Similarity
→ BM25 candidate/statistics/scoring
→ RRF
→ optional Reranker
```

不允许：

```text
全库 Vector/BM25
→ RRF
→ 再过滤未授权结果
```

这保证未授权 Chunk：

- 不进入 Vector 相似度结果；
- 不进入 BM25 候选和语料统计；
- 不进入 RRF；
- 不进入 Prompt Guard、Context 或 Citation。

RRF 本身不读取正文，只处理 Chunk ID 和名次，缩小了融合层的数据暴露面。

## 9. 确定性和错误边界

- 每个排名来源名称必须非空且唯一；
- 同一来源内的 Chunk ID 必须非空且唯一；
- RRF 至少需要两个命名排名；
- `rank_constant` 必须是正整数；
- `top_k` 必须大于零；
- `candidate_k` 不能小于最终 `top_k`；
- 一个通道没有命中时，另一个通道仍可产生结果；
- 非空但无法产生 BM25 Token 的查询仅降级到 Vector，不吞掉其他 BM25 参数错误；
- RRF 分数相同按 `record_id` 排序，结果可复现。

## 10. 测试与专项验收

测试覆盖：

- 标准 RRF 公式和多路贡献；
- 跨通道去重；
- 不比较原始分数尺度；
- 空通道降级；
- 确定性 tie-break；
- 参数、来源和重复 ID 校验；
- Vector/BM25 在真实 Retriever 中融合；
- 单通道候选保留；
- 纯标点等 BM25 不可分词查询的 Vector-only 降级；
- 两路检索前权限过滤；
- `PolicyAnswerService` 正式调用 Hybrid Search；
- 原有安全专项验证继续通过；
- CI 不允许移除 Phase 27 门禁。

专项命令：

```powershell
python -X utf8 -m scripts.verify_hybrid_search
```

正确结果包含：

```json
{
  "phase": 27,
  "passed": true,
  "document_count": 5,
  "chunk_count": 199,
  "vector_index_size": 199,
  "bm25_index_size": 199,
  "rrf_rank_constant": 60,
  "default_candidate_k": 20,
  "verification_scope": "hybrid_rrf_without_reranker"
}
```

## 11. 生产环境不足

- 两个索引仍在单进程内存中；
- Vector Search 当前是精确扫描，没有 ANN；
- BM25 当前没有 posting list；
- `candidate_k` 与 `rank_constant` 尚未通过标注集调优；
- 未实现 Weighted RRF、字段权重和 Query Router；
- 没有记录线上点击或人工相关性反馈；
- Phase 28 已接入可选 BGE Reranker，但尚无真实模型效果与延迟评测；
- 尚未用 Recall@K、MRR、nDCG 比较 Vector、BM25 和 Hybrid。

## 12. 面试官可能追问

### RRF 是召回还是重排？

它更准确地说是多个 ranked list 的 rank fusion。它不理解 Query-Document 语义，也不等价于
cross-encoder Reranker；它将多个召回通道的名次合成一个候选排名。

### `k=60` 越大越好吗？

不是。较大的 `k` 会缩小相邻名次贡献差距，更强调多个列表共同出现；较小的 `k` 更奖励单路头部。
需要用检索评测集验证，不能把 60 当作所有语料的最优值。

### 为什么共同命中的结果通常会上升？

因为它从多个通道获得倒数名次贡献。语义和精确词面同时支持一个 Chunk 时，融合分数通常更高。

### RRF 有什么缺点？

它丢弃原始分数间距：第一名和第二名即使原始相关性差距巨大，也只由名次决定；它还依赖候选窗口，
窗口外的相关文档无法被恢复。

### 如何证明 Hybrid 比单路更好？

Phase 27 只能证明算法、集成和安全边界正确。真正的效果结论要在 Phase 31 用带相关性标注的查询集，
分别比较 Vector、BM25、Hybrid 的 Recall@K、MRR，必要时再增加 nDCG 和消融实验。

## 13. 参考资料

- Cormack、Clarke、Büttcher，*Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods*：https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf
- Elasticsearch 官方 RRF 说明与公式：https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion
