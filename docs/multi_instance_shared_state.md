# Phase 38：Multi-instance Shared State

## 1. 当前结论

本设计最初以 GitHub `main` 的 `b37277953a663930cc01a251b5f0de71d7284b4f` 为审计基线，
Step 1 已由 `58b64a6fe4d6345c68f1b944527dd82892d89369` 提交。代码、测试和 CI 表明
Advanced RAG Phase 37 已完成；Phase 38 Step 3.3 已增加 PostgreSQL Submission/Audit Repository，但还没有切换运行时。

当前 Agent 的 checkpoint、会话投影、草稿快照、对话记忆、提交回执、幂等记录和提交审计，
全部写入 `SQLITE_DATABASE_PATH` 指向的同一个 SQLite 文件。Compose 只启动一个 Agent，
并把这个文件放在 `agent_runtime` 具名卷中。该方案能通过单实例重启恢复，但不能让两个拥有独立
文件系统的 Agent 实例看到同一份状态。

PostgreSQL 已用于 pgvector、Collection Release、Indexing Lease 和 Collection GC；Redis 已用于
可丢失、可重建的 LLM 响应缓存。这两类现有能力可以复用，但 Phase 38 不修改 RAG 检索、安全顺序
或 Collection 控制面。

本阶段的机器可读事实来源是
[`docs/multi_instance_state_inventory.json`](multi_instance_state_inventory.json)。Step 1 建立清单和验证
契约；Step 2 增加显式 PostgreSQL schema setup/status 能力；Step 3.1–3.3 增加未接线的
Session/Draft/Conversation/Submission/Audit Repository。FastAPI 仍使用 SQLite，也没有复制数据。

## 2. 本阶段解决什么问题

目标拓扑为：

```mermaid
flowchart TD
    LB[Load Balancer]
    A[Agent A]
    B[Agent B]
    PG[(PostgreSQL)]
    R[(Redis)]
    LB --> A
    LB --> B
    A --> PG
    B --> PG
    A --> R
    B --> R
```

必须成立两条业务契约：

1. 请求在 A、B 之间切换时，相同 `session_id` 仍能继续同一 LangGraph workflow；
2. A 在提交前后崩溃时，B 能恢复草稿，并且同一已确认草稿最多产生一个提交回执。

当前实现无法保证这两条契约，原因不是 SQLite 不能持久化，而是每个实例拥有不同的状态副本；
同时 `AgentWorkflow._session_locks`、single-flight、Provider 队列和运行指标都只存在于单个 Python
进程中。

## 3. 当前状态清单

### 3.1 Durable state

| 状态 | 当前后端 | 当前事实来源 | Phase 38 目标 | 关键约束 |
| --- | --- | --- | --- | --- |
| LangGraph checkpoint / blobs / writes | SQLite | workflow 恢复事实来源 | PostgreSQL | thread 内有序、可中断恢复 |
| Agent session head | SQLite | checkpoint 的查询投影 | PostgreSQL | 单调 revision，禁止旧 turn 覆盖 |
| Application draft revisions | SQLite | checkpoint 的版本化投影 | PostgreSQL | `(draft_id, revision)` 不可变 |
| Sanitized conversation memory | SQLite | follow-up context 来源 | PostgreSQL | 原子 turn、脱敏、截断、50 轮上限 |
| Submitted application + approval workflow | SQLite | 副作用回执事实来源 | PostgreSQL | idempotency key 和 draft 都唯一 |
| Submission audit | SQLite | 提交与 replay 审计事实来源 | PostgreSQL | 与提交事务一致、追加式 |
| Vector / release / lease / GC | PostgreSQL | RAG 控制面事实来源 | 保持 PostgreSQL | 不改变 fencing、CAS、GC 保护 |

