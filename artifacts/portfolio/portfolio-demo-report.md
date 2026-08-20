# 企业制度 Agent Day 30 作品集演示报告

- 发布标签：`day30`
- 生成时间：`2026-08-20T08:31:04.992185+00:00`
- 制度文档：5
- 演示场景：6/6
- 质量门禁：**通过**

> 本报告完全离线。Hash 词法向量、固定 LLM 返回和固定 Web 结果只用于演示
> 编排、规则、安全与引用契约，不代表真实 BGE、LLM、网络效果或生产 SLA。

## 场景结果

| 场景 | 能力 | 耗时 | 观测证据 | 结果 |
|---|---|---:|---|---|
| `rag_citation` / 制度问答与引用 | 授权检索、上下文构造和 S 编号引用校验 | 15.140 ms | `{"answer_has_valid_citation":true,"authorized_chunk_count":199,"citation_source_ids":["S1"],"document_title":"差旅报销管理制度","embedding_fixture":"deterministic_lexical_hash_v1"}` | 通过 |
| `material_rules` / 材料完整性规则 | LangGraph 路由到确定性材料规则并返回制度条款 | 12.571 ms | `{"citation_articles":["第十六条"],"required_material_count":7,"status":"completed","workflow_terminal_node":"check_materials"}` | 通过 |
| `approval_route` / 审批路线计算 | 金额与业务条件由确定性代码生成审批链 | 6.652 ms | `{"approval_level":"general_purchase","approvers":["DIRECT_MANAGER","DEPARTMENT_HEAD","IT_DEPARTMENT","PROCUREMENT_DEPARTMENT"],"citation_articles":["第十二条","第七条"],"status":"completed"}` | 通过 |
| `human_in_loop` / 草稿、确认与幂等提交 | 副作用操作必须经过人工确认且重复提交复用结果 | 54.533 ms | `{"approval_step_count":4,"confirmed_status":"confirmed","created_status":"awaiting_confirmation","idempotent_replay":true,"same_submission_reused":true,"storage_backend":"in_memory","submitted_status":"submitted"}` | 通过 |
| `research_boundary` / 内外资料研究边界 | 内部 S 引用优先、显式 Web 授权和外部 W 引用分区 | 15.011 ms | `{"external_source_is_advisory":true,"external_sources":["W1"],"internal_sources":["S1"],"network_calls":0,"offline_web_fixture_calls":1,"status":"completed"}` | 通过 |
| `security_boundary` / 提示注入执行前拒绝 | 攻击输入在检索和 Provider 调用前阻止且不记录原文 | 0.069 ms | `{"attack_blocked":true,"blocked_input_delta":1,"llm_calls_avoided_delta":1,"provider_call_delta":0,"raw_attack_recorded":false}` | 通过 |

## 演示边界

- 演示复用真实制度解析、检索器、LangGraph、业务规则和安全边界；
- Embedding、LLM 和 Web Search 使用确定性离线夹具，不联网也不读取 API Key；
- 模拟提交只写入进程内存，不会连接真实 OA、ERP 或审批系统；
- 真实效果应另行使用经过授权的 Provider、身份系统和生产流量评测。
