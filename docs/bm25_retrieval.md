# Phase 26：BM25 关键词检索

## 1. 本阶段解决什么问题

现有向量检索擅长语义相似，但对制度编号、表单编号、专有名词和用户原词不一定稳定。Phase 26 在现有 `PolicyRetriever` 内增加独立 BM25 通道，使以下查询可以依靠词面证据命中：

- `INFORMATION_SECURITY_POLICY_001` 这类制度标识；
- `PROC-2026-001` 这类企业编号；
- `住宿发票`、`采购申请单` 这类明确条款词语；
- 中英文混合的制度文本。

本阶段只提供 BM25 检索，不把向量分数与 BM25 分数直接相加。Hybrid Search 和 RRF 属于 Phase 27。

## 2. 它在 RAG Pipeline 中的位置

```text
LoadedDocument
→ Policy Parser
→ Policy Chunker
→ PolicyChunk.retrieval_text
→ BM25 Index
→ authorization-scoped BM25 search
→ PolicyRetrievalResult(method=BM25)
```

BM25 与现有向量索引共享同一组 `PolicyChunk`，不会建立另一套 Parser、Chunker、Retriever 或权限系统。

## 3. 输入和输出

### 输入

- 建索引输入：`BM25Record(record_id, text, metadata)`；
- 检索输入：查询字符串、`top_k` 和可选的 `allowed_record_ids`；
- 业务入口：`PolicyRetriever.search_keywords(...)`；
- 安全入口：`AccessControlledPolicyRetriever.search_keywords(...)`。

### 输出

底层索引返回 `BM25SearchResult(record, score)`；制度检索器把它转换为已有的：

```python
PolicyRetrievalResult(
    chunk=matched_chunk,
    score=bm25_score,
    retrieval_method=RetrievalMethod.BM25,
)
```

保留 `retrieval_method` 是为了让下一阶段 RRF 能区分候选来自哪个通道，同时不破坏现有向量结果的默认契约。

## 4. BM25 评分

本项目使用常见的 Okapi BM25 形式：

```text
IDF(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))

score(D, Q) = Σ IDF(t) ×
              tf(t,D) × (k1 + 1)
              ─────────────────────────────────────────
              tf(t,D) + k1 × (1 - b + b × |D| / avgdl)
```

默认参数为：

- `k1 = 1.2`：控制词频饱和，避免同一个词重复很多次就无限放大；
- `b = 0.75`：对文档长度进行适度归一化；
- 查询 Token 去重：Phase 26 不启用 query term frequency，避免重复用户输入放大分数；
- 只返回正分结果，并按 `score desc, record_id asc` 确定性排序。

## 5. 中英文 Tokenizer

`PolicyKeywordTokenizer` 不依赖在线服务，也没有引入 Jieba 等新的运行时依赖：

1. 先做 Unicode NFKC 规范化和 `casefold`；
2. 保留 `abc-001`、`policy_001`、路径式编号等 ASCII 企业标识；
3. 对不超过 8 个字符的中文片段保留完整词；
4. 同时产生中文字符 bigram，例如 `住宿费` 产生 `住宿`、`宿费`；
5. 标点不成为检索词。

这个选择的优点是完全离线、确定性强、容易测试，并能覆盖当前小型制度语料。它不等同于生产级中文分词。

可替代方案包括 Jieba、IK Analyzer、Lucene/OpenSearch analyzer、基于词典的领域分词，以及模型分词器。随着语料规模和中文词义要求提高，可以通过 `KeywordTokenizer` Protocol 替换实现，而不用改变 `PolicyRetriever`。

## 6. 为什么权限必须先于 BM25

本项目保持以下顺序：

```text
Trusted Identity
→ Authorization Filter
→ authorized chunk IDs
→ candidate selection
→ DF / avgdl statistics
→ BM25 scoring
```

未授权 Chunk 不只是不能出现在结果中，它还不能改变授权结果的 IDF 和平均长度。否则攻击者可以通过观察排名或分数变化，推测不可见语料是否存在。

