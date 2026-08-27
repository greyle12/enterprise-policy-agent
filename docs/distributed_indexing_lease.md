# Phase 36：Distributed Indexing Lease 与 Fencing

## 1. 本阶段解决什么问题

Phase 35 已将 Green 构建与 Blue 发布分开，但多个 Job/容器仍可能同时运行：

```text
Builder A → list old snapshot → embed → write Green
Builder B → list old snapshot → embed → write same Green
```

即使每次 `apply_changes` 自身是原子的，两个完整同步任务交错后仍可能出现最后写入者覆盖、陈旧删除和错误
snapshot 报告。Phase 36 为每个物理 collection 增加 PostgreSQL 租约，并用 fencing token 阻止过期进程恢复
后继续写入。

## 2. 数据模型

`rag_vector_indexing_leases` 每个物理 collection 保留一行稳定控制记录：

- `owner_id`：可审计的构建实例，例如 `HOSTNAME:PID`；
- `lease_token`：每次 acquire 随机生成的 128-bit token，不在 CLI 状态中输出；
- `fencing_token`：每次 acquire 单调递增，旧实例无法复用旧 generation；
- `acquired_at`、`renewed_at`、`expires_at`；
- `released_at`。

Release 后不删除控制行，只清空 owner/token/expiry 并保留 fencing token。这既保证下一次 generation 继续
递增，也为后续安全 GC 判断“是否存在活跃构建”提供状态来源。

## 3. Acquire、Heartbeat 和 Release

构建 CLI 默认 TTL 为 900 秒，每 60 秒续约：

```text
lock collection control row
→ reject active/previous release collection
→ reject unexpired owner
→ fencing_token += 1
→ start heartbeat
→ run existing PolicyDocumentIndexer
→ final fenced write
→ release lease
```

TTL 不是构建超时。只要 heartbeat 正常，长时间 BGE Embedding 会持续续约。进程崩溃后没有 heartbeat，
`expires_at` 到期，其他实例可以接管。

## 4. 为什么只有分布式锁还不够

假设 A 的租约过期，B 获得新租约；随后 A 因网络恢复继续执行。如果只在任务开始检查租约，A 仍可能覆盖
B。当前最终 `PgVectorIndex.apply_changes_guarded()` 在同一个数据库事务中执行：

```text
SELECT lease WHERE token + fencing_token + unexpired FOR UPDATE
→ vector upsert + stale delete
→ COMMIT
```

旧 token、旧 fencing token 或过期租约都会 fail closed。即使本次是 no-op，也会执行最终 fence 校验，避免
把并发变化前读取的快照误报为可发布快照。

## 5. 与 Phase 35 发布事务的协调

Acquire、publish 和 rollback 都先创建并锁定相同的 collection 控制行：

- 活跃 lease 存在时，publish/rollback 拒绝；
- collection 已是 active 或 previous 时，新的构建 lease 拒绝；
- 如果 publish 先获得行锁，后续 acquire 会在提交后看到 release pointer 并拒绝；
- 如果 acquire 先获得行锁，publish 会看到活跃 lease 并拒绝。

因此不会出现“发布验证刚完成，仍在运行的旧 Builder 又覆盖 active collection”。

## 6. Windows PowerShell 操作

启动 PostgreSQL，并显式指定 Green：

```powershell
$env:RAG_VECTOR_STORE_PROVIDER = "pgvector"
docker compose up -d postgres

python -X utf8 -m scripts.index_policy_documents `
  --collection enterprise-policy-bge-small-zh-v1-green `
  --lease-owner "$env:COMPUTERNAME:manual-build" `
  --lease-ttl-seconds 900 `
  --lease-renew-interval-seconds 60
```

成功输出包括：

```json
{
  "phase": 36,
  "passed": true,
  "lease": {
    "collection_name": "enterprise-policy-bge-small-zh-v1-green",
    "owner_id": "HOST:manual-build",
    "fencing_token": 1,
    "released": true
  },
  "snapshot_sha256": "<64-character SHA-256>"
}
```

查看租约状态：

```powershell
python -X utf8 -m scripts.manage_indexing_lease `
  status `
  --collection enterprise-policy-bge-small-zh-v1-green
```

CLI 不提供强制解锁。正常进程通过 finally release；崩溃进程等待 TTL 到期。手工删除 lease 行会破坏 fencing
generation，不应作为日常恢复操作。

## 7. 本阶段输入、输出和 Pipeline 位置

输入：物理 Green collection、owner、TTL、当前制度目录、Embedding Provider。

输出：原有 `DocumentIndexingReport`、snapshot SHA，以及不包含 secret token 的 lease generation 证据。

Pipeline 位置：

```text
Acquire Lease
→ Loader / Parser / Chunker
→ Incremental Fingerprint
→ Embedding changed chunks
→ Fenced atomic apply_changes
→ Snapshot report
→ Release Lease
→ Phase 35 Publish
```

它属于离线索引控制面，不进入在线 `Authorization → Similarity` 检索热路径，因此不会改变权限模型。

## 8. 替代方案

| 方案 | 优点 | 为什么当前未选择 |
|---|---|---|
| PostgreSQL advisory lock | 实现短 | 依赖长连接；进程/连接状态难审计，不能直接为 GC 提供持久状态 |
| Redis lock | TTL 和生态成熟 | 增加第二协调系统，发布指针仍在 PostgreSQL，难做同事务互锁 |
| Kubernetes Lease | 适合 K8s controller | 当前 Compose/本地作品集没有 Kubernetes 控制面 |
| 全局 leader election | 一个 leader 简单 | 不同 Green collection 本可并行，粒度过粗 |
| PostgreSQL 行租约 + fencing（当前） | 可审计、按 collection 并行、与发布事务协调 | 需要 heartbeat、时钟和 schema 运维 |

## 9. 生产环境不足

- heartbeat 使用进程内线程，没有独立 Worker supervisor；
- 依赖 PostgreSQL `CURRENT_TIMESTAMP`，但这是为了避免应用主机时钟漂移；
- 没有 lease acquire/renew/release 的长期审计历史与指标告警；
- 没有构建任务队列、重试预算、断点续传和内容分批提交；
- 没有管理员 RBAC 或双人审批；
- Phase 37 已实现旧 collection retention、引用检查与两阶段 GC；仍缺自动调度、RBAC 和指标告警；
- CI 使用确定性 SQL 状态机替身，不连接真实 PostgreSQL，也不加载 BGE。

## 10. 面试官可能追问

**TTL 和 fencing token 分别解决什么？**

TTL 让崩溃 owner 最终可被接管；fencing token 防止已经过期的旧 owner 恢复后继续写。只有 TTL 没有 fencing
仍存在僵尸写入。

**为什么最终写入还要 `FOR UPDATE`？**

先检查再写之间存在 TOCTOU 窗口。锁定 lease 行并在同一事务写向量，能让 takeover/publish 与最终 mutation
串行化。

**为什么不允许重建 previous collection？**

Previous 是快速回滚快照。修改它会使 Phase 35 的审计历史与回滚摘要失去意义。新构建必须使用新的 Green
物理 collection。

**它是 leader election 吗？**

它是按 collection 的临时写入领导权，不是整个系统的全局 leader。不同 Green collection 可以并行构建。
