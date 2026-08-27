# Phase 37：Safe Vector Collection GC

## 1. 解决什么问题

Phase 35 把在线检索从物理表名解耦为 active/previous 发布指针；Phase 36 用 PostgreSQL lease 和
fencing token 保证同一 Green collection 只有一个 Builder。多次发布后，既非 active 也非 previous 的旧
collection 仍会占用 pgvector 存储，但直接执行 `DELETE WHERE collection_name = ...` 存在四类风险：

1. 删除当前 active collection，在线检索立即失效；
2. 删除 previous collection，失去一键回滚快照；
3. 删除仍在构建的 Green collection；
4. GC 做完检查后，Builder 或 Publisher 改变状态，形成 TOCTOU 竞争。

Phase 37 增加 read-only plan、retention、mark/sweep 和 fencing generation 重验证。它只回收
`rag_policy_vectors` 中明确命名的物理 collection，不修改发布历史、release pointer 或稳定 lease 控制行。

## 2. Pipeline 位置

```text
Blue/Green Build → Evaluation → Publish → Retention → GC Plan
                                                   ↓
                                             Mark + Grace
                                                   ↓
                                             Fenced Sweep
```

GC 是离线控制面，不进入在线 RAG 热路径。原有安全顺序保持：

```text
Trusted Identity → Authorization Filter → Vector Similarity → BM25/RRF → Reranker
```

## 3. 三重保护与 retention

每个候选 collection 都必须通过以下门禁：

| 门禁 | 数据源 | Fail-closed 行为 |
|---|---|---|
| Active 保护 | `rag_vector_collection_releases.active_collection` | 拒绝 mark/sweep |
| Previous 保护 | `rag_vector_collection_releases.previous_collection` | 拒绝 mark/sweep |
| Lease 保护 | `rag_vector_indexing_leases.expires_at` | 有效租约时拒绝 |
| Retention | Vector/lease 最后活动时间 | 未达到保留期时拒绝 mark |
| Fencing | 稳定 lease 行的 `fencing_token` | mark 后 generation 改变则拒绝 sweep |

最后活动时间取 vector `updated_at` 与 lease acquire/renew/release 时间的最大值，并使用 PostgreSQL
`CURRENT_TIMESTAMP` 判断保留期，避免应用实例时钟漂移。

## 4. 两阶段删除

### 4.1 Plan：只读 dry-run

管理 CLI 会先幂等执行 `CREATE ... IF NOT EXISTS`，确保全新 PostgreSQL 已具备 pgvector、release、lease 和
GC 控制表；随后 `plan()` 只读取 vector、release 和 lease 状态，不创建 mark、不修改 pointer，也不删除记录。
这里的 dry-run 指“不修改业务数据和 GC 状态”，而不是“禁止幂等 schema bootstrap”。输出每个 collection
的记录数、fencing token、最后活动时间、是否可回收和明确的保护原因。

### 4.2 Mark：生成删除意图

Mark 事务按 collection 锁定 Phase 36 的稳定 lease 控制行，然后重新验证：

- 没有有效构建 lease；
- 不是任何 alias 的 active/previous；
- collection 非空；
- 已超过 retention。

通过后写入 `rag_vector_collection_gc_marks`，记录：

- 随机 `mark_token`；
- 当前 `fencing_token`；
- 记录数和最后活动时间；
- retention；
- `marked_at` 和 `sweep_after`；
- 最终 `swept_at` 与删除数量。

Mark 不删除任何向量。宽限期让运维人员、监控或变更审批有时间发现错误并停止后续 sweep。

### 4.3 Sweep：重新检查后精确删除

Sweep 必须提供 collection 和 mark token，并在同一事务内：

1. 锁定稳定 lease 控制行；
2. 锁定 GC mark；
3. 验证 mark token；
4. 再次检查 active/previous/有效 lease；
5. 比较当前 fencing token 与 mark generation；
6. 验证宽限期已结束；
7. 验证记录数和最后活动时间没有变化；
8. 删除精确 collection；
9. 验证删除数等于 mark 记录数并写入 receipt。

任何一步失败都会回滚整个事务。成功后保留 lease 控制行和 GC receipt；因此 generation 不会回退，重复
sweep 使用同一 token 时可安全返回原 receipt。

## 5. PowerShell 操作

先启动 PostgreSQL：

```powershell
docker compose up -d postgres
$env:RAG_PGVECTOR_DSN = "postgresql://policy_agent:local-development-only@127.0.0.1:5432/policy_agent"
```

