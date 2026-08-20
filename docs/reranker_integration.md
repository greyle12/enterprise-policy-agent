# Phase 28：BGE Reranker 正式接入

## 1. 本阶段解决什么问题

Phase 27 的 RRF 能融合 Vector 与 BM25 的名次，但它不理解 Query 和候选制度之间的细粒度语义关系。
它只知道：

- Chunk 在 Vector 中排第几；
- Chunk 在 BM25 中排第几。

Phase 28 把已有 `BGERerankingProvider` 正式放到 RRF 之后，对候选 `(query, retrieval_text)` 进行
Cross-Encoder 联合评分，再选择最终 Top-K。

```text
Authorization
→ Vector top-20 + BM25 top-20
→ RRF candidate pool top-20
→ BGE Cross-Encoder batch score
→ final top-5
→ Prompt Guard
→ Context Builder
→ LLM + Citation
```

## 2. Cross-Encoder 是什么

Embedding/Bi-Encoder 分别计算 Query 和 Document 向量，因此文档向量可以预先计算，适合大规模召回。

Cross-Encoder 把 Query 和每个候选文档作为一个文本对联合输入 Transformer：

```text
(query, candidate_1) → relevance score
(query, candidate_2) → relevance score
...
```

它能观察 Query 与 Document Token 之间更细的交互，通常比单纯向量相似度更适合最终重排，但不能像
Embedding 那样预先计算一份可复用的文档向量。因此它只处理有限候选池，不能直接扫描全部 199 个
Chunk，更不能替代第一阶段检索。

## 3. 输入和输出

### 输入

`PolicyRetriever.search_reranked(...)` 接收：

- 用户 Query；
- 最终 `top_k`，默认 5；
- Reranker 候选窗口，默认 20；
- RRF `rank_constant`；
- 调用方预先计算的授权 Chunk ID。

Reranker Provider 一次接收：

```python
provider.score(
    query,
    [candidate.chunk.retrieval_text, ...],
)
```

使用 `retrieval_text` 而不是裸 `content`，是因为它包含制度标题、章节、条款和正文，给 Cross-Encoder
提供完整的相关性线索，同时仍然保持 Chunk 粒度。

### 输出

仍然返回已有 `PolicyRetrievalResult`：

```python
retrieval_method = RetrievalMethod.RERANKED
score = reranker_score
pre_rerank_score = rrf_score
pre_rerank_rank = rrf_rank
retrieval_signals = vector_and_bm25_diagnostics
```

因此最终分数表示 Reranker 分数，而原始 RRF 信息没有丢失。

## 4. 为什么在 RRF 后面接 Reranker

RRF 与 Cross-Encoder 解决不同问题：

| 层次 | 目标 | 使用的信息 |
|---|---|---|
| Vector | 语义召回 | Query/Document 向量 |
| BM25 | 精确词面召回 | Token、TF、IDF、长度 |
| RRF | 合并两路排名 | 两路 rank |
| Cross-Encoder | 精细相关性排序 | Query 与候选文本联合注意力 |

如果先 Rerank Vector 再与 BM25 融合，BM25 独有候选没有机会接受相同的精排判断。当前顺序保证两路召回
先形成统一候选池，再由同一个相关性模型比较。

## 5. 批量调用

Phase 28 复用已有 batch-first 接口：

```python
scores = provider.score(query, candidate_documents)
```

20 个候选只发生一次应用层 Provider 调用；`batch_size=8` 时，底层模型可以分成多个内部推理批次。

这避免：

```text
20 candidates → 20 Python/CrossEncoder.predict calls
```

同时保持：

- 输入候选数量与输出分数数量一致；
- 分数必须是有限数值；
- 输出顺序必须对应输入顺序；
- 分数相同时保留 RRF 原始顺序；
- 空候选池不调用 Provider。

## 6. 运行配置

默认配置：

```dotenv
RAG_RERANKER_PROVIDER=disabled
RAG_RERANKER_MODEL_NAME=BAAI/bge-reranker-v2-m3
RAG_RERANKER_DEVICE=
RAG_RERANKER_BATCH_SIZE=8
RAG_RERANKER_CANDIDATE_K=20
```

显式启用：

```dotenv
RAG_RERANKER_PROVIDER=bge
```

默认关闭的原因：

- CI 不应下载真实模型；
- 首次启动可能需要下载较大的 Cross-Encoder 权重；
- CPU 环境会增加请求延迟；
- 当前尚无 Retrieval Evaluation 数据证明真实模型收益；
- 作品集离线验收不能把固定替身结果冒充真实 BGE 效果。

关闭时，`PolicyAnswerService` 仍然调用统一的 `search_reranked()` 边界，但 Retriever 返回 Phase 27 的
RRF Hybrid 结果。启用时才构建并调用 `BGERerankingProvider`。

## 7. 模型选择

当前默认模型为：

```text
BAAI/bge-reranker-v2-m3
```

选择原因：

- 与项目已有 BGE 技术栈一致；
- 支持多语言，适合中文制度和中英文编号混合文本；
- 已有 `sentence-transformers.CrossEncoder` 适配边界；
- 比直接使用生成式 LLM 给候选打分更确定、成本更可控。

模型输出按相对排名使用，不假设是概率，也没有在 Phase 28 设置“相关性阈值”。阈值必须结合真实模型、
语料和标注集校准。

