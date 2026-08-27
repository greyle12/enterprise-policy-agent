# Phase 35：蓝绿 Vector Collection 发布与回滚

## 1. 本阶段解决什么问题

Phase 30 已能对一个 collection 做幂等增量同步，但直接在在线 collection 中更新仍存在风险：新模型、
新 Chunker 或新权限元数据如果有问题，应用启动后已经覆盖旧快照，回滚需要重新生成向量。

Phase 35 将“构建”和“发布”拆开：

```text
Blue active collection ───────────────┐
                                      ├── atomic release pointer
Green isolated build → validation ────┘
```

Green collection 完整构建并通过检索评测后，发布操作只更新一个数据库指针。旧 Blue 继续保留为
`previous_collection`，需要时可以通过一次事务交换回来。

Phase 36 进一步要求 Green 通过 distributed indexing lease 构建。Publish/rollback 与 Builder 锁定同一
collection 控制行；活跃 lease 存在时发布失败，active/previous collection 也不能再获得构建 lease。

## 2. 核心数据模型

### Release pointer

`rag_vector_collection_releases` 每个逻辑 alias 只有一行：

- `active_collection`：新进程应该读取的物理 collection；
- `previous_collection`：允许一次快速回滚的上一快照；
- `generation`：单调递增的 Compare-And-Swap 版本；
- `embedding_identity`、`pipeline_version`；
- `record_count` 和完整 `snapshot_sha256`；
- `updated_at`。

### Release history

`rag_vector_collection_release_history` 按 alias + generation 保存 publish/rollback、被替换 collection、
模型、pipeline、数量和快照摘要。它用于审计，也为回滚重新验证旧 collection 提供可信预期值。

## 3. 为什么需要 generation CAS

假设两个管理员都看到 generation 2：A 发布 Green，B 随后基于旧状态执行回滚。如果没有 CAS，B 可能
覆盖 A 的发布决定。当前所有写操作都要求调用者提交 `expected_generation`：

```text
SELECT pointer FOR UPDATE
→ actual_generation == expected_generation
→ validate target
→ UPDATE ... WHERE generation = expected_generation
→ insert audit history
→ transaction commit
```

代数不一致时立即失败，调用者必须重新执行 `status`，不能静默覆盖并发发布。

## 4. 发布前验证

只验证 `COUNT(*)=199` 不够，因为“数量相同、内容陈旧”的 collection 仍可能被发布。本阶段使用四层门禁：

1. 记录总数与 Indexer 输出一致；
2. 所有记录的 `embedding_identity` 与待发布模型一致；
3. 所有记录的 `index_pipeline_version` 一致；
4. 按 record ID 排序后，对完整 `(record_id, index_fingerprint)` 清单计算 SHA-256。

Indexer 输出的 `snapshot_sha256` 必须与发布事务从数据库重新计算的摘要完全相同。权限元数据参与每个
Chunk 的 `index_fingerprint`，因此权限变更也会改变整体摘要。

## 5. 运行时行为

默认配置保持兼容：

```text
RAG_PGVECTOR_RELEASE_ALIAS=
```

留空时继续直接使用 `RAG_PGVECTOR_COLLECTION`，并保持原有启动同步行为。

设置 alias 后：

```text
resolve active physical collection
→ open existing PgVectorIndex
→ parse current documents
→ validate IDs + every index fingerprint + snapshot SHA
→ construct existing PolicyRetriever with index_vectors=False
```

Alias 模式不会在应用启动时重写 active collection。已发布快照与当前代码/语料不一致时启动失败，避免
用旧向量配新 Chunk 或新权限元数据。

指针在进程启动时解析，不是每个请求查询数据库。发布或回滚后需要滚动重启 Agent；已有进程会继续使用
它启动时打开的 collection。这避免给每个检索请求增加控制面查询，也保证单个进程内请求视图稳定。

## 6. Windows PowerShell 操作流程

### 6.1 启动 PostgreSQL

```powershell
docker compose up -d postgres
docker compose ps postgres
$env:RAG_VECTOR_STORE_PROVIDER = "pgvector"
```

### 6.2 构建 Green 物理 collection

```powershell
python -X utf8 -m scripts.index_policy_documents `
  --collection enterprise-policy-bge-small-zh-v1-green