`AccessControlledPolicyRetriever` 仍然是固定可信身份的检索视图。它先调用现有 `authorized_chunk_ids(...)`，再把白名单传给 BM25 索引。用户在聊天中自述部门或角色，不能扩展这个范围。

## 7. 为什么扩展现有 PolicyRetriever

当前项目选择在 `PolicyRetriever` 内并列维护向量索引和 BM25 索引，原因是：

- 两个通道必须索引完全相同的 Chunk；
- 两个通道必须复用完全相同的授权集合；
- 下游 Context Builder 已经消费 `PolicyRetrievalResult`；
- Phase 27 可以在同一边界上执行 RRF；
- 避免两套 Retriever 在生命周期、元数据和权限逻辑上漂移。

## 8. 安全和可靠性边界

- `record_id` 必须非空且唯一；
- 空查询、纯标点查询、非法 `top_k` 被拒绝；
- 查询和文档都有 Token 数量上限；
- 索引元数据复制后保存，避免调用方后续修改；
- 排序具有确定性；
- BM25 不调用 Embedding Provider，不产生网络或模型调用；
- Prompt Injection Guard 仍然位于检索结果进入 Context Builder 的边界，本阶段没有绕过它。

## 9. 生产环境仍然不足

Phase 26 的 `InMemoryBM25Index` 适合当前 199 个 Chunk 的作品集验证，但仍有以下限制：

- 重启后需要重建，尚无持久化倒排索引；
- 每次查询会扫描授权候选，尚未使用 posting list 优化；
- 中文 bigram 不能处理同义词、实体边界和专业词典；
- 没有字段权重、短语查询、模糊匹配和增量删除；
- 单进程索引不适合多实例一致性；
- 尚未通过 Recall@K、MRR 数据集比较参数和 Tokenizer；
- 尚未与向量检索融合，也未正式接入 BGE Reranker。

较大规模生产系统通常会考虑 PostgreSQL 全文检索、OpenSearch/Elasticsearch/Lucene 或专用检索服务。Phase 29 的 pgvector 解决向量持久化，不会自动替代 BM25 倒排索引设计。

## 10. 测试与专项验收

单元测试覆盖：

- NFKC、大小写、企业编号和中文 bigram；
- 词面相关性排序与只返回正分结果；
- 授权范围先于候选、DF、平均长度和评分；
- 确定性 tie-break；
- 非法参数、重复 ID 和 Token 上限；
- `PolicyRetriever` 复用现有 5 文档/199 Chunk；
- 原有向量搜索仍可用；
- CI 不允许移除 Phase 26 专项门禁。

专项验证：

```powershell
python -X utf8 -m scripts.verify_bm25_retrieval
```

正确时 JSON 中应包含：

```json
{
  "phase": 26,
  "passed": true,
  "document_count": 5,
  "chunk_count": 199,
  "keyword_index_size": 199,
  "hybrid_search_enabled": false
}
```

## 11. 面试官可能追问

### 为什么不直接把向量分数和 BM25 分数相加？

两类分数的尺度和分布不同，直接加权需要标定并容易受 Provider、语料和查询变化影响。Phase 27 使用基于名次的 RRF，先得到更稳定的融合基线。

### 权限过滤为什么还要影响 IDF？

如果先用全库统计再过滤，未授权文档仍会改变可见结果的分数和顺序，形成统计侧信道。当前实现把候选和统计都限制在授权集合中。

### 为什么中文使用 bigram？

它不依赖外部词典，能确定性覆盖当前短制度词语，并可通过 Protocol 替换。代价是 Token 数量增加，且语义边界较粗。

### BM25 的 `k1` 和 `b` 是什么？

`k1` 控制词频收益多快饱和；`b` 控制文档长度归一化强度。参数应通过检索评测集选择，而不是只凭经验。

### 当前复杂度为什么可以接受？

当前只有 199 个 Chunk，查询期扫描授权候选便于证明安全和正确性。规模扩大后应使用倒排 posting list、增量索引和专用存储，同时保留 authorization-before-scoring 契约。
