# 企业制度 Agent：简历与技术面试讲解材料

本材料用于把代码事实转换成可追问的项目表达。所有数字都应能在仓库脚本、测试或报告中复验。

## 1. 90 秒项目介绍

> 我独立开发了一个企业制度问答与流程办理 Agent。它不是普通 PDF 问答：系统先解析带版本、
> 有效期和权限元数据的企业制度，通过 BGE Vector 与 BM25 双路召回、RRF 融合构建带条款引用的上下文；然后使用
> LangGraph 对制度问答、材料检查、审批路线、申请草稿、人工确认和模拟提交进行编排。
> 金额、材料和审批规则由确定性 Python 代码计算，LLM 只负责意图理解和自然语言生成。
> 工程侧实现了 SQLite 重启恢复、幂等提交、受限会话记忆、Redis LLM 缓存、single-flight、
> Provider 背压、请求 ID、Prometheus 指标、RAG 权限过滤和提示注入防护。项目有 30 条黄金
> 用例、离线性能与并发报告，以及一键 6 场景作品集演示，并通过 GitHub Actions 自动门禁。

## 2. 简历描述

### 中文版本

**企业制度问答与流程办理 Agent｜Python、FastAPI、LangGraph、RAG、SQLite、Redis、Docker**

- 设计企业制度 RAG：Markdown/PDF/DOCX/OCR、条款级 Chunk、BGE Vector、BM25 与 RRF Hybrid、
  结构化 Context 和 `S1/S2` 引用；加入生命周期、等级、部门、角色和区域检索前授权。
- 使用 LangGraph 编排制度问答、材料检查、审批路线、申请草稿、人工确认与模拟提交；金额、
  材料、审批链和幂等性由确定性代码执行，SQLite 支持会话与审计跨重启恢复。
- 实现 Redis 精确 LLM 缓存、异步 single-flight、Provider 有界并发/FIFO 背压、超时重试、
  请求关联和 Prometheus 指标；对提示注入和污染制度证据进行执行前拒绝与隔离。
- 建立 30 条黄金用例、五项准确率门禁、离线性能/并发/批处理报告和 6 场景作品集演示，
  通过 pytest、Ruff、Wheel、Docker 构建及 GitHub Actions 固化交付证据。

### English version

**Enterprise Policy Q&A and Workflow Agent | Python, FastAPI, LangGraph, RAG, SQLite, Redis, Docker**

- Built a policy-grounded RAG pipeline with article-level parsing, BGE embeddings, verifiable citations,
  lifecycle metadata, and pre-retrieval authorization by clearance, department, role, and region.
- Orchestrated policy Q&A, material checks, deterministic approval routing, draft generation, human
  confirmation, and idempotent mock submission with LangGraph and restart-safe SQLite state.
- Added exact-request Redis caching, async single-flight, bounded provider backpressure, safe retries,
  request correlation, Prometheus metrics, and prompt-injection/evidence-quarantine guardrails.
- Established 30 golden cases, offline performance/concurrency/batching evidence, and a six-scenario
  portfolio demo enforced by pytest, Ruff, wheel, Docker, and GitHub Actions quality gates.

## 3. 面试官最可能追问什么

### 为什么不让 LLM 直接判断审批路线？

审批是确定性、高风险规则。相同金额和条件必须得到相同结果，还要能审计、回归和解释。LLM
负责把自然语言转换成结构化意图；审批层级、顺序和特殊条件由版本化 Python 规则生成并绑定
制度条款。

### 为什么 RAG 证据还要做提示注入检查？

用户输入不是唯一攻击面。被上传或被污染的制度可能包含“忽略系统指令”等文本。如果检索后
直接拼接，这些内容仍会进入模型。系统因此在用户入口阻断攻击，并对每个检索 Chunk 二次检查，
隔离后重新生成 `S1/S2` 映射。

### 权限过滤发生在哪里？

服务端先根据可信身份和制度元数据计算允许的 Chunk ID，再把同一集合交给 Vector 与 BM25；
RRF 只融合两路授权排名。未授权 Chunk 可以存在于进程的启动索引中，但不会进入用户特定的评分结果
或 Prompt。这里不能偷换概念成“未授权内容从未加载进进程”。

### 缓存、single-flight 和背压的顺序是什么？

先查精确缓存；同一个 miss 用 single-flight 合并；真正需要上游调用的 leader 再进入 Provider
有界并发和 FIFO 队列。这样既减少重复请求，又避免大量不同键同时压垮 Provider。

### 如何证明项目不是只写了 README？

仓库提供可执行证据：30 条黄金用例、pytest 全量测试、权限与注入专项验证、离线性能/并发/
批处理报告、CI 配置契约和 Day 30 六场景演示。每项简历数字都能通过固定命令重新生成。

## 4. 证据数字如何解释

| 数字 | 能说明什么 | 不能说明什么 |
|---|---|---|
| 黄金用例 30/30 | 固定业务夹具满足五项门禁 | 开放世界用户问题全部正确 |
| 权限拒绝 7/7 | 七类元数据边界在固定夹具生效 | 已接企业 SSO 或集中策略服务 |
| 攻击 6/6、正常 4/4 | 固定高信号规则可回归 | 真实攻击检出率为 100% |
| 并发三种分布 | 测量方法、合并率和放大率正确 | 真实 Provider SLA |
| Day 30 演示 6/6 | 关键编排可以完全离线复现 | 真实 BGE、LLM 和公网效果 |

## 5. 主动说明的技术债

当前不能宣称已经达到生产级，原因包括：

- 运行时使用固定可信演示身份，未接 JWT/OIDC、员工目录和集中策略服务；
- 制度索引仍是单进程内存 Vector/BM25，未接 PostgreSQL/pgvector 或持久化倒排索引；
- 文档加载已支持 Markdown、PDF、DOCX 和显式 OCR fallback，但缺复杂 layout/table parser 和真实企业扫描集；
- Reranker 已有批量契约，但尚未接入正式检索主链路；
- 指标和 single-flight 是单进程状态，未做跨实例聚合与协调；
- 尚无 OpenTelemetry、集中日志、Grafana 告警和真实流量 SLO；
- Day 30 演示不调用真实 BGE、LLM 或 Web Provider，只证明编排与工程契约。

主动解释这些边界，比模糊地说“企业级生产系统”更可信，也能自然引出下一阶段设计。

## 6. 代码讲解顺序

1. `app/main.py`：依赖组合和运行时边界；
2. `app/agent/workflow.py`：LangGraph 节点、确认与副作用；
3. `app/rag/policy_retriever.py`、`fusion.py`：授权 ID、Vector/BM25 与 RRF；
4. `app/rag/policy_answer_service.py`：JSON 上下文和引用校验；
5. `app/security/`：身份授权和提示注入；
6. `app/cache/`、`app/llm/concurrency.py`：缓存、防击穿和背压；
7. `app/portfolio/`：如何把能力组合成可重复发布证据。