`agent_sessions` 和 `application_draft_snapshots` 是便于查询的投影；LangGraph checkpoint 是 workflow
恢复事实来源。`approval_submissions` 则是副作用是否已经发生的事实来源。Phase 38 不使用跨数据库
分布式事务：投影失败可以从 checkpoint 重建，但提交回执与提交审计必须在一个 PostgreSQL 事务中
完成。

### 3.2 Ephemeral or reconstructable state

| 状态 | 当前范围 | 决策 |
| --- | --- | --- |
| Redis LLM cache | 跨实例（启用时） | 保留；继续 fail-open，绝不存 workflow 正确性状态 |
| Session `asyncio.Lock` | 单进程 | Phase 38 改为 token-owned Redis lease，并保留数据库冲突保护 |
| Async single-flight registry | 单进程 | Phase 43 再做分布式实验；不影响 Phase 38 正确性 |
| Provider FIFO / concurrency | 单进程 | Phase 43 处理全局配额；本阶段不伪装成集群限制 |
| HTTP / security / provider metrics | 单进程 | Phase 40 集中聚合；本阶段只增加必要的 backend/conflict 指标 |
| In-memory vector provider | 单进程、可重建 | 本地测试可用；multi-instance profile 必须显式使用 pgvector |
| Model artifact cache | 文件系统、可重建 | 可按实例缓存；不能放业务状态或身份信息 |

## 4. Architecture Decision

### 4.1 Durable state 为什么选 PostgreSQL

候选方案：

| 方案 | 优点 | 问题 | 决策 |
| --- | --- | --- | --- |
| 共享 SQLite 文件 | 改动最少 | 容器卷和文件锁不适合作为横向扩展一致性边界；故障域仍是文件 | 拒绝 |
| 全部放 Redis | 延迟低、TTL 和锁方便 | workflow、提交和审计可能因淘汰或持久化策略丢失；事务查询能力不足 | 拒绝 |
| PostgreSQL durable + Redis ephemeral | 复用现有基础设施；事务、唯一约束和行级并发清晰 | 需要 schema、repository、迁移和集成测试 | 采用 |

PostgreSQL 是 durable source of truth。Redis 只保存有 TTL、可重建的协调状态。Redis 中不能存唯一的
草稿、确认状态、提交结果、身份、权限或审计记录。

### 4.2 不做永久 dual-write

长期同时写 SQLite 和 PostgreSQL 会产生新的“双事实来源”问题。最终切换采用显式 provider：

- `sqlite` 仅保留给单机开发和迁移前兼容；
- `postgresql` 用于 multi-instance profile；
- PostgreSQL 配置失败时 fail closed，不静默回退到本地 SQLite；
- 历史数据通过一次性、可重复校验的导入命令迁移，切换窗口内停止写入。

### 4.3 LangGraph checkpoint

优先采用 LangGraph 官方 PostgreSQL checkpointer，并在项目内增加生命周期适配层：

- 复用现有 `BaseCheckpointSaver` 注入边界和当前 serializer allowlist；
- 固定并验证兼容版本，启动时显式执行幂等 schema setup；
- 使用 async PostgreSQL pool，避免把数据库 I/O 移到无限 worker thread；
- 用现有 checkpoint contract、HITL interrupt/resume 和 delete-thread 测试验收；
- 应用表仍使用项目自己的 repository，不把业务草稿或提交塞进通用 LangGraph Store。

这样减少自写 checkpoint 协议的风险，同时不改变 `AgentWorkflow` 图结构。

### 4.4 Repository 边界

现有接口可以直接复用：

- `BaseCheckpointSaver`：替换 checkpointer；
- `AgentStatePersister`：扩展 PostgreSQL session/draft projection；
- `ConversationMemoryStore`：扩展 PostgreSQL conversation store；
- `ApplicationSubmitter`：扩展 PostgreSQL idempotent submitter；
- `Settings` 和 FastAPI lifespan：增加显式 provider factory 和资源关闭。

不新建另一套 Agent、Workflow、Draft、Submission 或 RAG 模型。

## 5. 一致性设计

