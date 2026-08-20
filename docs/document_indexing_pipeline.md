# Phase 30：Document Indexing Pipeline

## 1. 它是什么，解决什么问题

Document Indexing Pipeline 是文档摄取链路和在线检索链路之间的同步边界。Phase 29 已让向量保存在
PostgreSQL/pgvector，但应用仍会在每次启动时为全部 Chunk 重新生成 Embedding。持久化只避免数据丢失，
没有避免重复模型计算，也没有清除源文档删除后遗留的向量。

Phase 30 增加 `PolicyDocumentIndexer`，把当前文档目录视为一个期望快照，并与 Vector Store 中当前
collection 的实际快照比较。它只为新增或变化的 Chunk 生成向量，同时删除已不存在的陈旧记录。

## 2. 在整个 RAG Pipeline 中的位置

```mermaid
flowchart TD
    D["Loader / Parser / Chunker"] --> F["Stable Fingerprints"]
    F --> C{"Compare with Vector Store"}
    C -->|"added / updated"| E["Embedding + Upsert"]
    C -->|"deleted"| X["Delete stale chunks"]
    E --> R["Existing Retriever"]
    X --> R
```

在线查询链路没有改变：

```text
Trusted Identity
→ Authorization Filter
→ Vector Search + BM25
→ RRF
→ Optional BGE Reranker
→ Prompt Injection Guard / Context Builder
→ LLM + Citation
```

Indexer 不回答问题，也不决定用户权限。它只维护共享知识索引；查询时仍必须根据可信身份生成
`allowed_chunk_ids`，并在向量距离和 BM25 评分前过滤。

## 3. 输入和输出

输入：

- `PolicyChunk` 列表；
- 已配置的 `EmbeddingProvider`；
- 已配置的 `VectorIndex`；
- 显式 Embedding identity，例如 `BAAI/bge-small-zh-v1.5`；
- `RAG_INDEX_PIPELINE_VERSION`。

输出：

- 同一次解析产生的 `PolicyChunk` 快照，直接交给现有 Retriever 建 BM25 和引用映射；
- `DocumentIndexingReport`；
- 每个文档的 `added / updated / unchanged / deleted` 状态；
- Chunk 的 upsert、delete 和 unchanged 数量。

## 4. 指纹如何生成

每个 Chunk 的稳定指纹由以下字段进行确定性 JSON 序列化后计算 SHA-256：

```text
pipeline_version
embedding_identity
chunk_id
retrieval_text
citation / provenance / authorization metadata
```

不能只比较 `content_hash`，因为这些变化也要求更新索引：

- 制度标题、章节标题或条款标题变化会改变 `retrieval_text`；
- 部门、角色、区域、安全等级变化会改变授权结果；
- PDF 页码、DOCX 块序号或 OCR provenance 变化会改变引用证据；
- Embedding 模型或 Chunk 策略变化必须使旧向量失效。

文档指纹由该文档全部 `(chunk_id, chunk_fingerprint)` 有序集合派生。它用于产生文档级状态，但 Chunk
仍是最小更新单位，所以一条制度修改不会强制重算同一文档内其他未变化条款。

`RAG_INDEX_PIPELINE_VERSION` 是人工控制的 schema/version boundary。修改 Loader 规范化规则、Chunker、
`retrieval_text` 模板或其他会影响索引语义的逻辑时，应升级该版本。

## 5. 同步算法

一次同步执行：

1. 复用现有 Loader Registry、Parser 和 Chunker，得到唯一 `chunk_id` 快照；
2. 调用 `VectorIndex.list_entries()`，只读取 ID 和 JSONB 元数据，不传输向量；
3. 比较持久化 `index_fingerprint` 与期望指纹；
4. 只把新增或变化的 `retrieval_text` 批量传给 Embedding Provider；
5. 找出 Vector Store 中存在、但当前目录中不存在的 stale IDs；
6. 调用一次 `apply_changes(upserts, delete_record_ids=...)`；
7. 用同一批 Chunk 构建 Retriever 的 BM25、权限视图和 Citation 映射，不再次生成文档向量。

完全相同的第二次运行不会调用 `embed_documents()`，也不会打开 pgvector 写事务。
同步语义覆盖整个 configured collection，因此该 collection 必须由本 Indexer 独占，不能混放手工向量或
其他租户记录；探针和新模型应使用不同 collection。

## 6. 原子变更边界

`VectorIndex` 新增：

```python
index.list_entries()

index.apply_changes(
    changed_records,
    delete_record_ids=stale_ids,
)
```

内存实现先验证整个变更集合，然后替换新快照。pgvector 实现在同一个 Psycopg connection context 中执行
批量 `ON CONFLICT ... DO UPDATE` 和限定 collection/record ID 的 `DELETE`。任一步骤失败时数据库事务回滚，
不会出现 upsert 已提交但 stale deletion 未提交的半完成状态。

为防止误清空，Indexer 拒绝空 Chunk 输入。清空整个 collection 需要单独、明确的管理操作。

## 7. 为什么 Retriever 仍需要解析后的 Chunk