可替代方案包括：

- 其他 Cross-Encoder；
- Cohere/Jina 等远程 Rerank API；
- ColBERT late interaction；
- LLM listwise reranking；
- Learning to Rank；
- 不启用 Reranker，仅使用 RRF。

当前项目优先本地、可替换、批量契约清晰的 BGE Provider。

## 8. 安全顺序

必须保持：

```text
Trusted Identity
→ authorization filter
→ authorized Vector/BM25
→ authorized RRF pool
→ Reranker Provider
```

而不是：

```text
全库候选
→ Reranker
→ 最后过滤权限
```

未授权 Chunk 不会进入 Cross-Encoder 文本对。专项测试使用只存在于 Core Chunk 的秘密字符串，断言
Reranker 收到的所有 document 输入都不包含该字符串。

Rerank 后的证据仍然经过 Prompt Injection Guard；相关性模型不能替代污染证据检查。

## 9. 失败语义

- Provider 未配置：明确回退到 RRF，不属于异常；
- Provider 已配置但输出数量错误、非数字或非有限值：请求失败；
- Provider 已配置但模型推理失败：错误向上传播，不静默伪装成已成功 Rerank；
- 空授权候选：返回空结果，不调用 Reranker 或 LLM；
- `candidate_k < top_k`：配置或调用错误，立即拒绝。

当前没有实现“真实 Reranker 故障自动回退 RRF”的运行策略，因为静默降级需要可观测指标、告警和明确
产品决策。生产系统可以增加受监控的 fail-open，但不能悄悄吞掉所有异常。

## 10. 正式问答链路

`PolicyAnswerService` 的最小 Retriever Protocol 已切换为：

```python
search_reranked(query, top_k=...)
```

这意味着正式制度问答始终经过统一第二阶段入口：

- Provider disabled：返回 RRF 结果；
- Provider bge：返回 Reranked 结果。

原有 `search()`、`search_keywords()`、`search_hybrid()` 继续保留，用于消融实验和 Phase 31 评测。

## 11. 测试与专项验收

测试覆盖：

- RRF 候选只调用一次批量 Provider；
- Reranker 可以改变最终顺序；
- 原始 RRF rank、score 和 Vector/BM25 diagnostics 被保留；
- 相同 Reranker 分数保持 RRF 稳定顺序；
- 空候选不调用 Provider；
- 未授权文本不进入 Provider；
- disabled Provider 回退到 RRF；
- BGE 配置默认关闭并可显式启用；
- 模型名、设备、batch size、candidate window 配置校验；
- `PolicyAnswerService` 使用统一 Reranked 入口；
- CI 不允许移除 Phase 28 专项门禁。

专项验证：

```powershell
python -X utf8 -m scripts.verify_reranker_integration
```

正确结果包含：

```json
{
  "phase": 28,
  "passed": true,
  "chunk_count": 199,
  "rerank_candidate_k": 20,
  "configured_bge_model": "BAAI/bge-reranker-v2-m3",
  "runtime_provider": "offline_lexical_fixture",
  "external_model_calls": false,
  "real_bge_model_loaded": false
}
```

## 12. 生产环境不足

- 尚未在真实 BGE 模型上测量相关性收益与延迟；
- `candidate_k=20` 和 `batch_size=8` 未经过硬件与数据集调优；
- 没有 GPU/CPU 自动容量基准和请求级并发门禁；
- 没有 ONNX/OpenVINO/量化部署；
- 没有真实模型故障降级指标和告警；
- 没有 Reranker score threshold 校准；
- 没有 Recall@K、MRR、nDCG 或 reranking ablation；
- 模型权重尚未锁定到不可变 revision 或内部制品哈希；
- 多实例模型加载会重复占用内存。

## 13. 面试官可能追问

### Cross-Encoder 为什么更慢？

每个 Query 都必须与每个候选联合推理，候选文档不能像 Embedding 一样预先向量化。因此复杂度随候选数
增长，只适合第二阶段有限候选。

### RRF 和 Reranker 是不是重复？

不是。RRF 融合不同检索器的名次；Cross-Encoder 阅读 Query 与候选正文，重新判断语义相关性。

### 为什么候选用 `retrieval_text`？

它包含标题、章节、条款和正文，比裸正文有更多制度定位信息；同时仍保持单 Chunk 输入，避免整篇文档
挤占模型长度。

### 为什么不设置最低 Reranker 分数？

不同模型可能输出 logits 或不同尺度的分数。没有标注数据就设置阈值容易误删相关证据。Phase 28 只用于
排序，证据不足仍由 Context/回答约束处理。

### 如何证明 Reranker 有效？

固定离线 Provider 只能证明接线、排序和安全正确，不能证明真实 BGE 提升效果。Phase 31 必须比较：

```text
Vector
BM25
RRF Hybrid
RRF Hybrid + BGE Reranker
```

并报告 Recall@K、MRR，必要时增加 nDCG、延迟和成本。

## 14. 参考资料

- BAAI `bge-reranker-v2-m3` 模型卡：https://huggingface.co/BAAI/bge-reranker-v2-m3
- Sentence Transformers CrossEncoder：https://sbert.net/docs/package_reference/cross_encoder/model.html
- Sentence Transformers Cross-Encoder 用法：https://sbert.net/docs/cross_encoder/usage/usage.html