### 5.1 同一 session 的并发请求

Phase 38 使用两层保护：

1. Redis session lease 做正常路径的跨实例互斥；
2. PostgreSQL revision/unique constraints 做最终正确性保护。

Redis lease 使用随机 owner token、`SET NX PX`、compare-and-renew、compare-and-delete 和 heartbeat。
Redis key 只包含 session ID 的摘要，不保存用户正文。锁丢失后当前请求不得进入提交节点；API 返回稳定的
冲突/暂不可用结果，由客户端重新读取最新状态，不能自动重试副作用。

数据库仍必须拒绝：

- 旧 session revision 覆盖新 revision；
- 同一 idempotency key 绑定不同 draft/user/session；
- 同一 draft 绑定第二个 submission；
- 同一 conversation turn/role 重复插入。

### 5.2 提交和崩溃窗口

提交事务同时写入 submission receipt 和 audit record。结果分三种：

| 崩溃位置 | Agent B 的行为 |
| --- | --- |
| PostgreSQL commit 前 | 没有回执；从已确认 draft 恢复并允许用户再次显式提交 |
| commit 后、HTTP response 前 | 相同确定性 idempotency key 命中原回执，返回 replay，不创建第二单 |
| checkpoint/projection 更新失败 | submission receipt 优先；恢复时按回执把 workflow 投影修正为 submitted |

### 5.3 Session reset

当前 reset 依次删除 checkpoint、session/draft projection 和 conversation，任一步失败都可能留下部分状态。
PostgreSQL 版本先原子写入 session tombstone，使后续 resume fail closed，再幂等清理 checkpoint 和历史；
只有 tombstone 成功后 API 才能报告 session 已清除。审计和提交回执不随普通 session reset 删除。

## 6. Failure Mode

| 故障 | 行为 |
| --- | --- |
| PostgreSQL 不可用 | readiness 失败；workflow mutation 返回 503；绝不回退 SQLite |
| Redis cache 不可用 | 保持现有 cache fail-open，直连 LLM |
| Redis session coordinator 不可用 | multi-instance workflow mutation fail closed；不能假装仍有互斥 |
| Agent A crash | lease TTL 后由 B 接管；B 从 PostgreSQL checkpoint 恢复 |
| lease heartbeat 丢失 | 在任何副作用前再次验证；丢锁请求停止 |
| 两实例同时提交 | PostgreSQL unique constraints 只允许一个 first submission，另一个读取 replay |
| stale projection | checkpoint/receipt 为事实来源，projection 通过 reconciliation 修复 |
| 迁移中断 | 导入按自然键幂等；校验数量和摘要后才切换 provider |

## 7. Security

- 保持 `PromptInjectionGuard` 在 `AgentRouter.route()` 调用 workflow 之前执行；
- 保持 Trusted Identity → Authorization → authorized Vector/BM25 → RRF → Reranker → Context；
- 状态后端不能从自然语言或请求 body 推导角色、部门、clearance；
- Redis key 不含 prompt、对话正文、员工姓名或数据库凭据；
- PostgreSQL JSONB 仍保存现有脱敏后的 conversation content；日志只记录 backend、operation、outcome、
  latency 和冲突类型；
- Session 与身份绑定的强制验证属于 Phase 39 Authentication/RBAC，但 Phase 38 schema 要预留稳定 owner key，
  不能把客户端自报身份写成可信 owner；
- 普通 reset 不删除 append-only submission audit。

## 8. Observability

Phase 38 至少记录：

- state operation duration / error / conflict；
- session lease acquire、wait、timeout、lost、release；
- checkpoint resume source instance 与 backend；
- idempotent first-submit / replay / conflict；
- PostgreSQL 和 Redis 独立 readiness。

指标标签必须有界，不使用原始 session ID、draft ID、employee ID、prompt 或异常正文。跨实例 trace 和集中指标
聚合留给 Phase 40。

## 9. 测试策略

### Unit