```

成功输出包括：

```json
{
  "total_chunk_count": 199,
  "snapshot_sha256": "<64-character SHA-256>"
}
```

`--collection` 会为这次 Indexing 显式关闭 alias 解析，确保写入 Green，而不是意外写入当前 Blue。

### 6.3 运行代码/模型评测

至少运行：

```powershell
python -X utf8 -m scripts.run_retrieval_evaluation --mode bge --device cpu
python -X utf8 -m scripts.run_retrieval_candidate_sweep --mode bge --device cpu
```

生产发布还应运行更大的匿名 Query 集和固定硬件基准。

这里的 Phase 31/33 命令验证当前语料、代码和 BGE 检索质量，并不直接查询刚构建的 Green pgvector
collection。Green 本身由发布事务重新计算完整快照 SHA 来证明内容一致；在生产环境中，还应补充绑定目标
collection 的真实 PostgreSQL 查询评测和固定硬件延迟基准，不能把上述命令描述成数据库验收。

### 6.4 查询当前指针

```powershell
python -X utf8 -m scripts.manage_vector_collection_release `
  status `
  --alias enterprise-policy
```

首次发布应显示 `published: false`，此时 expected generation 为 0。

### 6.5 发布 Green

将 Indexer 输出的 SHA-256 替换到命令中：

```powershell
python -X utf8 -m scripts.manage_vector_collection_release `
  publish `
  --alias enterprise-policy `
  --target-collection enterprise-policy-bge-small-zh-v1-green `
  --expected-generation 0 `
  --expected-record-count 199 `
  --expected-snapshot-sha256 <INDEXER_OUTPUT_SHA256> `
  --embedding-identity BAAI/bge-small-zh-v1.5 `
  --pipeline-version policy-index-v1
```

第二次 Blue/Green 切换时，先执行 status 并使用最新 generation，不要复制旧命令中的代数。

### 6.6 启用 alias 并重启 Agent

`.env`：

```text
RAG_VECTOR_STORE_PROVIDER=pgvector
RAG_PGVECTOR_RELEASE_ALIAS=enterprise-policy
```

然后：

```powershell
docker compose restart agent
docker compose ps
```

### 6.7 回滚

假设当前 generation 为 2：

```powershell
python -X utf8 -m scripts.manage_vector_collection_release `
  rollback `
  --alias enterprise-policy `
  --expected-generation 2

docker compose restart agent
```

回滚前会重新验证 previous collection 的数量、模型、pipeline 和 snapshot SHA。旧 collection 被删除或
篡改时，回滚失败关闭，而不是切换到不可信快照。

## 7. 安全模型是否变化

没有变化。发布控制面只决定从哪个物理 collection 读取。进入检索后仍然是：

```text
Trusted Identity
→ authorized_chunk_ids
→ pgvector MATERIALIZED authorized_records
→ cosine similarity
→ BM25 / RRF / Reranker
→ Context Builder
```

蓝绿发布不会绕过 authorization-before-similarity，也不会把无权限 Chunk 复制到 Prompt。

## 8. 替代方案

| 方案 | 优点 | 缺点 |
|---|---|---|
| 原 collection 原地更新 | 简单、存储少 | 新旧模型混写风险，回滚需重建 |
| `.env` 手工改 collection | 无额外表 | 缺少 CAS、审计和统一当前状态 |
| PostgreSQL VIEW | 读取透明 | DDL 切换和连接缓存更难管理，元数据校验仍需实现 |
| Release pointer（当前） | 事务小、可审计、回滚快 | 进程需要重启解析新指针 |
| 每请求动态解析 pointer | 切换即时 | 控制面进入热路径，增加延迟与故障面 |

## 9. 生产环境不足

- 当前只保留一个 previous 快照，不支持任意历史 generation 回滚；
- Phase 37 已增加旧 collection 保留期和安全 GC；仍没有存储配额、自动调度或批量限速；
- Phase 36 已增加按 collection 的分布式 Indexing lease 与 fencing；仍没有全局任务调度器；
- 发布后需要滚动重启，尚未实现配置监听或连接池热切换；
- 没有 Kubernetes readiness/Deployment rollout 集成；
- CI 使用 SQL 状态机替身，不连接真实 PostgreSQL、不加载真实 BGE；
- CLI 是管理命令，没有 RBAC、双人审批和变更单集成。

## 10. 面试官可能追问

**蓝绿索引和数据库事务分别解决什么？**

蓝绿隔离解决构建期间不污染在线快照；事务保证 active pointer 和审计历史一起提交；CAS generation 防止
并发管理员用过期状态覆盖新发布。

**为什么需要 snapshot SHA，记录数不够吗？**

相同记录数可能包含旧内容、旧权限或遗漏一条再多一条。完整 ID+指纹清单摘要才能证明数据库快照与当前
Indexer 期望完全一致。

**为什么运行时不继续增量同步 active collection？**

Alias 模式把 active collection 当成不可变发布物。启动时写入会绕过“构建→评测→发布”门禁，使回滚快照
失去确定性。

**指针更新后为什么还要重启？**

当前 VectorIndex 在启动时绑定物理 collection。每请求解析会增加延迟和控制面依赖；作品集阶段选择稳定的
进程级快照，通过滚动重启切换。生产系统可以进一步实现热加载和请求排空。
