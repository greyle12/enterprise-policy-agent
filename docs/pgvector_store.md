# Phase 29：PostgreSQL + pgvector 向量持久化

## 1. 本阶段解决什么问题

Phase 28 的向量索引只存在于 Agent 进程内。进程退出后索引消失，每个实例也各自维护一份副本。
Phase 29 增加可替换的 `VectorIndex` 协议和 PostgreSQL/pgvector 实现，使向量记录可以保存在具名卷
或外部 PostgreSQL 中，同时不重建另一套 Retriever、Embedding、Hybrid Search 或 Reranker。

本阶段只负责存储适配和运行边界。Phase 30 才负责正式的文档索引流水线、内容变更检测、删除陈旧
Chunk、索引版本切换和失败恢复。

## 2. 在 RAG Pipeline 中的位置

```mermaid
flowchart TD
    D["Document Loader / Chunker"] --> E["Embedding Provider"]
    E --> S["VectorIndex Protocol"]
    S --> M["In-memory Index"]
    S --> P["PostgreSQL + pgvector"]
    P --> H["BM25 + RRF + Reranker"]
```

`PolicyRetriever` 仍然负责：

- 将 Chunk 的 `retrieval_text` 批量交给 Embedding Provider；
- 将 `VectorRecord` 写入配置的 Vector Index；
- 查询时生成 Query Embedding；
- 与 BM25、RRF 和 Reranker 组合。

Vector Store 只负责：

- 向量及引用元数据持久化；
- collection 隔离；
- 幂等 upsert；
- 授权范围内的余弦相似度 Top-K；
- readiness 和连接池生命周期。

## 3. 统一 VectorIndex 接口

输入：

```python
VectorRecord(
    record_id=chunk.chunk_id,
    text=chunk.content,
    vector=embedding,
    metadata={...citation and provenance...},
)
```

写入：

```python
index.upsert(records)
```

检索：

```python
index.search(
    query_vector,
    top_k=20,
    allowed_record_ids=authorized_chunk_ids,
)
```

输出继续使用已有 `SearchResult(record, score)`，所以 `PolicyRetriever` 以上的 BM25、RRF、BGE
Reranker、Context Builder、Prompt Guard 和 Citation 不需要了解底层是内存还是 PostgreSQL。

## 4. pgvector Schema

应用启动时执行幂等 schema 初始化：

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_policy_vectors (
    collection_name TEXT NOT NULL,
    record_id TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding VECTOR(512) NOT NULL,
    metadata JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (collection_name, record_id)
);
```

`collection_name` 将模型与索引版本放在逻辑命名空间中。升级 Embedding 模型或 Chunk 策略时应使用新
collection，而不是让不同向量语义静默混在一起。

pgvector 使用 `<=>` 表示 cosine distance，余弦相似度为 `1 - distance`。官方项目同时支持精确检索、
HNSW 和 IVFFlat：https://github.com/pgvector/pgvector

## 5. 权限必须先于相似度

本项目没有执行全库 Top-K 后再过滤。查询先物化授权候选：

```sql
WITH authorized_records AS MATERIALIZED (
    SELECT record_id, text, embedding, metadata
    FROM rag_policy_vectors
    WHERE collection_name = %s
      AND record_id = ANY(%s)
),
query_vector AS (
    SELECT %s::vector AS embedding
)
SELECT
    record_id,
    text,
    embedding::text,
    metadata,
    1 - (embedding <=> query_vector.embedding) AS score