- schema 和 repository SQL 契约；
- serializer allowlist 与 checkpoint CRUD；
- Redis token ownership、renew、release 和丢锁；
- revision CAS、idempotency 与 audit transaction。

### Integration

- 真实 PostgreSQL schema migration / rollback-safe setup；
- 真实 Redis lease TTL 和 owner-token 校验；
- 两个独立 Router/App 实例共享 PostgreSQL/Redis。

### Failure and recovery

- A 建 draft，B 修改和确认；
- A 在等待确认时退出，B resume；
- commit 后模拟 response 丢失，再由 B 重试；
- Redis、PostgreSQL、Agent 分别失效；
- 并发相同 session，只允许一个 mutation owner；
- reset 中断后 session 仍 fail closed。

### Security regression

- 未授权 Chunk 仍在 Vector/BM25 评分前排除；
- prompt injection 仍在 retrieval/workflow/tool 前阻断；
- 不可信身份不能绑定或读取另一用户 session；
- 锁、日志、metric 不暴露敏感正文。

## 10. Phase 38 分步计划

| Step | 目标 | 主要产物 | 进入下一步的门槛 |
| --- | --- | --- | --- |
| 1 | State Inventory | 机器清单、ADR、离线 verifier、pytest | 所有 SQLite 表与单实例依赖都有明确归属 |
| 2 | PostgreSQL configuration + schema | 独立 runtime DSN、migration、DDL | 幂等建表；尚不切换 runtime |
| 3 | PostgreSQL repositories | session/draft、memory、submission/audit | 事务、CAS、唯一约束和 repository tests 通过 |
| 4 | PostgreSQL checkpointer | 官方 saver 生命周期适配、HITL 恢复 | A 写 checkpoint，B 可 resume |
| 5 | Redis session coordination | token lease、heartbeat、lost-lock guard | 同 session 跨实例互斥；丢锁不提交 |
| 6 | Runtime cutover + one-time import | provider factory、lifespan、health、SQLite importer | 无静默 fallback；导入可校验、可重跑 |
| 7 | Multi-instance / failover tests | A/B 连续办理、crash/replay/no-duplicate | 两实例和故障验收全部通过 |
| 8 | Documentation + CI gate | README、Compose 验收、CI services/evidence | 全量 pytest/Ruff/专项/真实依赖 gate 通过 |

当前已完成 Step 1–2 和 Step 3.1–3.3；Step 3.4 的真实 PostgreSQL 集成/并发测试、隔离 Compose profile 与
CI Gate 已就绪，仍需真实数据库运行结果才能标记完成。checkpointer、Redis coordinator、runtime 和 Compose
runtime 切换均未开始。

## 11. Step 1 完成标准

- GitHub `main` commit、README、目录、tests/scripts/docs/workflow 已审计；
- `docs/multi_instance_state_inventory.json` 覆盖 SQLite schema 中全部 8 张表；
- 每个 durable SQLite asset 的目标后端都是 PostgreSQL；
- Redis 只承载 ephemeral state；
- 所有 correctness/continuity 单实例依赖有明确 Phase 38 处置；
- Phase 40/43 的指标聚合、distributed single-flight 和 global backpressure 明确延后；
- verifier、pytest、Ruff 和现有专项验证通过；
- runtime migration 仍为 `false`。

## 12. Step 2：PostgreSQL configuration + schema

### 12.1 本步范围

Step 2 增加独立于 RAG pgvector 的 Agent runtime 配置：

- `AGENT_STATE_PROVIDER`：默认仍为 `sqlite`；
- `AGENT_POSTGRES_DSN`：独立 SecretStr，不复用 `RAG_PGVECTOR_DSN` 命名；
- `AGENT_POSTGRES_MIN_POOL_SIZE` / `MAX_POOL_SIZE`：为 Step 3–4 的 repository/checkpointer 预留；
- `AGENT_POSTGRES_CONNECT_TIMEOUT_SECONDS`：有界连接超时。

