# Advanced RAG Phase 31：Retrieval Evaluation

Phase 31 回答一个独立于 LLM 文风的问题：**正确制度 Chunk 是否被检索出来，以及排得是否足够靠前？**
它不评估最终答案是否流畅，也不复用 30 条意图/工具/规则黄金集冒充检索标注。

## 1. 在 RAG Pipeline 中的位置

```mermaid
flowchart TD
    A[标注 Query] --> B[可信身份与授权 Chunk 集合]
    B --> C[Vector]
    B --> D[BM25]
    C --> E[RRF Hybrid]
    D --> E
    E --> F[BGE Reranker]
    C --> G[Recall@K / MRR@K]
    D --> G
    E --> G
    F --> G
```

评测直接调用现有 `AccessControlledPolicyRetriever` 的四个入口。它停在检索结果处，不构建
Prompt、不调用 LLM、不评价答案。这样可以把“检索没找到”和“LLM 没用好证据”分开定位。

安全顺序保持为：

```text
Trusted Identity → Authorization Filter → Vector/BM25 Scoring → RRF → Reranker
```

数据集加载后会先确认每个相关 Chunk 确实存在，并且对固定评测身份可见。标注不允许要求系统
召回无权限证据；Reranker 也永远看不到未授权候选。

## 2. 输入与输出

Phase 32 已将数据集升级为 graded judgments，并新增 nDCG；当前数据格式和公式详见
`docs/graded_relevance_ndcg.md`。输入仍是 `tests/evaluation/retrieval_test_cases.jsonl`。

| 字段 | 含义 |
|---|---|
| `case_id` | 稳定的 `RET-NNN` 用例编号 |
| `title` | 便于人工审阅的场景名称 |
| `query` | 用户自然语言问题 |
| `judgments` | Chunk ID、G1/G2/G3 相关等级和人工理由，允许多个 |
| `tags` | 领域和单/多相关标签 |

当前 v1 数据集有 20 条查询，覆盖差旅、采购、普通费用、信息安全和请假；其中包含跨条款问题，
避免只用“一个问题对应一个条款”的过度简单数据。

输出为：

```text
artifacts/evaluation/retrieval-evaluation-report.json
artifacts/evaluation/retrieval-evaluation-report.md
```

报告记录数据集和语料 SHA-256、Provider identity、运行模式、候选窗口、每条 Query 的排名、
四通道宏平均指标、耗时、错误和质量门禁结果。语料指纹由排序后的 `chunk_id:content_hash`
生成，因此制度正文变化后，旧报告不会被误认为仍适用于新语料。

## 3. 指标

设查询的相关 Chunk 集合为 $R$，前 $K$ 个检索结果集合为 $S_K$：

$$
Recall@K = \frac{|R \cap S_K|}{|R|}
$$

Recall@K 衡量相关证据找回比例。对于两个相关条款的查询，Top 5 只找回一个时分数是 0.5，
而不是“命中过就算 1”。

设第一个相关 Chunk 的排名为 $rank$：

$$
RR@K =
\begin{cases}
\frac{1}{rank}, & rank \le K \\
0, & \text{Top K 内没有相关 Chunk}
\end{cases}
$$

$$
MRR@K = \frac{1}{N}\sum_{i=1}^{N} RR_i@K
$$

MRR@K 强调第一个可用证据是否靠前。本项目明确写作 `MRR@5`，不把截断指标模糊地称为 MRR。
Recall 使用查询级宏平均，让每个业务问题权重相同。

默认记录 Recall@1/3/5、MRR@5 和 nDCG@1/3/5。Hybrid 与 Reranked 是正式链路，因此当前
门禁要求它们的 Recall@5、MRR@5、nDCG@5 都不低于 0.80；Vector 与 BM25 作为消融对照。

## 4. 两种运行模式

### 4.1 Offline

```powershell
python -X utf8 -m scripts.run_retrieval_evaluation --mode offline
python -X utf8 -m scripts.verify_retrieval_evaluation
```

Offline 使用已有确定性哈希词法向量和确定性词项重排，完全不联网、不下载模型。它适合验证：

- JSONL 和相关 Chunk 标注契约；
- Recall@K / MRR@K 计算；
- 四检索通道的接线和候选窗口；
- 授权先于相似度与 Reranker；
- 报告、退出码和 CI 回归。

它不是语义 Embedding，也不能证明 BGE 的真实质量。当前确定性 v1 基线是：

