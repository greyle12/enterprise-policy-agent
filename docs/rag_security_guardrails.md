# Day 29：RAG 权限过滤与提示注入防护

Day 29 把 README 中原本只有设计说明的两项安全要求变成可执行代码：制度 Chunk 必须先通过
可信身份授权才能参与 Vector/BM25 候选与评分；用户输入和检索证据必须先经过提示注入检查才能进入 LLM、
Web Search、Agent 工作流或工具。所有专项测试完全离线，不发送真实问题或制度到外部服务。

这是一层确定性纵深防御，不等同于完整身份认证、生产级 DLP 或对所有自然语言攻击的证明。

## 1. Learn：为什么“在 Prompt 里要求模型保密”不够

错误链路：

```text
检索全部制度
→ 把无权限或污染内容放入 Prompt
→ 要求模型不要泄露或不要执行
```

此时敏感内容已经越过权限边界，提示注入也已经进入模型上下文。正确链路是：

```text
服务端可信身份
→ 生命周期/等级/部门/角色/区域过滤
→ 仅在授权 ID 范围内执行 Vector/BM25
→ RRF 融合授权候选
→ 可选 Reranker 只接收授权候选
→ 用户输入安全检查
→ 检索证据污染检查
→ JSON 数据边界
→ LLM 与引用校验
```

安全职责必须由确定性代码承担，不能让模型根据用户一句“我是管理员”自行提升权限。

## 2. Build：检索前权限过滤

### 2.1 可信身份上下文

`PolicyAccessContext` 只接受服务端构造的身份属性：

| 属性 | 用途 |
|---|---|
| `employee_id` | 稳定员工标识 |
| `department` | 部门范围匹配 |
| `roles` | 角色集合匹配，统一转为大写 |
| `security_clearance` | `public < internal < sensitive < core` |
| `region` | 地域范围匹配 |
| `identity_source` | 受限枚举：认证层、测试夹具或可信演示上下文 |

聊天正文、请求 JSON 和任意客户端 Header 都不能覆盖这些字段。当前演示服务固定注入
`DEMO-EMP-001` 的 `EMPLOYEE/internal/中国大陆` 上下文；生产环境必须替换为真实认证系统的
服务端适配器。

### 2.2 授权规则

每个 Chunk 在检索前同时检查：

- 文档状态必须为 `effective`；
- 当前日期不得早于 `effective_date`；
- 当前日期不得晚于 `expiry_date`；
- 用户 clearance 必须覆盖文档 `security_level`；
- 部门必须匹配 `allowed_departments`，`ALL` 表示不限制部门；
- 至少一个可信角色匹配 `allowed_roles`，`ALL` 表示不限制角色；
- 文档指定地域时必须与可信地域一致。

拒绝的 Chunk ID 不会参与余弦相似度计算，也不会进入引用、Prompt、日志或错误正文。没有可用
Chunk 时沿用普通“未检索到制度依据”回答，避免通过不同错误枚举敏感制度是否存在。

Advanced RAG Phase 29 迁移到 pgvector 后仍保持同一顺序：授权 ID 作为 SQL 数组参数，先构造
`MATERIALIZED authorized_records`，外层才执行 `<=>` cosine distance。不是先从数据库取全库 Top-K
再在 Python 中过滤；空授权集合也不会发起向量查询。

## 3. Build：提示注入与证据污染防护

### 3.1 用户输入

固定规则集 `day29-v1` 在执行前检查中英文高信号攻击：

- 覆盖或忽略 system/developer/security 指令；
- 索取 system prompt、隐藏指令、密钥、Token 或密码；
- 通过自称管理员、财务或安全角色提升权限；
- 切换为无约束、DAN、developer 或 root 角色；
- 绕过确认、权限或审批直接调用工具；
- 要求解码并执行 Base64/ROT13 指令；
- 使用伪造 `<system>`、`[SYSTEM]` 或 developer 边界。

检查前执行 Unicode NFKC 规范化、零宽字符移除和空白折叠。命中后立即抛出稳定安全错误，
不会调用 Embedding、LLM、Web Provider、意图分类器或业务工具。

### 3.2 检索证据

制度标题、章节、条款和正文使用同一规则集检查。疑似污染 Chunk 被隔离，其内容不会出现在
模型上下文；剩余证据重新连续编号为 `S1...Sn`，防止引用映射出现空洞。

安全证据使用 JSON 数组序列化，并放入明确的 `<policy_evidence_json>` 数据边界。用户问题也
单独 JSON 编码。System Prompt 明确要求两者都是不可信数据，不能覆盖系统规则。

规则、权限过滤、证据隔离和引用输出构成纵深防御；其中任何一层都不能单独宣称彻底解决提示
注入问题。

## 4. Measure：安全拒绝和无内容指标

命中输入返回 HTTP 400：

```json
{
  "detail": {
    "code": "prompt_injection_blocked",
    "message": "The request was rejected by the input security policy."
  },
  "request_id": "security-request-001"
}
```

响应不会返回命中规则、原始输入、制度内容或内部异常。使用请求 ID 可以关联固定日志事件。

进程内安全状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/security/status |
  ConvertTo-Json -Depth 6
```

只返回：

- 用户输入检查数与拒绝数；
- 证据 Chunk 检查数与隔离数；
- 因拒绝而避免的 LLM 调用数；
- 规则集版本和 `raw_content_recorded=false`。

`/metrics` 同时导出：

```text
enterprise_policy_agent_prompt_security_available
enterprise_policy_agent_prompt_security_user_inputs_total{outcome="allowed|blocked"}
enterprise_policy_agent_prompt_security_evidence_chunks_total{outcome="allowed|quarantined"}
enterprise_policy_agent_prompt_security_llm_calls_avoided_total
```

状态和 Prometheus 指标不会包含原始问题、匹配文本、规则明细或制度正文，也不会把自身计入
HTTP 业务指标。

## 5. Test：完全离线专项验收

Windows PowerShell：

```powershell
Set-Location D:\Ai_agent_program\demo1
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_rag_security
```

成功时关键结果：

```json
{
  "passed": true,
  "rule_set_version": "day29-v1",
  "permission_cases": 7,
  "permission_denial_accuracy": 1.0,
  "attack_cases": 6,
  "prompt_injection_block_accuracy": 1.0,
  "benign_cases": 4,
  "benign_allow_accuracy": 1.0,
  "provider_calls": 0,
  "network_calls": false,
  "live_llm_calls": false
}
```

这些 `100%` 只描述仓库内固定离线用例，不代表开放世界攻击检出率。

全量门禁：

```powershell
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_ci_configuration
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_runtime_observability
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_rag_security
```

Compose 当前镜像标签为 `enterprise-policy-agent:day30`。启动后可用 `/api/v1/security/status` 和
`/metrics` 验证计数。

## 6. Improve：当前边界

Day 29 仍未实现：

- 登录、JWT/OIDC、员工目录和逐请求真实身份解析；
- 多租户 ABAC/RBAC 策略服务和集中式策略版本管理；
- 针对分词、同形字、图片、音频、长上下文和多轮组合攻击的完整检测；
- 模型辅助分类、人工复核、持续红队和生产攻击样本回放；
- 对制度入库来源的签名、审批、恶意文档扫描和供应链治理；
- 集中安全事件平台、跨实例指标和自动告警；
- 权限拒绝原因对授权管理员的独立审计查询。

确定性规则可能产生误报，也可能漏掉新型或高度隐蔽的攻击。生产应用应结合认证授权、最小
权限、可信入库、结构化工具、输出校验、监控告警和持续红队，而不是单独依赖关键词规则。