本步不让 `app/main.py` 根据 Provider 构造 PostgreSQL repository；该切换属于 Step 6。因此即使把
`AGENT_STATE_PROVIDER` 写成 `postgresql`，当前 FastAPI 也不会假装已经完成迁移。

### 12.2 固定 schema 与迁移

Schema 名固定为 `agent_runtime`，不接受客户端或环境变量提供的 SQL identifier，避免标识符注入和不同实例
写入不同 schema。`schema_migrations` 保存当前版本，setup 在事务内锁定 migration table：

| 表 | 用途 | 关键约束 |
| --- | --- | --- |
| `schema_migrations` | schema version | 只允许正整数版本；拒绝比应用更新的数据库 |
| `agent_sessions` | session 查询投影和 CAS head | `state_version`、`deleted_at`、owner 成对出现 |
| `application_draft_snapshots` | 不可变 draft revision | `(draft_id, revision)` 主键、JSONB payload |
| `conversation_messages` | 已脱敏的多轮上下文 | `(session_id, turn_number, role)` 唯一 |
| `approval_submissions` | submission/idempotency 事实来源 | idempotency key、submission ID、draft ID 分别唯一 |
| `submission_audit_records` | first-submit/replay 审计 | 引用 submission，`ON DELETE RESTRICT` |

业务 payload 使用 JSONB 保留现有 Pydantic 模型的完整快照；可查询字段继续单独成列。时间统一使用
`TIMESTAMPTZ`。`owner_subject` 和 `owner_identity_source` 当前允许同时为空，仅为 Phase 39 的可信认证身份
预留，Step 2 不从用户文本或 request body 写入 owner。

LangGraph 官方 checkpoint tables 不在本迁移中创建；它们由 Step 4 的 checkpointer adapter 执行兼容版本的
官方 setup，避免当前 schema 与第三方迁移历史互相伪装。

### 12.3 管理命令

```powershell
docker compose up -d postgres

$env:AGENT_POSTGRES_DSN = "postgresql://policy_agent:local-development-only@127.0.0.1:5432/policy_agent"

python -X utf8 -m scripts.manage_agent_state_schema setup
python -X utf8 -m scripts.manage_agent_state_schema status
```

`setup` 可重复执行；`status` 只读检查 version、required tables 和 required columns。两者都不输出 DSN。

### 12.4 Step 2 完成标准

- 默认 Provider 仍为 SQLite；
- DSN 使用 SecretStr，连接池范围和 timeout 有校验；
- fresh setup 得到 schema version 1，重复 setup 不重新执行 migration 1；
- 缺表、缺列或数据库版本过新均 fail closed；
- submission/idempotency/audit 约束由 PostgreSQL DDL 明确表达；
- 离线 verifier 不访问 PostgreSQL、Redis、模型或网络；
- 真实 PostgreSQL `setup` 与 `status` 都返回 `ready: true`；
- `runtime_backend_switched` 与 `sqlite_data_migrated` 仍为 `false`。

## 13. Step 3.1：PostgreSQL Session/Draft Repository

Step 3.1 只实现未接入运行时的异步 Repository 边界：

- `PostgresStateConnectionPool` 以 Protocol 注入，Repository 不读取 DSN、不创建或关闭连接池；
- `save_route_state()` 在一个连接事务中写 session head 和 active draft revision；
- session 首写使用 `ON CONFLICT DO NOTHING`，后续锁行并以 `state_version` CAS 防止旧 turn 覆盖；
- 相同 turn、相同投影可幂等重放，相同 turn 的不同投影及 tombstone 复活会被拒绝；
- draft 的 `(draft_id, revision)` 业务内容不可覆盖；现有 workflow 所需的确认、取消、提交状态仅允许
  单调生命周期转换，并使用旧 status 做 CAS；
- reset 先写 session tombstone，再清理该 session 的 draft projection；submission/audit 不在本子步骤中删除；
- 查询忽略 tombstoned session，latest revision 降序选取，revision list 保持升序。