FROM authorized_records
CROSS JOIN query_vector
ORDER BY embedding <=> query_vector.embedding
LIMIT %s;
```

安全顺序是：

```text
Trusted Identity
→ authorized_chunk_ids
→ MATERIALIZED authorized rows
→ cosine distance
→ Vector Top-K
→ BM25 / RRF / Reranker
```

授权集合为空时直接返回空结果，不访问数据库。查询、collection、授权 ID、向量和 limit 都通过参数传递，
不会把用户输入拼接进 SQL。

当前授权白名单仍由服务端加载的可信 Chunk 元数据计算。把部门、角色、区域、有效期等策略完全下推到数据库
需要独立的策略 schema、迁移和审计设计，不应在本阶段偷偷改写已有安全模型。

## 6. 为什么默认使用精确搜索

当前语料只有 199 个 Chunk，pgvector 默认精确检索具有 perfect recall。Phase 29 没有创建 HNSW：

- 199 个向量不需要 ANN 才能满足延迟；
- HNSW 会引入参数、内存和召回率权衡；
- 过滤与近似索引组合可能得到少于预期的结果；
- Phase 31 尚未建立 Recall@K/MRR 基线，无法证明 ANN 的损失可接受；
- `MATERIALIZED` 授权候选边界比提前追求 ANN 更重要。

pgvector 官方说明 HNSW 相比 IVFFlat 通常有更好的 speed/recall trade-off，但构建更慢、占用更多内存；
过滤场景还需要考虑 iterative scans。是否增加 HNSW 应由真实规模与 Phase 31 评测决定。

## 7. 运行配置

本机默认不要求 PostgreSQL：

```dotenv
RAG_VECTOR_STORE_PROVIDER=memory
```

显式启用 pgvector：

```dotenv
RAG_VECTOR_STORE_PROVIDER=pgvector
RAG_PGVECTOR_DSN=postgresql://policy_agent:password@127.0.0.1:5432/policy_agent
RAG_PGVECTOR_COLLECTION=enterprise-policy-bge-small-zh-v1
RAG_PGVECTOR_MIN_POOL_SIZE=1
RAG_PGVECTOR_MAX_POOL_SIZE=4
RAG_PGVECTOR_CONNECT_TIMEOUT_SECONDS=5
```

DSN 使用 `SecretStr`，不会出现在正常配置展示中。生产环境不应把真实密码提交到 `.env.example`，应使用
托管密钥服务或容器 secret。

Python 驱动使用 Psycopg 3 连接池。连接池避免为每次请求重新建立数据库连接，并对进程内连接数量设置上限：
https://www.psycopg.org/psycopg3/docs/advanced/pool.html

## 8. Compose 拓扑

Compose 增加：

```text
pgvector/pgvector:0.8.6-pg17-bookworm
```

并使用：

```text
pgvector_data → /var/lib/postgresql/data
```

Agent 等待 PostgreSQL 和 Redis healthcheck 通过后启动。Compose 会覆盖
`RAG_VECTOR_STORE_PROVIDER=pgvector`，而直接运行本机 Python 时仍保持 `memory`。

pgvector 官方 Docker 标签和安装说明见：https://github.com/pgvector/pgvector#docker

## 9. 持久化语义

`ON CONFLICT (collection_name, record_id) DO UPDATE` 使相同 Chunk 可以幂等写入。连接上下文在成功时提交，
失败时事务回滚。

当前应用启动仍会：

```text
加载文档 → 生成全部 Embedding → upsert 全部记录
```

这证明 Vector Store 持久化和可替换边界已经成立，但还没有避免重复 Embedding。Phase 30 将增加：

- document/chunk content hash 比较；
- unchanged / inserted / updated / deleted 统计；
- 陈旧 Chunk 删除；
- 独立 indexing CLI；
- collection/version 发布切换；
- 部分失败与重试策略。

## 10. Readiness 与资源释放

`/health/ready` 现在同时验证：

- 应用组件已经初始化；
- SQLite schema 正常；
- Vector Index 可用；
- pgvector extension 和业务表存在。

应用关闭时会关闭 Psycopg pool。初始化任何后续组件失败时也会释放已经建立的 pool，避免启动失败后泄漏连接。

## 11. 验证

完全离线 SQL/安全契约：

```powershell
python -X utf8 -m scripts.verify_pgvector_store
```

它不连接数据库，验证：

- 现有 Retriever 使用 VectorIndex；
- schema、批量 upsert 和跨实例持久状态；
- 授权 CTE 在距离排序前；
- 未授权 Core 记录没有被相似度评分；
- Compose 镜像和持久卷契约。

真实 Docker 持久化：

```powershell
python -X utf8 -m scripts.verify_docker_deployment
```

脚本写入隔离的 SQLite 与 pgvector 探针，强制重建 PostgreSQL/Agent 容器，再读取并清理探针。它不会删除
业务 collection 或具名卷。

## 12. 替代方案

| 方案 | 优点 | 当前未选原因 |
|---|---|---|
| Qdrant / Milvus / Weaviate | 向量能力和扩展性丰富 | 增加独立服务与运维面，当前规模不需要 |
| Elasticsearch / OpenSearch | 全文和向量统一平台 | 资源和运维成本更高，当前 BM25 已有清晰边界 |
| FAISS + 文件快照 | 本机检索快 | 元数据事务、并发写和恢复需要自行实现 |
| 托管 Vector DB | 低运维 | 成本、数据边界和供应商锁定需要额外评估 |
| PostgreSQL + pgvector | 事务、JSONB、备份、SQL 过滤与向量共存 | 当前项目选择，仍需后续容量评测 |

## 13. 生产环境不足

- schema 初始化仍由应用启动执行，没有 Alembic 或独立迁移 Job；
- 启动时仍会重新计算并 upsert 全量 Embedding；
- 没有备份、恢复、PITR、复制、TLS 和凭据轮换验收；
- 没有连接池排队、超时和数据库错误指标；
- 没有 HNSW/IVFFlat、分区或大规模容量测试；
- collection 没有原子 alias 切换；
- BM25 仍在进程内，没有持久化倒排索引；
- Compose 是单机开发拓扑，不是 PostgreSQL 高可用方案。

## 14. 面试官可能追问

### 为什么不直接把 PolicyRetriever 改成 PgVectorRetriever？

Retriever 还负责 BM25、RRF、Reranker 和安全组合。数据库只是 Vector Index 的一种实现。依赖倒置可以保留
现有算法、测试和离线开发能力。

### PostgreSQL 保存了向量，为什么启动还要重新 Embedding？

Phase 29 只建立存储边界。没有 content hash 和索引发布状态时，直接跳过可能使用过期向量。Phase 30 会用
可验证的增量索引流水线解决，而不是用“表中有数据”作为不可靠判断。

### 为什么用 collection_name？

Embedding 模型、维度、Chunk 策略变化都会改变向量语义。collection 提供显式版本隔离，避免新旧向量混用。

### 如何证明权限过滤早于向量检索？

代码把可信身份生成的 Chunk ID 作为 SQL 参数，先建立 `MATERIALIZED authorized_records`，外层才能使用
`<=>`。测试还放入更相似的 Core 机密记录，并断言它没有进入评分集合。

### 什么情况下会增加 HNSW？

当精确搜索延迟超过预算，并且 Phase 31 标注集能证明选定的 `ef_search`、候选窗口和过滤策略仍满足
Recall@K/MRR 门槛时。不能只因为简历需要“HNSW”就提前加入。