| 通道 | Recall@1 | Recall@3 | Recall@5 | MRR@5 |
|---|---:|---:|---:|---:|
| Vector fixture | 72.50% | 82.50% | 90.00% | 80.42% |
| BM25 | 85.00% | 92.50% | 95.00% | 92.50% |
| Hybrid / RRF | 82.50% | 90.00% | 92.50% | 87.92% |
| Reranked fixture | 85.00% | 92.50% | 95.00% | 93.50% |

这些数值是回归快照，不应写进简历作为线上效果。

### 4.2 BGE

```powershell
python -X utf8 -m scripts.run_retrieval_evaluation `
  --mode bge `
  --device cpu
```

该模式使用现有 `BAAI/bge-small-zh-v1.5` Embedding 和
`BAAI/bge-reranker-v2-m3` Cross-Encoder，四通道仍用完全相同的数据集、授权身份、K 和报告格式。
首次运行如果本机无缓存，模型库可能需要下载模型；CPU 耗时也会明显高于 Offline。

可显式替换模型：

```powershell
python -X utf8 -m scripts.run_retrieval_evaluation `
  --mode bge `
  --embedding-model BAAI/bge-small-zh-v1.5 `
  --reranker-model BAAI/bge-reranker-v2-m3 `
  --device cuda
```

## 5. 关键设计选择

### 为什么不评估答案文本

Retrieval Evaluation 是组件评测。答案正确率还受 Context Builder、Prompt、LLM、引用解析影响。
将它们混在一个分数中，召回下降时无法定位责任。现有 30 条黄金集继续负责意图、工具、材料、
审批和引用契约；Phase 31 只负责排名质量。

### 为什么相关项是集合

制度问题经常需要组合“标准”和“例外”或“材料”和“时限”。单标签会把找回第二个正确条款
错误地当作无关，也无法衡量证据完整性。

### 为什么选择 Recall@K 和 MRR@K

Recall@K 对“是否找全证据”敏感，MRR@K 对“首个证据是否靠前”敏感，二者容易解释且符合
当前 Top-5 Context Builder。替代指标包括 Precision@K、Hit Rate、MAP 和 nDCG；当相关性具有
多级评分时，nDCG 更合适。当前数据只有二元相关标注，所以先使用 Recall 和 MRR。

### 为什么同时保留 Offline 和 BGE

只跑 BGE 会让 CI 依赖大模型下载、硬件和模型缓存；只跑词法夹具又不能说明语义质量。两层模式
分别承担工程回归和模型实验，且报告明确记录 Provider identity，避免混淆证据。

## 6. 生产不足与下一步实验

- 20 条自建查询规模小，且标注者单一；应扩展到真实匿名查询并进行双人标注与争议仲裁；
- 当前相关性是二元判断；需要 graded relevance 后再加入 nDCG@K；
- 尚未按部门、角色、文档格式、OCR 来源、短/长查询和时间切片分层报告；
- 未统计索引更新后的回归差异和置信区间；
- pgvector 当前生产默认仍为精确检索；Phase 34 已提供 HNSW `m` / `ef_construction` / `ef_search`
  的 ANN Recall–Judged nDCG–p95 实验入口，尚未沉淀固定硬件真实 BGE 快照；
- 真实 BGE 模式尚未在 CI 下载模型，也没有固定 GPU/CPU 硬件基线；
- 评测数据与训练数据需要防止泄漏，真实企业查询还必须先做隐私脱敏。

## 7. 面试追问

**Recall@K 与 Hit Rate@K 有什么区别？** 相关条款有两个、只找回一个时，Hit Rate 是 1，Recall
是 0.5；多证据问答更需要 Recall。

**为什么 MRR 高但 Recall 可能低？** 第一个相关结果排第一会得到 RR=1，但其他必要证据可能都
没找回。因此 MRR 不能替代 Recall。

**如何证明权限过滤不是 Top-K 后过滤？** 评测对象是绑定可信身份的
`AccessControlledPolicyRetriever`；数据集预检拒绝无权限相关标签，现有 Vector/BM25/pgvector
测试分别断言授权集合在候选与距离计算前形成，Reranker 输入也只来自授权 RRF 候选池。

**Hybrid 为什么可能比单独 BM25 差？** RRF 不使用原始分数，弱 Vector 排名也可能抬高无关项。
应通过消融结果调整候选窗口、通道权重或选择 weighted RRF，而不是默认混合一定更好。

**上线前你会怎样扩展？** 用匿名真实查询建立版本化 judgments，按权限与业务域分层，增加
nDCG、无答案查询、置信区间和失败切片；再在固定硬件上对 BGE、候选 K、Reranker K 和 pgvector
ANN 参数做 Recall–MRR–p95–成本联合实验。