离线验收命令不访问数据库或外部服务：

```powershell
python -X utf8 -m scripts.verify_postgres_session_draft_repository
python -X utf8 -m pytest tests/unit/test_postgres_agent_state_store.py tests/unit/test_verify_postgres_session_draft_repository.py -q
```

Step 3.1 不实现真实 pool lifecycle、Conversation、Submission/Audit、LangGraph checkpoint、Provider factory、
SQLite import 或 FastAPI 接线；这些边界继续留给 Phase 38 后续子步骤。因而
`runtime_backend_switched` 与 `sqlite_data_migrated` 仍为 `false`。

## 14. Step 3.2：PostgreSQL Conversation Repository

Step 3.2 实现 `ConversationMemoryStore` 的 PostgreSQL 版本，但不接入运行时：

- user/assistant 内容在借用数据库连接前完成既有 secret redaction、空白检查和长度截断；
- append 先锁定 live `agent_sessions` 行，再计算下一 turn，跨实例串行分配 turn number；
- user/assistant 使用单条 INSERT 原子写入；唯一约束只接受完整的两行结果，否则整个事务回滚；
- 每次 append 在同一事务内按 turn number 删除保留窗口之前的完整旧 turn；
- snapshot 用单条窗口查询同时取得 retained count 和最近消息，保持 SQLite 的最新截取、时间正序返回语义；
- tombstoned session 的历史不可读取或追加，但 reset 仍可锁行并幂等清理该 session 的 conversation rows。

离线验收命令：

```powershell
python -X utf8 -m scripts.verify_postgres_conversation_repository
python -X utf8 -m pytest tests/unit/test_postgres_conversation_memory.py tests/unit/test_verify_postgres_conversation_repository.py -q
```

本子步骤不创建连接池、不接入 `app/main.py`，也不实现 Submission/Audit、Checkpoint、数据迁移或双写。

## 15. Step 3.3：PostgreSQL Submission/Audit Repository

Step 3.3 实现 PostgreSQL mock approval submitter，提交回执与审计共同构成不可随 session reset 删除的
事实来源：

- 所有既有显式确认、草稿完整性、可信申请人和 session 归属校验都在访问数据库前执行；
- 首次提交在同一事务写入唯一 submission receipt 和 `submitted` audit；任一写入失败会整体回滚；
- 相同 idempotency key 锁定并校验 draft/session/employee 绑定，返回原 submission/workflow 并追加
  `idempotent_replay` audit；
- idempotency key、submission ID 和 draft ID 的数据库唯一约束是并发最终保护；
- 两实例同时 INSERT 时，失败方重新读取数据库胜者；相同 key 返回 replay，不同 key 绑定同一 draft 则冲突；
- audit 只追加，不提供 update/delete；查询按 `recorded_at, audit_id` 稳定排序；
- `get_submission(draft_id=...)` 支持 reset 后恢复已产生的副作用回执。

离线验收命令：

```powershell
python -X utf8 -m scripts.verify_postgres_submission_repository
python -X utf8 -m pytest tests/unit/test_postgres_submission_repository.py tests/unit/test_verify_postgres_submission_repository.py -q
```

本子步骤不接入运行时，也不执行真实审批、SQLite import、双写或跨数据库事务。Step 3.4 将使用真实
PostgreSQL 验证 DDL、事务回滚、并发 first-submit/replay、retention 和 tombstone 行为。

## 16. Step 3.4：真实 PostgreSQL Repository 集成与并发验收

Step 3.4 增加独立的真实数据库 gate，不接入 FastAPI runtime：

- 测试 DSN 必须显式来自 `AGENT_POSTGRES_TEST_DSN`，且数据库名必须以 `_test` 结尾；否则跳过或
  fail closed，避免 `TRUNCATE` 误操作开发/生产库；
