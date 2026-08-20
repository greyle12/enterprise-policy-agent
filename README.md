# 企业制度问答与流程办理 Agent

[![Continuous Integration](https://github.com/greyle12/enterprise-policy-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/greyle12/enterprise-policy-agent/actions/workflows/ci.yml)

一个面向企业内部制度查询与流程办理场景的 AI Agent 个人项目。

项目不止实现普通的文档问答，还计划完成从制度检索、条款引用、意图识别、材料检查、申请草稿生成、用户确认到模拟审批提交的完整流程。

> 当前项目为个人学习和求职作品集项目。仓库中的制度、员工、供应商、金额、审批流程和申请记录均为模拟数据，不代表任何真实企业。

---

## 1. 项目背景

企业员工经常需要查询和办理以下事项：

- 差旅住宿标准是多少？
- 出差报销需要哪些材料？
- 采购人民币 6,000 元的设备需要什么审批？
- 请四天病假需要提交什么证明？
- 哪些公司数据不能上传到公共大模型？
- 帮我生成采购申请草稿。
- 确认后模拟提交审批。

普通的企业知识库问答通常只能完成：

```text
用户提问
→ 检索文档
→ 大模型生成回答
```

本项目计划实现：

```text
用户提问
→ 识别用户意图
→ 获取可信用户身份
→ 执行权限过滤
→ 检索相关制度
→ 引用具体条款
→ 执行业务规则判断
→ 检查缺失字段和材料
→ 生成结构化申请草稿
→ 展示草稿并等待确认
→ 用户明确确认
→ 幂等模拟提交
→ 创建审批工作流
```

项目重点不是让大模型自由决定所有业务操作，而是让大模型在明确权限、业务规则和工具边界内完成任务。

---

## 2. 项目目标

本项目计划验证以下 AI 应用开发能力：

1. 企业制度文档解析与元数据提取；
2. 文档分块、向量检索和关键词检索；
3. 混合检索、重排与可追溯引用；
4. 基于部门、角色和数据等级的权限过滤；
5. LLM 意图识别和结构化字段提取；
6. Agent 工具调用和多轮对话状态管理；
7. 申请字段和附件完整性检查；
8. 金额、余额、日期等确定性规则计算；
9. 人在回路确认机制；
10. 幂等提交和模拟审批工作流；
11. RAG 与 Agent 自动化评测；
12. FastAPI、测试、日志、数据库和容器化部署。
13. 内部制度 RAG 与显式授权 Web Search 的受控研究整合。
14. 可重复的性能基准、预算门禁和 Python 热点分析。
15. 可失效、可观测且故障安全的 Redis LLM 响应缓存。
16. 单进程统一 LLM Provider 并发门禁、有限排队与安全过载降级。
17. 请求关联、结构化访问日志、低基数 HTTP 指标和 Prometheus 兼容导出。
18. 可信身份驱动的检索前授权、提示注入拒绝和污染证据隔离。

---

## 3. 核心业务场景

### 3.1 企业制度问答

示例问题：

```text
普通员工去上海出差，每晚住宿费用上限是多少？
```

预期处理流程：

```text
识别问题属于差旅制度
→ 检索城市分类条款
→ 确认上海属于一类城市
→ 检索普通员工住宿标准
→ 返回人民币 500 元/晚
→ 展示制度版本和具体条款引用
```

系统不得仅依靠大模型记忆回答制度问题。

---

### 3.2 采购流程办理

示例问题：

```text
帮我申请购买三台显示器。
```

Agent 需要完成：

1. 提取采购物品和数量；
2. 检查规格、用途、价格、预算等缺失字段；
3. 重新计算采购总金额；
4. 判断采购金额等级；
5. 判断所需报价数量；
6. 判断是否需要信息技术部评审；
7. 生成采购申请草稿；
8. 向用户展示草稿；
9. 等待用户明确确认；
10. 模拟提交审批。

Agent 不得自行虚构预算编号、供应商、价格或交付日期。

---

### 3.3 差旅报销办理

示例问题：

```text
我上周去上海出差，帮我报销。
```

Agent 需要：

- 识别“上周”为相对日期；
- 将相对日期转换为明确日期；
- 向用户确认转换结果；
- 关联原出差申请；
- 检查交通、住宿和支付材料；
- 重新计算报销金额；
- 判断住宿是否超过标准；
- 检查报销时限；
- 检查是否存在重复报销。

---

### 3.4 请假申请办理

示例问题：

```text
我下周一想请一天年假。
```

Agent 需要：

- 解析相对日期；
- 向用户确认绝对日期；
- 检查年假余额；
- 检查已有请假记录是否重叠；
- 计算实际工作日；
- 判断审批路线；
- 检查工作交接信息；
- 保护医疗信息和联系方式。

Agent 不得在年假余额不足时，自动把剩余时间转换为事假。

---

### 3.5 信息安全问答

示例问题：

```text
我可以把客户姓名、手机号和交易明细上传到公共 AI 网站吗？
```

Agent 应当：

- 识别客户信息和交易明细属于敏感数据；
- 明确说明不能上传公共大模型；
- 引用信息安全制度；
- 建议使用公司批准的内部工具；
- 或使用经过批准并充分脱敏的测试数据；
- 防止提示注入绕过权限控制。

---

## 4. 当前项目状态

当前处于：

```text
Advanced RAG Phase 23：PDF 文档解析（已完成）
基础作品集路线 Phase 21：项目收尾与作品集发布（Day 30 已完成）
```

### 已完成

- [x] Python 项目目录结构；
- [x] 5 份模拟企业制度；
- [x] 3 类业务申请的完整与不完整 JSON 样例；
- [x] 共 6 份申请样例；
- [x] 4 个 Agent 工具输入输出契约；
- [x] 30 条黄金评测用例；
- [x] 用户确认机制设计；
- [x] 权限和隐私规则设计；
- [x] 草稿与正式提交分离；
- [x] 幂等提交规则设计；
- [x] Git 忽略规则；
- [x] 环境变量模板。
- [x] Markdown 制度解析、条款切分与结构化引用；
- [x] BGE Embedding 和内存向量检索；
- [x] OpenAI-compatible LLM 客户端与引用约束；
- [x] 意图识别、材料检查和确定性审批路线；
- [x] LangGraph 多轮草稿、人工确认和模拟提交；
- [x] 提交幂等、审批路线冻结和审计记录；
- [x] SQLite checkpoint 与业务数据持久化；
- [x] 服务重启后恢复会话、草稿和审批记录；
- [x] 30 条可执行黄金用例与五项质量门禁；
- [x] JSON 和 Markdown 结构化评测报告。
- [x] 多阶段、非 root Docker 镜像；
- [x] Docker Compose 一键启动；
- [x] liveness 与 readiness 健康检查；
- [x] SQLite 与 BGE 模型缓存具名卷；
- [x] 容器重建后的 SQLite 持久化自动验收。
- [x] GitHub Actions 自动持续集成；
- [x] Ruff、pytest、黄金评测和 Wheel 自动质量门禁；
- [x] 合并后 Docker 镜像自动构建验证；
- [x] Pull Request 新增依赖风险审查；
- [x] Python 与 GitHub Actions 每周依赖更新；
- [x] CI 证据 Artifact 和安全配置契约。
- [x] 会话隔离的用户/助手对话记忆；
- [x] SQLite 对话历史与 v1 → v2 自动迁移；
- [x] 受限上下文窗口和省略追问消解；
- [x] 常见凭据脱敏、消息截断和 50 轮保留上限；
- [x] 对话历史查询和完整会话清除 API。
- [x] 只读和纯计算工具的 Tenacity 有界重试；
- [x] 每次工具调用的独立超时和稳定错误分类；
- [x] 不可信工具结果立即失败，不进入下游流程；
- [x] 审批提交固定单次执行，失败后保留已确认草稿；
- [x] 脱敏错误编号、恢复动作和容错元数据 API。
- [x] 内部制度优先的 Policy Research Assistant；
- [x] 显式 `include_web` 授权与服务端 Provider 双重开关；
- [x] 内部制度 `S` 引用与外部网页 `W` 引用分区；
- [x] Tavily HTTP Search Provider、查询脱敏和结果长度限制；
- [x] 外部资料只读、仅供参考且不进入办理工作流。
- [x] 第 3 周工程能力与验收证据总结。
- [x] 五个代表场景的完全离线性能基准；
- [x] warm-up、p50、p95、错误率和固定预算门禁；
- [x] cProfile 项目热点提取和相对路径报告；
- [x] py-spy 与 Scalene 可选 profiling 环境；
- [x] CI 自动性能回归检查和报告证据。
- [x] 统一 LLM 边界上的可选 Redis 精确请求缓存；
- [x] 模型、消息、协议版本共同参与的 SHA-256 缓存键；
- [x] TTL、请求/响应大小上限和敏感凭据形态绕过；
- [x] Redis 故障直连 LLM、进程内命中指标和安全状态 API；
- [x] Compose 临时 Redis 服务和完全离线缓存专项验收。
- [x] 相同 cache miss 的进程内异步 single-flight 请求合并；
- [x] follower 取消隔离、异常清理和应用关闭时 Task 回收；
- [x] 有容量上限的在途键注册表和 overflow 指标；
- [x] 12 请求只触发 1 次上游调用的完全离线并发验收。
- [x] 热点键、四键 hotset 与唯一键扇出的受控并发负载；
- [x] 客户端端到端 p50 / p95、吞吐和错误率报告；
- [x] 上游调用率、唯一键放大率和 Provider 峰值并发证据；
- [x] JSON / Markdown 并发报告与 CI 自动质量门禁。
- [x] BGE Reranker 批量 Provider、候选模型与稳定排序契约；
- [x] Embedding/Reranker 逐条与批量输出等价性验收；
- [x] Provider 调用减少、内部批次、吞吐和加速比报告；
- [x] 完全离线批处理专项脚本与 CI 证据。
- [x] 缓存与 single-flight 之后的统一 LLM Provider 并发门禁；
- [x] FIFO 有界队列、队满立即拒绝和排队超时；
- [x] 排队/执行取消清理、关闭排空和上游资源单次关闭；
- [x] 安全 503 错误、Provider 状态 API 与完全离线背压验收。
- [x] 合法性校验、服务端生成和响应回传的 `X-Request-ID`；
- [x] 不记录原始 URL/query/body 的 JSON 结构化访问日志；
- [x] 路由模板、固定直方图和 64 键上限的进程内 HTTP 指标；
- [x] 安全关联 500、JSON 状态、Prometheus Provider/HTTP 指标和离线验收。
- [x] 可信身份、制度生命周期、等级、部门、角色和区域授权；
- [x] 未授权 Chunk 在向量评分前排除且不进入 Prompt；
- [x] 中英文提示注入、权限提升、工具绕过和编码指令检测；
- [x] 污染制度证据隔离、JSON 数据边界和安全关联 400；
- [x] 无内容安全状态、Prometheus 指标与完全离线 CI 评测。
- [x] 六场景完全离线作品集演示与 JSON / Markdown 发布证据；
- [x] 真实解析、检索、LangGraph、业务规则和安全边界的集成演示；
- [x] Day 30 发布契约、CI 自动运行和证据 Artifact；
- [x] 已实现架构图、可重复演示手册、简历描述和面试讲解材料。
- [x] 统一 Document Loader Protocol、不可变格式注册表与 Markdown Loader；
- [x] Parser、Chunker、Retriever 复用同一加载入口且保持原有 5 文档/199 Chunk 契约；
- [x] Phase 22 完全离线 Loader 专项验证与 CI 门禁。
- [x] PyMuPDF 原生 PDF 文本提取与确定性阅读顺序；
- [x] PDF 可信 sidecar 元数据、页码 provenance 与 Citation 传递；
- [x] 加密/损坏/缺少 sidecar 拒绝及扫描件 `OCRRequiredError`；
- [x] Phase 23 完全离线真实 PDF 专项验证与 CI 门禁。

### 尚未实现

- [ ] DOCX 文档解析；
- [ ] OCR fallback；
- [ ] PostgreSQL / pgvector；
- [ ] BM25 关键词检索；
- [ ] Hybrid Search；
- [ ] Reranker 接入正式检索链路与黄金相关性评测；
- [ ] Redis 会话状态；
- [ ] 集中日志存储、跨实例指标聚合和 OpenTelemetry 链路追踪。
- [ ] 真实 BGE、LLM 和 Web Provider 性能基线；
- [ ] 生产级持续压测、跨进程全局背压、分布式防击穿和真实模型 batch 调优。

当前仓库不能被描述为“已经完成的企业级 Agent”。

更准确的状态是：

```text
已完成制度 RAG、确定性业务工具、LangGraph 多轮流程、
幂等模拟提交、SQLite 重启恢复、自动化黄金集评测和 Docker Compose 部署；
当前还具备自动 CI 质量门禁、可重启恢复的受限对话记忆、
有界工具重试和副作用安全降级，以及显式授权的制度研究助手，
并具备离线性能预算和 cProfile 热点分析，
以及可选、短 TTL、故障时直连模型的 Redis LLM 响应缓存和单进程异步防击穿，
并能用三种请求分布测量并发 p95、吞吐、上游放大率和 Provider 峰值，
同时具备 Embedding/Reranker 批量接口、结果等价性和调用减少证据，
并在缓存与 single-flight 之后提供默认关闭的单进程 Provider 有界并发与安全过载语义，
同时具备请求 ID、脱敏 JSON 访问日志、低基数 HTTP 指标和 Prometheus 抓取端点，
并在 RAG 和 Agent 执行前提供可信身份授权、提示注入拒绝与污染证据隔离，
并能通过六个完全离线场景一键展示引用、业务规则、人工确认、幂等提交、研究边界和安全拒绝，
定位仍是可容器化运行的单机个人作品集版本，
不宣称为多实例生产系统。
```

---

## 5. 模拟企业制度

制度目录：

```text
data/policies/
```

当前包含 5 份制度：

| 文件 | 制度名称 |
|---|---|
| `travel_reimbursement_policy_v1.md` | 差旅报销管理制度 |
| `procurement_management_policy_v1.md` | 采购管理办法 |
| `leave_management_policy_v1.md` | 员工请假管理制度 |
| `information_security_policy_v1.md` | 信息安全管理制度 |
| `expense_reimbursement_guide_v1.md` | 费用报销管理指南 |

每份制度包含 YAML 元数据，例如：

```yaml
document_id: PROCUREMENT_POLICY_001
document_type: policy
title: 采购管理办法
version: "1.0"
status: effective
issuing_department: 采购管理部
effective_date: 2026-01-01
allowed_departments:
  - ALL
allowed_roles:
  - EMPLOYEE
  - MANAGER
  - PROCUREMENT
  - FINANCE
security_level: internal
region: 中国大陆
```

这些元数据后续用于：

- 制度编号识别；
- 制度版本管理；
- 生效日期过滤；
- 历史制度查询；
- 部门权限过滤；
- 角色权限过滤；
- 数据安全级别过滤；
- 检索结果引用。

---

## 6. 业务申请样例

申请样例目录：

```text
data/samples/applications/
```

当前共有 6 份申请样例：

```text
purchase_application_complete.json
purchase_application_incomplete.json
travel_reimbursement_complete.json
travel_reimbursement_incomplete.json
leave_application_complete.json
leave_application_incomplete.json
```

### 完整样例

用于定义一份可进入用户确认阶段的标准申请应该包含哪些字段。

例如采购申请包含：

- 申请人；
- 采购事项；
- 数量；
- 单价；
- 总金额；
- 预算编号；
- 成本中心；
- 推荐供应商；
- 附件；
- 制度检查结果；
- 审批路线；
- 确认状态；
- 提交状态；
- 幂等键。

### 不完整样例

用于测试 Agent 的多轮信息收集能力。

不完整样例包含：

- 用户原始消息；
- 已提取字段；
- 标准化字段；
- 缺失字段；
- 缺失材料；
- 需要确认的字段；
- 下一步问题；
- 当前处理状态。

---

## 7. Agent 工具设计

工具契约目录：

```text
docs/tool_contracts/
```

当前定义了 4 个核心办理工具；Day 21 另增加内部制度研究和外部 Web Search
两个只读研究工具，它们不能执行草稿、审批或提交操作。

---

### 7.1 `search_policy`

用途：

```text
根据用户问题和可信用户身份，
检索用户有权限访问的企业制度条款。
```

主要输入：

- 用户问题；
- 员工编号；
- 部门；
- 角色；
- 区域；
- 安全等级；
- 指定生效日期；
- 检索数量；
- 文档过滤条件。

主要输出：

- 制度编号；
- 制度名称；
- 制度版本；
- 条款编号；
- 条款原文；
- 相似度分数；
- 引用文本；
- 检索元数据。

安全要求：

- 必须在检索前执行权限过滤；
- 无权限内容不得进入大模型上下文；
- 用户身份不能由大模型自行生成；
- 历史问题必须根据指定日期选择制度版本。

---

### 7.2 `check_required_materials`

用途：

```text
检查申请字段、附件和业务规则是否完整。
```

它会返回：

- 缺失字段；
- 缺失材料；
- 验证问题；
- 阻断问题；
- 所需审批人；
- 下一步动作；
- 是否可以创建草稿；
- 是否可以进入确认；
- 是否可以正式提交。

该工具区分三个检查阶段：

```text
draft
confirmation
submission
```

草稿阶段可以允许部分材料缺失。

正式提交阶段则必须满足所有阻断条件。

---

### 7.3 `create_application_draft`

用途：

```text
把已经收集并完成必要校验的数据，
转换为标准化申请草稿。
```

它可以：

- 创建草稿编号；
- 重新计算金额；
- 重新计算余额；
- 标准化日期；
- 记录制度版本；
- 生成审批路线；
- 保存缺失项；
- 生成用户可读摘要；
- 请求用户确认。

它不能：

- 自动替用户确认；
- 自动提交审批；
- 虚构缺失数据；
- 忽略阻断问题。

---

### 7.4 `submit_mock_approval`

用途：

```text
在用户明确确认后，
把申请草稿模拟提交到审批工作流。
```

提交前必须验证：

- 草稿状态正确；
- 用户明确确认；
- 确认人与申请人一致；
- 或确认人具有合法代办权限；
- 缺失字段为空；
- 缺失材料为空；
- 不存在阻断问题；
- 制度版本仍然适用；
- 提交幂等键存在；
- 幂等键没有冲突；
- 审批路线有效。

成功后返回：

- 正式申请编号；
- 工作流编号；
- 当前审批步骤；
- 完整审批路线；
- 更新后的草稿状态；
- 审计信息。

---

## 8. 工具风险等级

| 工具 | 副作用 | 调用前是否需要用户确认 |
|---|---|---|
| `search_policy` | 无 | 否 |
| `check_required_materials` | 无 | 否 |
| `create_application_draft` | 写入草稿 | 否 |
| `submit_mock_approval` | 提交审批工作流 | 是 |

正确流程：

```text
检索制度
→ 检查材料
→ 创建草稿
→ 展示草稿
→ 用户明确确认
→ 模拟提交
```

不能把以下模糊表达直接识别为提交授权：

```text
看起来差不多
应该没问题
先这样吧
好的
嗯
可以吧
```

提交时应要求明确表达，例如：

```text
确认提交
我确认提交这份申请
内容正确，可以提交
```

---

## 9. 安全设计

### 9.1 可信用户身份

用户身份必须来自：

```text
登录认证系统
员工目录
组织架构系统
```

不能因为用户在聊天中说：

```text
我是财务管理员
```

就自动授予财务权限。

---

### 9.2 检索前权限过滤

错误方式：

```text
检索所有文档
→ 把敏感内容放入 Prompt
→ 要求模型不要泄露
```

正确方式：

```text
读取可信身份
→ 按部门、角色、区域和数据等级过滤
→ 只检索用户有权访问的 Chunk
→ 再将结果交给模型
```

即使最终回答没有显示敏感内容，也不应让无权限内容进入模型上下文。

Day 29 已在运行时实现该顺序：固定可信演示身份先检查制度状态、有效期、安全等级、部门、
角色和地域，只有授权 Chunk ID 才参与向量评分。生产环境仍须用登录认证结果替换演示身份。

---

### 9.3 提示注入防护

用户消息会在意图分类、RAG、Web Search 和工具执行前检查；疑似污染的制度证据也会在 Prompt
构造前隔离。命中请求只返回固定错误码和请求 ID，不回显输入、命中规则或内部制度内容。

确定性规则只是纵深防御的一层，仍应结合可信身份、最小权限、入库治理、结构化工具、输出
校验、监控和持续红队。

---

### 9.4 敏感信息保护

普通日志和回答中不得无必要地记录：

- 密码；
- API 密钥；
- 访问令牌；
- 私钥；
- 完整身份证号码；
- 完整电话号码；
- 完整医疗诊断；
- 未脱敏客户数据；
- 生产数据库连接信息；
- 核心安全配置。

病假材料等敏感附件应限制访问范围。

对话记忆写入前会脱敏常见 API Key、Token 和密码形态，并限制单条消息、
上下文窗口和每个会话的保留轮次。该机制不能替代生产环境的身份认证、授权和数据分类。

---

### 9.5 人在回路

采购、请假和报销提交属于有业务影响的操作。

大模型不得：

- 自行确认申请；
- 自行提交申请；
- 自动替用户选择假期类型；
- 自动修改报销金额；
- 自动选择供应商；
- 自动接受超标准费用；
- 自动绕过缺失材料。

---

### 9.6 幂等控制

提交操作必须提供幂等键。

例如：

```text
第一次提交：
submit:purchase:PURCHASE-DRAFT-001:EMP-10001
→ 创建 PURCHASE-202607-0001
```

相同请求再次提交：

```text
返回 PURCHASE-202607-0001
不创建第二份申请
```

如果同一个幂等键对应不同草稿，则返回：

```text
IDEMPOTENCY_KEY_CONFLICT
```

---

### 9.7 审计记录

关键操作应记录：

- `session_id`；
- `request_id`；
- 操作人；
- 操作时间；
- 工具名称；
- 草稿编号；
- 正式申请编号；
- 制度版本；
- 用户确认原文；
- 幂等键；
- 操作结果。

日志中不应记录完整敏感信息。

---

## 10. 黄金测试集

测试文件：

```text
tests/evaluation/golden_test_cases.jsonl
```

当前包含 30 条测试用例：

| 测试类别 | 数量 |
|---|---:|
| 意图识别与工具路由 | 10 |
| 材料完整性检查 | 10 |
| 审批路线判断 | 10 |
| 合计 | 30 |

每条用例都使用严格字段契约，自动统计：

- 意图识别准确率；
- 工具选择准确率；
- 材料检查准确率；
- 审批路线准确率；
- 制度引用准确率。

默认离线评测不会调用真实 LLM、不会连接正式运行数据库，也不会提交审批：

```powershell
python -X utf8 -m scripts.run_golden_evaluation --mode offline
```

报告输出到：

```text
artifacts/evaluation/golden-evaluation-report.json
artifacts/evaluation/golden-evaluation-report.md
```

如需测量真实意图分类模型，配置 `.env` 后运行：

```powershell
python -X utf8 -m scripts.run_golden_evaluation --mode live
```

报告会明确记录 `offline` 或 `live`，避免把关键词基线结果误写成真实 LLM 准确率。

---

## 11. Docker Compose 部署

Day 17 提供多阶段 Docker 镜像和 Compose 编排。先复制并填写运行配置：

```powershell
Copy-Item .env.example .env
notepad .env
```

一键构建并启动：

```powershell
docker compose config --quiet
docker compose up --build --detach --wait
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

完整自动验收会真实重建一次容器，并检查 SQLite 数据是否仍然存在：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_docker_deployment
```

Compose 使用：

- `agent_runtime` 保存 SQLite 数据；
- `model_cache` 保存 BGE 模型缓存；
- 临时 Redis 服务保存短 TTL 的 LLM 响应缓存；
- 非 root 用户运行应用；
- 只读根文件系统；
- `no-new-privileges` 和全部 Linux capability 移除；
- readiness 作为容器健康判断依据。

`docker compose down` 会保留两个具名卷，但 Redis 响应缓存本来就是可丢失的临时数据；
`docker compose down --volumes` 会删除本地运行数据和模型缓存。

详细步骤、首次模型下载说明和排障方式见：

```text
docs/docker_deployment.md
```

---

## 12. GitHub Actions 持续集成

Day 18 将本地质量命令接入无密钥、只读权限的 GitHub Actions：

```text
Push / Pull Request / 手动运行
→ 固定 Python 3.12.10
→ 安装依赖并执行 pip check
→ 验证 CI 安全契约
→ Ruff check
→ 全量 pytest
→ 30 条离线黄金评测
→ 五场景离线性能预算
→ Redis LLM 缓存离线契约
→ 异步 LLM single-flight 契约
→ LLM Provider 并发与背压契约
→ 请求关联与运行时可观测性契约
→ 三种并发 load shape 对照
→ Embedding/Reranker 批处理对照
→ 构建 Python Wheel
```

向 `main` 或 `master` 推送时，在 Python 质量门禁通过后继续验证：

```text
Docker Compose 配置
→ 完整 Docker 镜像构建
→ 非 root 用户和 Python 包检查
```

Pull Request 还会检查新引入的高危或严重依赖漏洞。构建结果保留 pytest JUnit、黄金评测、
串行性能、并发负载与批处理报告，以及可安装 Wheel，方便查看失败原因和保存可复现证据。

本地检查 CI 配置：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_ci_configuration
```

详细触发规则、安全边界、GitHub 首次验收和排障见：

```text
docs/continuous_integration.md
```

---

## 13. Agent 对话记忆

Day 19 将流程 checkpoint 与自然语言对话记忆明确分离：

```text
用户消息
→ 读取当前 session 最近 4 条已脱敏消息
→ 仅在省略追问时构造上下文
→ 执行意图识别和业务工具
→ 返回本轮原始 request
→ 将用户消息和助手回复写入 SQLite
```

查询最近历史：

```http
GET /api/v1/agent/sessions/{session_id}/messages?limit=20
```

清除 checkpoint、可变草稿投影和对话记忆：

```http
DELETE /api/v1/agent/sessions/{session_id}
```

完整设计、保留上限、安全边界和 PowerShell 示例见：

```text
docs/conversation_memory.md
```

---

## 14. Agent 工具容错

Day 20 在 LangGraph 与各业务工具之间增加统一执行边界：

```text
只读 / 纯计算工具
→ 单次超时
→ 仅对超时、限流和上游不可用进行最多 3 次尝试
→ 成功则标记 recovered
→ 仍失败则返回 unavailable 和安全错误

审批提交
→ 单次超时
→ 固定只执行 1 次
→ 结果不确定时保留已确认草稿
→ 用户使用相同 session_id 再次提交，由幂等键防重
```

响应中的 `resilience` 会说明：

- 是否发生安全降级；
- 是否通过重试恢复；
- 每个工具的尝试次数；
- 操作是否允许自动重试；
- 稳定错误编号和恢复动作。

原始异常文本、请求正文和凭据不会进入容错元数据。完整策略、错误分类、配置和 PowerShell 示例见：

```text
docs/agent_error_handling.md
```

---

## 15. 受控制度研究助手

Day 21 新增独立于事务办理状态机的研究入口：

```http
POST /api/v1/research/answers
```

默认请求只使用内部制度 RAG：

```json
{
  "question": "差旅住宿费如何报销？"
}
```

只有同时满足以下两个条件才会调用外部 Web Search：

1. 客户端明确传入 `"include_web": true`；
2. 服务端配置 `WEB_SEARCH_PROVIDER=tavily` 和有效密钥。

研究回答始终区分：

- `S1`、`S2`：内部制度依据，可作为本项目中的权威业务依据；
- `W1`、`W2`：外部公开资料，只供研究参考；
- `source_policy.external_web_used_for_workflow=false`：外部资料不会驱动材料检查、
  审批判断、草稿生成或提交。

发送给外部 Provider 的内容只包含当前问题的脱敏、最多 500 字版本，不包含对话历史、
内部制度原文、用户身份或申请草稿。网页摘要不会再次送入 LLM，避免外部提示注入改变
内部制度结论。

完整配置、API 字段、状态语义和 PowerShell 验收见：

```text
docs/policy_research_assistant.md
docs/week3_milestone.md
```

---

## 16. 性能瓶颈分析

Day 22 新增完全离线、可重复的性能基准：

```powershell
python -X utf8 -m scripts.run_performance_benchmark --warmups 1 --iterations 5
```

它覆盖：

```text
运行时启动
制度 RAG 回答
Agent 材料路由
Agent 审批路由
内部 RAG + 固定 Web 结果的混合研究
```

每个场景先 warm-up，再串行收集样本，输出 p50、p95、最大值、错误率和预算占用率。
任何错误或 p95 超预算都会使质量门禁失败。基准使用确定性 Hash Embedding、固定 LLM
回答和固定 Web 结果，因此不会下载模型、调用真实 LLM 或产生外部网络请求。

内置 cProfile 命令：

```powershell
python -X utf8 -m scripts.profile_agent_performance --warmups 1 --iterations 5 --top 20
```

结构化热点报告只保存 `app/` 下的项目相对路径。`py-spy` 与 Scalene 位于可选 `profiling` extra，
不会增加正式运行镜像的依赖。完整测量方法、预算、Windows 命令和解释边界见：

```text
docs/performance_analysis.md
```

---

## 17. Redis LLM 响应缓存

Day 23 在统一 `LLMClient` 外增加 cache-aside 装饰器。完全相同且合规的消息序列先读取
Redis；未命中才调用原 LLM，并只缓存成功、非空、大小合规的文本 600 秒。

缓存键由协议版本、模型身份和完整消息规范化后计算 SHA-256，Redis 键不包含原始提问。
含凭据形态的消息和超大请求直接绕过；Redis 读写错误只增加缓存错误计数，并安全回退到
原 LLM，不改变 SQLite、申请草稿、审批提交或 Web Search。

完全离线专项验收：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_llm_cache
```

运行时状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/cache/status |
  ConvertTo-Json -Depth 5
```

配置、键失效、隐私边界、Compose Redis 和完整 Windows 验收见：

```text
docs/redis_llm_cache.md
```

---

## 18. 异步 LLM single-flight

Day 24 在 Day 23 精确缓存之上增加进程内异步请求合并。同一缓存键同时发生多个 miss 时，
第一个请求创建 leader Task，其余 follower 使用 `asyncio.shield()` 等待共享结果，因此不会
重复调用 LLM，也不会因某个客户端取消而取消其他等待者的共享任务。

不同缓存键仍可并发执行；敏感、超大、非法或明确绕过缓存的请求不进入 single-flight。
注册表默认最多跟踪 128 个不同的在途键，容量溢出时保持可用并记录指标。

完全离线专项验收：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_async_singleflight
```

完整算法、取消语义和状态字段见：

```text
docs/async_llm_singleflight.md
```

---

## 19. 异步并发负载与吞吐证据

Day 25 将 Day 24 的并发正确性契约扩展为三种受控请求分布：同一热点键、四键 hotset 和
全部唯一键。报告同时记录端到端 p50 / p95、吞吐、错误率、上游调用率、唯一键放大率及
Provider 峰值并发，并把排队时间纳入客户端延迟。

完全离线专项验收：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_concurrency_load
```

生成 JSON / Markdown 报告：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.run_concurrency_load_test `
  --requests 24 `
  --concurrency 12 `
  --provider-latency-ms 15
```

完整场景、指标公式、验收和真实 Provider 边界见：

```text
docs/async_concurrency_load.md
```

---

## 20. Embedding/Reranker 批处理优化

Day 26 对比 32 条输入逐条调用与一次列表调用。Embedding 和 Reranker 都从 32 次应用层
Provider 调用降为 1 次，配置 `batch_size=8` 时由模型内部执行 4 个逻辑批次；质量门禁同时
验证输出摘要和顺序与逐条基线完全一致。

完全离线专项验收：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_embedding_reranker_batching
```

生成 JSON / Markdown 报告：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.run_batch_optimization `
  --items 32 `
  --batch-size 8 `
  --call-overhead-ms 1.5 `
  --batch-latency-ms 0.25
```

当前 Reranker 已具备批量 Provider 和稳定排序契约，但尚未接入正式检索链路。完整指标、
真实模型边界和 batch size 选择方法见：

```text
docs/embedding_reranker_batching.md
```

---

## 21. LLM Provider 并发门禁与背压

Day 27 在统一真实 LLM 边界增加进程内 Provider 容量保护。门禁位于 Redis cache-aside 和
single-flight 之后，因此 cache hit 与 follower 不消耗执行 permit；不同请求和缓存绕过请求
仍受相同的并发上限保护。

默认保持关闭；启用后使用 FIFO 有界队列，队满立即返回安全 503，排队超过配置时间则清理
waiter 并返回稳定超时错误。取消、上游异常和应用关闭都会归还容量或排空资源。

完全离线专项验收：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_provider_backpressure
```

运行时状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/provider/status |
  ConvertTo-Json -Depth 5
```

配置、FIFO/取消语义、503 契约、指标解释和真实参数选择方法见：

```text
docs/provider_backpressure.md
```

---

## 22. 请求关联与运行时可观测性

Day 28 为每个 HTTP 请求校验或生成 `X-Request-ID`，写入处理上下文并回传响应。访问日志只
记录请求 ID、方法、路由模板、状态、耗时和结果，不记录原始路径参数、query string、请求体
或异常正文；未知异常统一返回带关联 ID 的安全 500。

进程内指标按 FastAPI 路由模板聚合并限制为 64 个正常路由键，提供固定 latency histogram，
避免用户输入造成标签基数失控。`/metrics` 使用 Prometheus text format 0.0.4，同时导出 Day 27
Provider 执行、排队和事件指标。

完全离线专项验收：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_runtime_observability
```

运行时端点：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/observability/status |
  ConvertTo-Json -Depth 8
Invoke-WebRequest http://127.0.0.1:8000/metrics |
  Select-Object -ExpandProperty Content
```

安全字段、直方图、PromQL 示例、Docker 验收和单进程边界见：

```text
docs/runtime_observability.md
```

---

## 23. RAG 权限过滤与提示注入防护

Day 29 把安全设计变成强制执行边界：服务器固定注入可信演示身份，制度 Chunk 先检查状态、
有效期、安全等级、部门、角色和地域，再在授权 ID 范围内做向量评分。用户在聊天中自称管理员
不能改变该上下文，也不能让无权限内容进入 LLM。

用户输入在意图分类、内部 RAG、Web Search、Agent 工作流和工具执行前检查；疑似污染的制度
标题、章节、条款或正文会被隔离。剩余证据以 JSON 数据边界发送，命中输入返回带请求 ID 的
固定 `prompt_injection_blocked`，不回显原文或命中规则。

完全离线专项验收：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_rag_security
```

安全计数：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/security/status |
  ConvertTo-Json -Depth 6
Invoke-WebRequest http://127.0.0.1:8000/metrics |
  Select-Object -ExpandProperty Content
```

威胁模型、授权规则、离线指标、误报/漏报边界和生产改进见：

```text
docs/rag_security_guardrails.md
```

---

## 24. Day 30 一键作品集演示与发布验收

Day 30 将前 29 天分散的能力组合成六个面试友好的完全离线场景：制度引用、材料规则、审批
路线、草稿确认与幂等提交、内外研究来源分区、提示注入执行前拒绝。演示复用真实解析、检索、
LangGraph、规则与安全代码，只把 BGE、LLM 和 Web Search 替换为确定性离线夹具。

运行演示与最终发布契约：

```powershell
python -X utf8 -m scripts.run_portfolio_demo --output-dir artifacts/portfolio
python -X utf8 -m scripts.verify_portfolio_release
```

输出：

```text
artifacts/portfolio/portfolio-demo-report.json
artifacts/portfolio/portfolio-demo-report.md
```

架构、现场演示顺序、简历描述和面试追问材料见：

```text
docs/system_architecture.md
docs/portfolio_demo.md
docs/interview_guide.md
```

离线 6/6 证明的是编排和工程契约可重复，不代表真实 BGE 召回、LLM 回答质量、外部网络或
生产 SLA。

---

## 25. 规划系统架构（含未实现组件）

```text
Client
  │
  ▼
FastAPI API
  │
  ├── Authentication Context
  ├── Request ID
  ├── Session ID
  └── Input Validation
  │
  ├── Security Boundary
  │   ├── Prompt Injection Guard
  │   ├── Trusted Policy Access Context
  │   └── Safe Correlated Rejection
  │
  ▼
Application Services
  │
  ├── Transaction Agent Orchestrator
  │   ├── Intent Router
  │   ├── Conversation State
  │   ├── Bounded Conversation Memory
  │   ├── Human Confirmation Node
  │   └── Error Recovery
  │
  ├── Policy Research Assistant
  │   ├── Internal Policy RAG（authoritative）
  │   └── Optional Web Search（advisory）
  │
  ├── LLM Boundary
  │   ├── Sensitive / Size Bypass
  │   ├── SHA-256 Exact-request Key
  │   ├── Optional Redis Cache
  │   ├── Process-local Async Single-flight
  │   ├── Provider Concurrency + FIFO Backpressure
  │   └── OpenAI-compatible Upstream
  │
  ├── Runtime Observability
  │   ├── Request ID + Safe JSON Access Log
  │   ├── Bounded Route-template Metrics
  │   └── Prometheus-compatible Export
  │
  ├── search_policy
  ├── check_required_materials
  ├── create_application_draft
  └── submit_mock_approval
       │
       ├── RAG Service
       │   ├── Document Loader Registry
       │   │   ├── Markdown Loader
       │   │   ├── PDF Native-text Loader
       │   │   └── DOCX / OCR（planned）
       │   ├── Policy Parser
       │   ├── Metadata Extractor
       │   ├── Chunker
       │   ├── Embedding
       │   ├── Vector Search
       │   ├── BM25 Search（planned）
       │   └── Reranker（contract only）
       │
       ├── Policy Repository
       ├── Application Repository
       ├── Workflow Repository
       ├── User Repository
       └── Audit Log

Offline Performance Analysis
  ├── Repeatable Scenario Benchmark
  ├── p50 / p95 / Error Budget
  ├── cProfile Hotspots
  ├── Optional py-spy / Scalene
  ├── Concurrent Load Shapes
      ├── Hot Key / Mixed Hotset / Unique Keys
      ├── Throughput / End-to-end p95 / Error Rate
      └── Upstream Amplification / Provider Peak
  └── Model Batch Optimization
      ├── Embedding Documents / Reranker Candidates
      ├── Provider Calls / Internal Batches / Throughput
      └── Exact Output and Order Equivalence
```

---

## 26. 项目目录

```text
demo1/
├── .github/
│   ├── workflows/
│   │   └── ci.yml
│   └── dependabot.yml
├── app/
│   ├── api/
│   ├── core/
│   ├── cache/
│   ├── llm/
│   ├── rag/
│   ├── agent/
│   ├── memory/
│   ├── tools/
│   ├── persistence/
│   ├── resilience/
│   ├── research/
│   ├── performance/
│   ├── observability/
│   ├── security/
│   ├── portfolio/
│   ├── evaluation/
│   ├── repositories/
│   └── schemas/
├── data/
│   ├── policies/
│   └── samples/
│       └── applications/
├── docs/
│   ├── tool_contracts/
│   ├── docker_deployment.md
│   ├── continuous_integration.md
│   ├── conversation_memory.md
│   ├── agent_error_handling.md
│   ├── policy_research_assistant.md
│   ├── performance_analysis.md
│   ├── redis_llm_cache.md
│   ├── async_llm_singleflight.md
│   ├── async_concurrency_load.md
│   ├── embedding_reranker_batching.md
│   ├── provider_backpressure.md
│   ├── runtime_observability.md
│   ├── rag_security_guardrails.md
│   ├── document_loader.md
│   ├── pdf_document_parsing.md
│   ├── system_architecture.md
│   ├── portfolio_demo.md
│   ├── interview_guide.md
│   └── week3_milestone.md
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── evaluation/
│   ├── deployment/
│   └── fixtures/
├── scripts/
├── Dockerfile
├── compose.yaml
├── .dockerignore
├── .env.example
├── .gitattributes
├── .gitignore
├── pyproject.toml
└── README.md
```

---

## 27. 当前开发环境

```text
操作系统：Windows
终端：PowerShell
Python：3.12.10
FastAPI：0.140.8
pytest：9.1.1
PDF Parser：PyMuPDF 1.26.x
Tenacity：9.1.x
Web Search：默认关闭；可选 Tavily HTTP API
LLM 缓存：本机默认关闭；Compose 使用 Redis 8.10.0
异步合并：缓存启用时默认跟踪最多 128 个 single-flight 在途键
并发负载：默认每场景 24 请求、客户端并发 12、固定离线 I/O 15 ms
Provider 背压：默认关闭；示例上限 4、FIFO 队列 16、排队超时 2 秒
运行时观测：JSON 访问日志、64 个路由键、固定延迟直方图和 `/metrics`
性能基线：Python 内置 perf_counter_ns 与 cProfile
采样 Profiler：可选 py-spy 0.4.x、Scalene 2.x
Docker Desktop：使用 Docker Compose v2
```

虚拟环境 Python 路径：

```text
D:\Ai_agent_program\demo1\.venv\Scripts\python.exe
```

创建虚拟环境：

```powershell
python -m venv .venv
```

激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

安装运行和开发依赖：

```powershell
python -m pip install -e ".[dev]"
```

可选安装 Day 22 采样 profiler：

```powershell
python -m pip install -e ".[dev,profiling]"
```

验证环境：

```powershell
python --version
python -c "import fastapi, pytest; print('FastAPI:', fastapi.__version__); print('pytest:', pytest.__version__)"
```

---

## 28. 数据验证命令

### 验证 5 份制度

```powershell
python -c "from pathlib import Path; files=sorted(Path('data/policies').glob('*.md')); print('制度数量:',len(files)); [print(f.name) for f in files]"
```

### 验证 6 份申请样例

```powershell
python -c "import json; from pathlib import Path; files=sorted(Path('data/samples/applications').glob('*.json')); [json.loads(f.read_text(encoding='utf-8-sig')) for f in files]; print('申请数量:',len(files)); print('全部解析成功')"
```

### 验证 4 份工具契约

```powershell
python -c "import json; from pathlib import Path; files=sorted(Path('docs/tool_contracts').glob('*.json')); [json.loads(f.read_text(encoding='utf-8-sig')) for f in files]; print('工具数量:',len(files)); print('全部解析成功')"
```

### 验证 30 条黄金测试

```powershell
python -X utf8 -m scripts.run_golden_evaluation --mode offline
```

### 验证 CI 配置契约

```powershell
python -X utf8 -m scripts.verify_ci_configuration
```

### 验证持久化对话记忆

```powershell
python -X utf8 -m scripts.verify_conversation_memory
```

### 验证 Agent 工具容错

```powershell
python -X utf8 -m scripts.verify_agent_resilience
```

### 验证受控制度研究助手

```powershell
python -X utf8 -m scripts.verify_policy_research
```

### 验证性能基准与 cProfile

```powershell
python -X utf8 -m scripts.verify_agent_performance
python -X utf8 -m scripts.run_performance_benchmark --warmups 1 --iterations 5
```

### 验证 Redis LLM 响应缓存

```powershell
python -X utf8 -m scripts.verify_llm_cache
```

### 验证异步 LLM single-flight

```powershell
python -X utf8 -m scripts.verify_async_singleflight
```

### 验证异步并发负载

```powershell
python -X utf8 -m scripts.verify_concurrency_load
python -X utf8 -m scripts.run_concurrency_load_test --requests 24 --concurrency 12
```

### 验证 Embedding/Reranker 批处理

```powershell
python -X utf8 -m scripts.verify_embedding_reranker_batching
python -X utf8 -m scripts.run_batch_optimization --items 32 --batch-size 8
```

### 验证 LLM Provider 背压

```powershell
python -X utf8 -m scripts.verify_provider_backpressure
```

### 验证请求关联与运行时可观测性

```powershell
python -X utf8 -m scripts.verify_runtime_observability
```

### 验证 RAG 权限与提示注入防护

```powershell
python -X utf8 -m scripts.verify_rag_security
```

### 验证 Phase 22 Document Loader

```powershell
python -X utf8 -m scripts.verify_document_loader
```

### 验证 Phase 23 PDF 文档解析

```powershell
python -X utf8 -m scripts.verify_pdf_document_parsing
```

### 运行 Day 30 作品集演示与发布验收

```powershell
python -X utf8 -m scripts.run_portfolio_demo --output-dir artifacts/portfolio
python -X utf8 -m scripts.verify_portfolio_release
```

---

## 29. 开发路线

### Phase 1：需求建模与工程骨架

- [x] 模拟企业制度；
- [x] 业务申请样例；
- [x] 工具契约；
- [x] 黄金测试集；
- [x] 项目 README。

### Phase 2：文档解析与基础检索

- [x] Markdown 文档解析；
- [x] YAML 元数据提取；
- [x] 章节和条款切分；
- [x] Chunk 数据模型；
- [ ] 基础关键词检索；
- [x] 制度引用结构；
- [x] 单元测试。

### Phase 3：向量检索与混合 RAG

- [x] Embedding 接入；
- [x] 内存向量索引；
- [x] Reranker 批量 Provider 与稳定排序契约；
- [ ] BM25；
- [ ] Hybrid Search；
- [ ] Reranker 接入正式检索链路；
- [ ] Query Rewrite；
- [x] 引用生成；
- [ ] RAG 评测。

### Phase 4：Agent 工具实现

- [x] 实现 `search_policy`；
- [x] 实现 `check_required_materials`；
- [x] 实现 `create_application_draft`；
- [x] 实现 `submit_mock_approval`；
- [x] Pydantic 输入输出模型；
- [x] 工具单元测试；
- [x] 工具错误处理。

### Phase 5：Agent 状态机

- [x] 意图路由；
- [x] 会话状态；
- [x] 多轮字段收集；
- [x] 受限对话记忆；
- [x] 多轮省略指代消解；
- [ ] 相对日期确认；
- [x] 人在回路确认节点；
- [x] 工具调用编排；
- [x] 错误恢复；
- [x] 重试和超时。

### Phase 6：FastAPI 与数据持久化

- [x] REST API；
- [x] SQLite；
- [x] SQLite 对话历史；
- [ ] Redis 会话状态；
- [x] 申请数据库；
- [x] 审批工作流数据库；
- [x] 审计日志；
- [x] 幂等提交；
- [x] API 集成测试。

### Phase 7：评测与安全

- [x] 自动运行黄金测试；
- [ ] 制度问答正确率；
- [x] 引用正确率；
- [x] 工具选择准确率；
- [x] 缺失材料识别率；
- [x] 意图识别准确率；
- [x] 审批路线准确率；
- [x] 权限拒绝成功率；
- [x] 提示注入测试；
- [x] 重复提交阻止率；
- [x] 错误案例明细报告。

### Phase 8：受控制度研究

- [x] 内部制度 RAG 优先；
- [x] 显式授权的外部 Web Search；
- [x] 内外来源分区和权威边界；
- [x] 查询脱敏与外部结果限制；
- [x] 外部搜索重试、局部降级和离线验收。

### Phase 9：性能分析

- [x] 五个离线代表场景；
- [x] warm-up 与串行重复测量；
- [x] p50 / p95 / 错误率报告；
- [x] 固定性能预算门禁；
- [x] cProfile 项目热点；
- [x] py-spy / Scalene 可选环境；
- [x] 热点、hotset、唯一键三种完全离线并发负载；
- [x] 端到端 p95、吞吐、错误率和上游放大证据；
- [x] Embedding/Reranker 逐条与批量调用对照；
- [x] Provider 调用、内部批次、等价性和吞吐报告；
- [x] 单进程统一 LLM Provider 并发门禁；
- [x] FIFO 有界队列、排队超时、取消清理和安全 503；
- [ ] 真实模型与 Provider 基线；
- [ ] 生产级持续压测与跨进程 Provider 配额协调；
- [ ] 基于证据的性能优化。

### Phase 10：缓存优化

- [x] Redis 精确请求 LLM 响应缓存；
- [x] 模型和消息变化自动失效；
- [x] TTL 与内存边界；
- [x] 敏感内容和超大请求绕过；
- [x] Redis 故障安全降级；
- [x] 命中、未命中、写入、绕过和错误指标；
- [x] 单进程相同 cache miss 的异步 single-flight；
- [x] 取消隔离、异常清理和 bounded 在途键注册表；
- [ ] 多实例 single-flight；
- [ ] 真实 LLM 延迟和成本节省基线。

### Phase 11：部署与作品集整理

- [x] Docker 多阶段镜像；
- [x] Docker Compose；
- [x] 健康检查；
- [x] SQLite 持久卷自动验收；
- [x] CI；
- [x] 请求 ID 与安全 JSON 访问日志；
- [x] 有界 HTTP 指标与 Prometheus 兼容端点；
- [ ] Prometheus/Grafana 集中采集和告警；
- [ ] OpenTelemetry 跨服务 Trace；
- [ ] 演示数据初始化；
- [x] 演示脚本；
- [x] 架构图；
- [ ] 项目截图；
- [x] 简历项目描述；
- [x] 面试讲解材料。

### Advanced RAG Phase 22：Document Loader 抽象

- [x] 格式无关的 `LoadedDocument` 输出契约；
- [x] 最小 `DocumentLoader` Protocol；
- [x] 不可变扩展名 Registry 和冲突拒绝；
- [x] UTF-8/BOM Markdown Loader；
- [x] Parser、Chunker、Retriever 增量接入；
- [x] 原有安全边界和 5 文档/199 Chunk 回归验证；
- [x] PDF Loader（Phase 23）；
- [ ] DOCX Loader（Phase 24）；
- [ ] OCR fallback（Phase 25）。

### Advanced RAG Phase 23：PDF 文档解析

- [x] PyMuPDF 原生文本层与 `sort=True` 阅读顺序；
- [x] `policy.pdf` + `policy.metadata.yaml` 可信 ingestion 契约；
- [x] 纯文本章/条标题规范化并复用现有 Chunker；
- [x] Loader 行到 PDF 页码的稳定映射；
- [x] `PolicyChunk`、Context、Citation 和 Research API 页码传递；
- [x] 加密、损坏、缺元数据和 OCR-required 错误边界；
- [x] 完全离线真实 PDF 生成与 CI 验收；
- [ ] DOCX Loader（Phase 24）；
- [ ] OCR fallback（Phase 25）。

---

## 30. 设计原则

本项目遵循以下原则：

```text
LLM 负责理解用户意图和生成自然语言
确定性代码负责金额、日期、余额和规则计算
检索系统负责提供制度证据
权限系统负责控制数据访问
用户负责高影响操作的最终确认
幂等机制负责防止重复提交
审计系统负责记录关键行为
外部公开资料只供研究参考，不覆盖企业内部有效制度
性能优化必须先有可重复基线、预算和 profiler 证据
缓存只能优化可重建结果，不能成为审批状态或业务正确性的来源
相同异步请求可以共享结果，但取消、异常和敏感内容边界必须显式设计
Embedding 与 Reranker 可以批量推理，但输出数量、顺序和相关性必须先通过等价性验证
Provider 容量不足时必须有限排队并安全拒绝，不能用无界 Task 隐藏过载
运行指标必须使用有界路由模板，日志不得把用户输入或异常正文当作访问字段
权限过滤必须发生在向量评分和 Prompt 构造之前，聊天自述不能覆盖可信身份
用户输入和检索证据都是不可信数据，命中攻击时必须在任何外部调用或工具执行前拒绝
作品集数字必须有可执行证据，离线夹具结果不得冒充真实模型或生产 SLA
```

Agent 的目标不是无限自主，而是在明确业务边界内安全地完成任务。

---

## 31. 预期评测指标

Day 16 当前质量门禁：

| 指标 | 门槛 |
|---|---:|
| 意图识别准确率 | ≥ 90% |
| 工具选择准确率 | 100% |
| 材料检查准确率 | 100% |
| 审批路线准确率 | 100% |
| 制度引用准确率 | 100% |

后续仍需补充制度问答语义正确率、真实认证用户矩阵、持续红队和真实 LLM 回归基线。

Day 22 离线性能预算：

| 场景 | p95 上限 | 错误率上限 |
|---|---:|---:|
| 运行时启动 | 750 ms | 0% |
| 制度 RAG 回答 | 150 ms | 0% |
| 材料路由 | 250 ms | 0% |
| 审批路由 | 250 ms | 0% |
| 混合研究 | 250 ms | 0% |

这些数值是离线回归护栏，不是生产 SLA，也不包含真实模型或网络延迟。

Day 24 专项并发契约固定验证：12 个相同 cache miss 只产生 1 次上游调用和 1 次缓存写入，
其余 11 个请求复用 leader 结果。该契约证明请求合并逻辑，不代表生产环境吞吐量或 SLA。

Day 25 专项负载固定使用三种请求分布、每场景 24 请求和客户端并发 12。预期上游调用分别
为 1、4、24，唯一键调用放大率均为 1.00x；p95 和吞吐保留为同环境趋势证据，不设置跨机器
绝对预算，也不代表真实 DeepSeek/OpenAI-compatible Provider SLA。

Day 26 专项批处理固定使用每场景 32 条输入和 batch size 8。Embedding 与 Reranker 的
Provider 调用都应从 32 次降为 1 次，内部批次从 32 降为 4；输出摘要和顺序必须完全等价。
离线 fixture 的吞吐提升只验证方法，不代表真实 BGE 模型或硬件 SLA。

Day 27 专项背压固定使用 Provider 并发 2、FIFO 队列 2：5 个请求中接纳并完成 4 个、立即
拒绝 1 个，峰值执行和排队都不得超过 2；另行验证排队超时、取消清理、关闭资源和默认关闭
时的兼容直通。该契约是单进程容量边界，不代表跨实例限流或真实 Provider SLA。

Day 28 专项观测固定产生 3 个业务请求和 2 个路由模板，验证请求 ID 关联、成功/5xx 计数、
固定 histogram、监控端点不自计数、500 脱敏和 Prometheus 格式。原始路径参数、query、凭据
形态和异常正文不得出现在状态、指标、访问事件或错误响应中。

Day 29 专项安全评测固定验证 7 个权限拒绝边界、6 个高信号攻击和 4 个正常安全问题：权限
拒绝、攻击拦截和正常放行在固定夹具中均须为 100%，被拒绝输入的 Provider 调用必须为 0，
未授权与污染制度内容不得进入模型上下文。该结果不代表开放世界攻击检出率。

Day 30 作品集发布门禁固定串行运行六个场景，要求相关制度引用、七项差旅材料、四步 IT 采购
审批、人工确认、幂等重放、`S/W` 来源分区和提示注入零 Provider 调用全部通过。演示使用
确定性词法向量、固定 LLM 和固定 Web 夹具，证明集成契约而非真实模型效果。

---

## 32. 作品集价值

项目完成后，可以用于展示以下能力：

- 企业 RAG 方案设计；
- 文档解析和检索工程；
- Agent 工作流编排；
- Tool Calling；
- Pydantic 数据建模；
- FastAPI 后端开发；
- 权限和隐私设计；
- 人在回路；
- 幂等性；
- 自动化测试；
- RAG 与 Agent 评测；
- Docker 和工程化部署。
- GitHub Actions 持续集成与供应链门禁。
- 会话隔离、持久化和受限上下文记忆。
- 内部 RAG 与显式授权 Web Search 的安全整合。
- 离线性能基准、p95 预算和 cProfile 热点分析。
- Redis cache-aside、精确失效、隐私绕过和故障安全降级。
- asyncio Task 协调、single-flight 防击穿、取消隔离和并发竞态测试。
- 并发 load shape、端到端 p95、吞吐、上游放大率和容量边界分析。
- Embedding/Reranker 批量推理、稳定排序、等价性门禁和吞吐对照。
- 请求关联、结构化日志、指标基数控制、Prometheus 格式和安全错误观测。
- 检索前 ABAC 边界、提示注入纵深防御、污染证据隔离和安全回归评测。
- 可重复的一键演示、机器可读发布证据、架构讲解和简历/面试材料。

相比普通 PDF 问答项目，本项目增加了：

```text
制度版本
权限过滤
业务规则
材料校验
申请草稿
用户确认
模拟提交
审批工作流
安全测试
回归评测
```

---

## 33. 免责声明

本仓库仅用于：

- 个人学习；
- 技术演示；
- 求职作品集；
- AI Agent 工程实践。

仓库中的所有：

- 企业制度；
- 审批标准；
- 员工信息；
- 联系方式；
- 客户信息；
- 供应商信息；
- 费用金额；
- 申请记录；
- 工作流结果；

均为模拟内容。

本项目不提供法律、财务、人力资源、劳动关系或企业合规建议。
