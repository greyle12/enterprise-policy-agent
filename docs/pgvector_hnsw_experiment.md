# Phase 34：pgvector HNSW Recall–nDCG–p95 实验

## 1. 它是什么

HNSW（Hierarchical Navigable Small World）是 pgvector 支持的近似最近邻索引。它用多层邻接图减少
向量搜索需要访问的节点数量，通常能在大语料上降低延迟，但可能漏掉 exact cosine search 本来会返回的
近邻。

Phase 34 不把 HNSW 直接设为生产默认值，而是提供 exact 与多组 HNSW 参数的受控实验：

```text
相同 Query / judgments / Embedding / authorized IDs
                │
                ├── pgvector exact cosine baseline
                │
                └── isolated authorized HNSW graph
                         ├── m
                         ├── ef_construction
                         └── ef_search
```

## 2. 为什么需要

Phase 29 的 pgvector 使用精确余弦检索。199 个 Chunk 时 exact 足够简单可靠，但语料扩大后线性扫描
成本会上升。直接加入 HNSW 技术名词没有意义，必须回答：

- ANN 相对 exact Top-5 漏了多少近邻？
- 人工 judgments 上 Recall/MRR/nDCG 是否下降？
- 查询 p95 改善多少？
- 索引构建成本是多少？
- 参数变化是否仍满足权限边界？

## 3. 最重要的安全设计

禁止以下流程：

```text
全库 HNSW Top-K
→ 删除无权限结果
```

全局 ANN 图即使 SQL 最终不返回无权限记录，图遍历仍可能让无权限节点参与候选导航，而且过滤后可能
不足 Top-K。本项目采用更强的实验隔离：

```text
Trusted evaluation identity
→ authorized_chunk_ids(...)
→ COPY only authorized rows into isolated UNLOGGED table
→ build HNSW on that table
→ vector similarity
```

`PgVectorHnswExperimentIndex` 还要求每次 `search()` 传入的授权集合与建图集合完全一致，防止把为身份 A
构建的图复用于身份 B。实验表名只接受内部生成或严格十六进制 ID，避免动态 SQL identifier 注入。

## 4. 输入与输出

输入：

- PostgreSQL DSN 和专用实验 collection；
- 20 条查询、30 个 graded judgments；
- `offline` 确定性 Embedding 或真实 BGE；
- HNSW `m:ef_construction:ef_search` 参数列表；
- warm-up、repetitions 和质量阈值。

输出：

- `pgvector-hnsw-experiment-report.json`；
- `pgvector-hnsw-experiment-report.md`；
- exact 与每个 HNSW 点的质量、延迟、构建耗时和 Pareto 标记；
- 退出码 `0/1/2`：通过、质量失败、参数/数据库/运行错误。

## 5. 指标为什么分两类

### ANN Recall@5

以 exact Top-5 为近邻真值：

\[
ANNRecall@5 = \frac{|HNSW@5 \cap Exact@5|}{|Exact@5|}
\]

它只衡量近似索引损失，不代表制度答案是否正确。

### Judged Recall@5、MRR@5、nDCG@5

它们使用人工 judgments：Recall 判断相关证据是否召回，MRR 判断首条相关证据位置，nDCG 使用 G1/G2/G3
判断高价值证据是否靠前。一个 HNSW 点可能 ANN Recall 很高，但 Embedding 检索质量仍不达标；也可能
漏掉 exact 近邻，却恰好没有影响人工标注相关证据。两组指标必须同时看。

## 6. 参数含义

| 参数 | 影响 | 常见权衡 |
|---|---|---|
| `m` | 每个节点的图连接数量 | 增大通常提升召回，也增加索引体积和构建成本 |
| `ef_construction` | 建图时的候选宽度 | 增大通常提升图质量，但建图更慢 |
| `ef_search` | 查询时的候选宽度 | 增大通常提升 ANN Recall，也增加查询延迟 |

本阶段默认比较 `8:32:20`、`16:64:40`、`16:64:80`，当前门禁配置为
`16:64:40`。这些是实验起点，不是生产最优结论。