- `postgres-test` Compose profile 使用 `policy_agent_test`、独立端口和 tmpfs，不复用 runtime volume；
- pytest-asyncio integration scope 显式使用 `SelectorEventLoop`，兼容 Windows 上 Psycopg 不支持默认
  `ProactorEventLoop` 的限制，同时不改变应用运行时事件循环；
- 每个测试先清空五张业务表，但保留 versioned schema migration 历史；
- 验证 pool 关闭/重建后 Session 仍可恢复、同 turn 不同 head 只有一个 CAS 胜者；
- 验证 Session tombstone 与 Draft projection 清理事务语义；
- 10 个并发 Conversation append 必须产生连续、完整的 user/assistant turn pair；
- 8 个并发同 key Submission 必须只有一个首次回执和七个 replay audit；
- 同一 Draft 的两个不同 key 并发提交必须只有一个胜者，失败方返回稳定冲突；
- GitHub Actions 使用真实 PostgreSQL service 执行六个 integration tests，并上传 JUnit XML。

Windows PowerShell 本地验收：

```powershell
docker compose --profile integration up -d --wait postgres-test

$env:AGENT_POSTGRES_DSN = "postgresql://policy_agent:local-integration-only@127.0.0.1:55432/policy_agent_test"
$env:AGENT_POSTGRES_TEST_DSN = $env:AGENT_POSTGRES_DSN

python -X utf8 -m scripts.manage_agent_state_schema setup
python -X utf8 -m scripts.manage_agent_state_schema status
python -X utf8 -m pytest tests/integration/test_postgres_repositories.py -m postgres_integration -q
python -X utf8 -m scripts.verify_postgres_repository_integration_gate

docker compose --profile integration down
Remove-Item Env:AGENT_POSTGRES_DSN
Remove-Item Env:AGENT_POSTGRES_TEST_DSN
```

离线 verifier 只证明 gate、隔离保护和 CI 接线完整，状态为 `integration_gate_ready`；Step 3.4 只有在上述
真实数据库测试或等价 GitHub Actions job 返回六项通过后才能标记完成。

## 17. 生产环境仍有的不足

即使 Phase 38 完成，系统仍缺少 Phase 39 的真实认证与 session ownership enforcement、Phase 40 的集中
trace/metrics/log、数据库备份恢复演练、跨区域容灾、密钥轮换、容量基线和正式 SLO。Phase 38 只证明共享
状态和故障接管，不等于完整生产就绪。

## 18. 面试官可能追问

### 为什么不把 workflow 全放 Redis？

Redis 适合 TTL cache 和短租约；workflow、确认、提交与审计需要事务、唯一约束、可查询历史和明确恢复点。
因此 PostgreSQL 是事实来源，Redis 只协调。

### Redis lock 能保证 exactly-once 吗？

不能。租约可能过期或在网络分区中失去所有权。锁减少并发，真正防重依赖 PostgreSQL 唯一约束、确定性
idempotency key 和提交回执 replay。对外表达应是“effectively-once business effect”，不是网络层
exactly-once。

### 为什么 checkpoint 和 session projection 不做分布式事务？

checkpoint 是恢复事实来源，session/draft head 是可重建投影。把所有写入强耦合会扩大事务和库适配复杂度。
关键副作用提交与审计必须同事务；投影则通过 revision 和 reconciliation 达到一致。

### Agent A commit 后崩溃，B 为什么不会重复提交？

确认后的 draft 生成稳定 idempotency key；PostgreSQL 同时对 idempotency key 和 draft ID 建唯一约束。
B 的相同请求读取已存在的 receipt，返回 replay，而不是再次执行 first submission。

### 为什么 Phase 38 不顺手做 distributed single-flight 和 global backpressure？

它们解决 Provider 成本与容量，不决定 workflow 正确性。先完成状态事实来源、跨实例恢复和副作用防重；
全局 Provider 协调需要独立容量与故障实验，放在 Phase 43 更容易验证和回滚。