Vector Store 不是整个 RAG 状态的替代品。现有 Retriever 还需要 `PolicyChunk` 来完成：

- BM25 索引构建；
- 权限、有效期、部门、角色、区域过滤；
- RRF 与 Reranker 的候选映射；
- Context Builder 和 Citation；
- OCR 页/块 provenance。

因此当前启动仍会执行一次 Loader/Parser/Chunker，但昂贵的 Embedding 只处理变化项。把完整 Chunk 快照也
持久化并让 BM25 增量化是另一项独立设计，不能通过从 Vector Store 拼凑不完整元数据来冒充。

## 8. CLI 和配置

本机默认内存模式可用于学习和验证：

```powershell
python -X utf8 -m scripts.index_policy_documents
```

持久化增量同步需要：

```dotenv
RAG_VECTOR_STORE_PROVIDER=pgvector
RAG_INDEX_PIPELINE_VERSION=policy-index-v1
RAG_PGVECTOR_DSN=postgresql://policy_agent:password@127.0.0.1:5432/policy_agent
RAG_PGVECTOR_COLLECTION=enterprise-policy-bge-small-zh-v1
```

CLI 输出稳定 JSON，包括文档状态和 Chunk 统计。应用启动也复用同一个 `PolicyDocumentIndexer`，不存在
“CLI 一套索引逻辑、Runtime 另一套索引逻辑”。

## 9. 验证

完全离线专项验证：

```powershell
python -X utf8 -m scripts.verify_document_indexing
```

正确结果应包含：

```text
passed: true
no_op_embedding_count: 0
incremental_upsert_count: 1
stale_delete_count: 1
authorization_still_precedes_vector_scoring: true
```

真实 pgvector 可先启动 Compose，再连续执行两次 CLI。第一次应有 upsert；第二次应显示
`changed: false` 和 `upserted_chunk_count: 0`。

## 10. 替代方案与当前选择

| 方案 | 优点 | 不足与本项目决定 |
|---|---|---|
| 每次全量重建 | 实现最简单 | 重复模型成本高，删除语义不清晰；已替换 |
| 只比较文件修改时间 | 读取快 | 复制、时钟和构建环境会造成误判，不可靠 |
| 只比较正文 `content_hash` | 简单 | 漏掉标题、权限、provenance 和模型变化 |
| 独立 manifest 数据库 | 可跳过更多解析 | 需要 schema、事务和恢复一致性；当前规模暂不增加第二状态源 |
| 消息队列/CDC 异步摄取 | 适合大规模、多生产者 | 运维和最终一致性复杂，当前作品集规模不需要 |
| 当前稳定指纹 + collection mirror | 依赖少、可重跑、可解释 | 仍解析全目录和重建 BM25；当前选择 |

## 11. 生产环境仍有哪些不足

- 当前单次同步以 collection 为镜像边界，没有分布式写锁或 leader election；
- Loader/Parser/Chunker 与 BM25 仍会在每个进程启动时执行；
- 没有蓝绿 collection、发布指针和一键回滚；
- 没有失败任务队列、死信队列或断点续传；
- 大目录仍需要流式发现、分页 list entries 和分批事务；
- 没有病毒扫描、文件配额、租户隔离和 ingestion 审计表；
- 权限或引用元数据单独变化时也会重新 Embedding；后续可增加 metadata-only update 避免模型调用；
- 修改 Embedding 模型时应使用新 collection，不能让不同向量空间混写；
- 索引正确性尚未用 Recall@K、MRR 和真实 BGE 数据集衡量。

## 12. 面试官可能怎么追问

### 为什么不是只看 `content_hash`？

因为向量输入是 `retrieval_text`，而授权和引用依赖元数据。正文不变但标题、角色范围或 OCR provenance
变化时，旧记录也已经过期。

### 如何保证幂等？

相同输入产生相同指纹和 Chunk ID；第二次比较后不会生成 Embedding。写入使用 collection + record ID
复合主键和 upsert，删除限定在同一 collection 的 stale ID 集合。

### 为什么先 upsert 和 delete 放在同一个 API？

如果分别提交，进程可能在两次事务之间失败，留下新旧 Chunk 并存的混合快照。`apply_changes` 让存储实现
决定原子提交方式。

### 权限过滤为什么不属于 Indexer？

Indexer 写入完整知识集合和安全元数据。访问权限取决于每个请求的可信身份，必须在在线检索时动态计算，
并在任何 Vector/BM25 评分之前执行。

### 为什么还要解析 unchanged 文档？

当前 BM25、权限对象和 Citation 都依赖完整 `PolicyChunk`。本阶段优先消除最昂贵的重复 Embedding，并保持
一套数据模型。进一步跳过解析需要持久化完整 Chunk 快照和增量 BM25，是后续独立优化。

### 如何发布新 Embedding 模型？

使用新 collection 和新的 pipeline/model identity 完整构建，跑 Phase 31 Retrieval Evaluation，通过后再切换
读取配置；不要在旧 collection 内混合不同维度或语义空间的向量。