## 7. 运行方式

先启动已有 pgvector 服务：

```powershell
docker compose up -d postgres
docker compose ps postgres
```

使用确定性 Embedding 验证真实 PostgreSQL/HNSW：

```powershell
python -X utf8 -m scripts.run_pgvector_hnsw_experiment `
  --mode offline `
  --hnsw-config 8:32:20 16:64:40 16:64:80 `
  --default-config 16:64:40 `
  --warmups 1 `
  --repetitions 3
```

使用真实 BGE 和固定 CPU：

```powershell
python -X utf8 -m scripts.run_pgvector_hnsw_experiment `
  --mode bge `
  --hnsw-config 8:32:20 16:64:40 16:64:80 `
  --default-config 16:64:40 `
  --warmups 1 `
  --repetitions 3 `
  --device cpu `
  --embedding-batch-size 32
```

专项方法验证不连接数据库：

```powershell
python -X utf8 -m scripts.verify_pgvector_hnsw_experiment
```

CLI 默认使用专用 collection 前缀 `enterprise-policy-ann-experiment`，并追加 mode 与实际模型维度，例如
`enterprise-policy-ann-experiment-offline-1024d` 或 `enterprise-policy-ann-experiment-bge-512d`。这避免
确定性向量和 BGE 向量互相覆盖，也不要把 `--collection` 指向生产 collection。DSN 不写入
报告，也不应提交真实密码。

## 8. 架构选择与替代方案

可选方案包括：

- 在全库 HNSW 查询中增加 `WHERE allowed_id`：实现简单，但图仍由全库节点组成，不满足本项目的强安全顺序；
- 按部门/安全等级建立固定 partition/index：查询快，但角色、区域、日期组合会造成分区爆炸；
- pgvector iterative scan：能改善过滤后结果数量，但仍不等于授权集合先建图；
- 外部向量数据库 namespace/tenant index：更适合大规模多租户，但会引入另一套存储和运维边界；
- 本阶段隔离实验表：安全语义清楚、参数可重复，代价是需要额外建表和索引，适合实验而非每请求创建。

当前项目选择隔离实验表，是因为目标是安全地取得 Recall–Latency 证据，而不是提前宣布生产 ANN 架构。

## 9. 生产不足

- 199 Chunk 太小，PostgreSQL 可能更偏好顺序扫描，因此实验显式关闭 seqscan；结果不能外推到百万级语料；
- 没有并发 Query、缓存冷热状态、数据库 shared buffers 和 I/O 指标；
- 没有多租户授权索引发布、版本切换和回滚方案；
- 没有双人完整 pool judgments、置信区间或显著性检验；
- UNLOGGED 实验表不具备生产持久性承诺，进程异常时可能残留，需要运维清理策略；
- CI 只运行无数据库方法验证，真实 PostgreSQL/BGE 报告必须在固定硬件手动生成。

## 10. 面试追问

**ANN Recall 和业务 Recall 有什么区别？**

ANN Recall 比较 HNSW 与 exact 近邻；业务 Recall 比较返回结果与人工相关性标注。前者定位索引近似损失，
后者评价整个 Embedding 检索是否找到正确制度证据。

**为什么小数据上 HNSW 可能更慢？**

图遍历、配置和索引访问有固定开销；199 条记录的 exact 扫描非常便宜。HNSW 的优势通常在更大数据规模
才出现，所以不能只凭技术趋势替换 exact。

**为什么索引构建耗时不放进 p95？**

线上查询通常复用已经构建的索引。将一次性建图成本混入每次 Query 会扭曲服务延迟；因此单独报告
`index_build_ms`，查询 p95 只覆盖 Query Embedding 和检索调用。

**最终如何上线？**

先扩大语料和 Query，固定硬件重复实验，选择满足 ANN Recall、Judged Recall/MRR/nDCG 和 SLA 的 Pareto
点；再设计授权分区、蓝绿索引、回滚和监控，经过配置评审后显式切换，实验脚本不自动上线。
