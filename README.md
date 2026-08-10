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
Phase 9：持续集成与质量门禁（Day 18 已完成）
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

### 尚未实现

- [ ] PDF 文档解析；
- [ ] PostgreSQL / pgvector；
- [ ] BM25 关键词检索；
- [ ] Hybrid Search；
- [ ] Rerank；
- [ ] Redis 会话状态；
- [ ] 权限过滤与提示注入专项评测；
- [ ] 日志和可观测性。

当前仓库不能被描述为“已经完成的企业级 Agent”。

更准确的状态是：

```text
已完成制度 RAG、确定性业务工具、LangGraph 多轮流程、
幂等模拟提交、SQLite 重启恢复、自动化黄金集评测和 Docker Compose 部署；
当前还具备自动 CI 质量门禁，定位仍是可容器化运行的单机个人作品集版本，
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

当前定义了 4 个工具。

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

---

### 9.3 敏感信息保护

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

---

### 9.4 人在回路

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

### 9.5 幂等控制

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

### 9.6 审计记录

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
- 非 root 用户运行应用；
- 只读根文件系统；
- `no-new-privileges` 和全部 Linux capability 移除；
- readiness 作为容器健康判断依据。

`docker compose down` 会保留两个具名卷；`docker compose down --volumes` 会删除本地运行数据和模型缓存。

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
→ 构建 Python Wheel
```

向 `main` 或 `master` 推送时，在 Python 质量门禁通过后继续验证：

```text
Docker Compose 配置
→ 完整 Docker 镜像构建
→ 非 root 用户和 Python 包检查
```

Pull Request 还会检查新引入的高危或严重依赖漏洞。构建结果保留 pytest JUnit、
黄金评测 JSON/Markdown 和可安装 Wheel，方便查看失败原因和保存可复现证据。

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

## 13. 计划系统架构

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
  ▼
Agent Orchestrator
  │
  ├── Intent Router
  ├── Conversation State
  ├── Human Confirmation Node
  ├── Error Recovery
  │
  ├── search_policy
  ├── check_required_materials
  ├── create_application_draft
  └── submit_mock_approval
       │
       ├── RAG Service
       │   ├── Document Parser
       │   ├── Metadata Extractor
       │   ├── Chunker
       │   ├── Embedding
       │   ├── Vector Search
       │   ├── BM25 Search
       │   └── Reranker
       │
       ├── Policy Repository
       ├── Application Repository
       ├── Workflow Repository
       ├── User Repository
       └── Audit Log
```

---

## 14. 项目目录

```text
demo1/
├── .github/
│   ├── workflows/
│   │   └── ci.yml
│   └── dependabot.yml
├── app/
│   ├── api/
│   ├── core/
│   ├── llm/
│   ├── rag/
│   ├── agent/
│   ├── tools/
│   ├── persistence/
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
│   └── continuous_integration.md
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

## 15. 当前开发环境

```text
操作系统：Windows
终端：PowerShell
Python：3.12.10
FastAPI：0.140.8
pytest：9.1.1
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

验证环境：

```powershell
python --version
python -c "import fastapi, pytest; print('FastAPI:', fastapi.__version__); print('pytest:', pytest.__version__)"
```

---

## 16. 数据验证命令

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

---

## 17. 开发路线

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
- [ ] BM25；
- [ ] Hybrid Search；
- [ ] Rerank；
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
- [ ] 相对日期确认；
- [x] 人在回路确认节点；
- [x] 工具调用编排；
- [ ] 错误恢复；
- [ ] 重试和超时。

### Phase 6：FastAPI 与数据持久化

- [x] REST API；
- [x] SQLite；
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
- [ ] 权限拒绝成功率；
- [ ] 提示注入测试；
- [x] 重复提交阻止率；
- [x] 错误案例明细报告。

### Phase 8：部署与作品集整理

- [x] Docker 多阶段镜像；
- [x] Docker Compose；
- [x] 健康检查；
- [x] SQLite 持久卷自动验收；
- [x] CI；
- [ ] 演示数据初始化；
- [ ] 演示脚本；
- [ ] 架构图；
- [ ] 项目截图；
- [ ] 简历项目描述；
- [ ] 面试讲解材料。

---

## 18. 设计原则

本项目遵循以下原则：

```text
LLM 负责理解用户意图和生成自然语言
确定性代码负责金额、日期、余额和规则计算
检索系统负责提供制度证据
权限系统负责控制数据访问
用户负责高影响操作的最终确认
幂等机制负责防止重复提交
审计系统负责记录关键行为
```

Agent 的目标不是无限自主，而是在明确业务边界内安全地完成任务。

---

## 19. 预期评测指标

Day 16 当前质量门禁：

| 指标 | 门槛 |
|---|---:|
| 意图识别准确率 | ≥ 90% |
| 工具选择准确率 | 100% |
| 材料检查准确率 | 100% |
| 审批路线准确率 | 100% |
| 制度引用准确率 | 100% |

后续仍需补充制度问答语义正确率、权限越界拒绝率、提示注入防护通过率和真实 LLM 回归基线。

---

## 20. 作品集价值

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

## 21. 免责声明

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