### 5.1 查看 dry-run

```powershell
python -X utf8 -m scripts.manage_vector_collection_gc plan `
  --retention-days 7
```

首次运行可能创建空控制表；输出必须包含 `"dry_run": true`，且不会创建 GC mark 或删除 Vector。
`protection_reasons` 可能是：

- `active:<alias>`；
- `previous:<alias>`；
- `active_lease:<owner>`；
- `retention`。

### 5.2 标记一个已确认的旧 collection

```powershell
python -X utf8 -m scripts.manage_vector_collection_gc mark `
  --collection enterprise-policy-bge-small-zh-v1-retired-20260801 `
  --retention-days 7 `
  --sweep-grace-seconds 3600
```

保存输出中的 `mark_token`。Mark 后可以检查：

```powershell
python -X utf8 -m scripts.manage_vector_collection_gc status `
  --collection enterprise-policy-bge-small-zh-v1-retired-20260801
```

### 5.3 宽限期结束后 sweep

```powershell
python -X utf8 -m scripts.manage_vector_collection_gc sweep `
  --collection enterprise-policy-bge-small-zh-v1-retired-20260801 `
  --mark-token <MARK_TOKEN>
```

成功输出应包含 `"swept": true`，且 `deleted_record_count` 等于 mark 时记录数。不要把示例 collection
直接替换成 active 或 previous；程序仍会 fail closed，但人工操作必须先核对 plan。

## 6. 测试和专项验证

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -X utf8 -m scripts.verify_vector_collection_gc
python -X utf8 -m scripts.verify_indexing_lease
python -X utf8 -m scripts.verify_vector_collection_release
python -X utf8 -m scripts.verify_ci_configuration
```

专项 verifier 使用确定性 SQL 状态机替身，不连接 PostgreSQL、不加载 BGE、不调用 LLM。它证明控制流和
事务契约，不冒充真实数据库删除验收。真实删除必须在隔离 PostgreSQL 中使用管理 CLI 验证。

## 7. 为什么选择数据库 mark/sweep

| 方案 | 优点 | 当前项目不选择的原因 |
|---|---|---|
| 定时任务直接 DELETE | 实现少 | 检查与删除间有竞态，没有审批窗口和 receipt |
| 只保留最近 N 个名称 | 规则直观 | 命名不等于发布状态，可能删除 active/previous |
| 对象存储生命周期规则 | 自动化成熟 | pgvector 行不在对象存储，无法理解 lease/pointer |
| PostgreSQL mark/sweep（当前） | 与 pointer、lease 和删除同事务协调 | 需要 mark 表和运维命令 |

## 8. 生产环境不足

- CLI 尚未接入管理员 RBAC、双人审批或变更单；
- 没有自动调度器，本阶段故意要求显式 mark 和 sweep；
- 没有存储水位、候选数量、保护拒绝和删除量 Prometheus 指标；
- 没有 partition drop，超大 collection 的行级 DELETE 可能产生 WAL 和表膨胀；
- 没有批量/限速 sweep、VACUUM 调度或租户级配额；
- CI 是无数据库状态机验证，真实 PostgreSQL 多进程竞态仍需集成测试；
- `PgVectorIndex.delete_collection()` 仍是底层测试工具，生产 GC 必须走本阶段管理器。

## 9. 面试官可能追问

**为什么 dry-run 不够？**

Dry-run 是某一时刻的观察结果。Mark 固化删除意图和 generation，Sweep 再检查最新状态，才能覆盖观察后发生
的发布、构建或向量变化。

**为什么 Mark 需要 fencing token？**

Mark 后可能有新 Builder acquire/release。即使 sweep 时没有活跃 lease，generation 已经变化，旧 mark
也必须失效，否则可能删除新构建结果。

**为什么不能删除 lease 控制行？**

控制行保存单调 generation。删除或归零会让旧 Writer 的 token 再次变得可接受，也会失去 collection
生命周期的稳定协调点。

**为什么比较记录数还要比较最后更新时间？**

相同数量并不代表相同内容；一删一增或原地更新都可能保持 count 不变。Fencing 防规范 Builder，时间和
count 重验证还能对未经过 lease 的意外直接写入 fail closed。

**大量数据如何优化？**

生产规模可按物理 partition 保存 collection，Sweep 使用 detach/drop partition，并结合限速、WAL 预算、
replica lag 和 VACUUM 监控；安全门禁仍应保留。
