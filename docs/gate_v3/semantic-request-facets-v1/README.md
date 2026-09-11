# Semantic Request Facets v0.1

状态：proposal_pending_confirmation。本文档和 facets.json 定义请求信息维度草案，供用户审核；它们不是现有 SemanticParseRecord v1.0 的新增字段，也不是模型输出。

## 本步骤解决的问题

已确认的 semantic-dev-v1 开发集显示，七条样本中有六条包含当前动作契约无法完整表达的请求：材料、时间阈值、开放式处理、责任人或流程选择。现有 VERIFY_ASSERTION、DENY_REQUEST、REPORT_ASSERTION 表达用户或被引用内容的语义动作；它们不足以表达用户希望获得哪类制度信息。

本步骤把这两个层次分开：

- SemanticAct：当前用户在做什么，或正在转述什么；
- RequestFacet：用户想获取哪类信息。

这样可以把“还能直接走普通报销吗”作为 VERIFY_ASSERTION，把“制度对这种超时怎么处理”作为 PROCEDURE，两者不会被压成同一个正则动作。

## 版本和兼容策略

本草案使用 semantic-request-facets-v0.1-draft-1。现有 SemanticParseRecord v1.0、语义动作枚举、Gate 选择器和生产运行时保持不变。

facets.json 是与 records.jsonl 通过 record_line、case_id、query 和 source_spans 绑定的 sidecar 设计。v1.0 严格解码器不读取它，因此未知字段规则不会被绕过。待本草案获确认后，再单独设计 v1.1 的序列化方式或 sidecar adapter；本步骤不提前选择，也不迁移旧记录。

## 请求信息维度

| facet | 含义 | 典型问法 | 排除 |
| --- | --- | --- | --- |
| PROCEDURE | 如何处理、需要哪些步骤或办理方式 | 怎么办、怎么处理 | 仅询问能否办理、材料、时间或责任人 |
| MATERIAL | 需要补充、提交或准备的说明、凭证或材料 | 补什么说明、需要什么材料 | 不把财务职责自动解释为材料或复核 |
| DEADLINE | 时间点、期限、天数阈值或逾期后何时可采取动作 | 多久后、什么时候 | 只陈述已经过去的天数 |
| RESPONSIBLE_ROLE | 部门、岗位、人员或审批主体 | 哪些人、由谁审批、哪个部门 | 不补制度未指定的人名或岗位 |
| PROCESS_CHOICE | 在多个流程之间选择哪一个 | 按哪个流程、走哪条流程 | 单纯询问步骤归 PROCEDURE |

每个 facet 还记录 binding：

- BOUND：请求维度和目标对象都有可回查的唯一原文绑定；
- UNBOUND：请求维度出现，但目标对象、候选流程或断言内容不唯一；
- MISSING：当前样本没有安全可标注的请求维度，或现有契约没有对应表达。

EXPRESSED 只表示原文出现了对应信息需求，不代表制度答案正确，也不代表语义模型预测正确。

## 七条草案映射

| case_id | proposed facet | binding | 说明 |
| --- | --- | --- | --- |
| COV-007 | MATERIAL、RESPONSIBLE_ROLE | BOUND | 询问补充说明及部门、财务分工；动作仍保留 contract_gap |
| COV-008 | DEADLINE | BOUND | 询问拒收时间阈值；“不需要审批流程”是范围排除 |
| COV-009 | PROCEDURE | BOUND | 询问逾期如何处理；普通报销可行性由 VERIFY_ASSERTION 表达 |
| COV-010 | DEADLINE、RESPONSIBLE_ROLE | BOUND | 差旅说明时间、接收人及普通费用审批主体分开保留 |
| V3U-004 | PROCEDURE | UNBOUND | “怎么办”已出现，但“这个”的具体办理目标不唯一 |
| V3U-006 | 无 | MISSING | 主要缺口是“那个要求”的断言对象，不强行补流程维度 |
| V3U-009 | PROCESS_CHOICE | UNBOUND | 流程选择已出现，但候选流程不唯一；“都说过了”仍是未知内容的转述 |

各条 source_spans 必须精确回到原始 query。unresolved_outputs 保留原有 ambiguous 和 contract_gap，不把缺口误写成 facet 预测。

## 审核状态

facets.json 的七条记录全部为 pending_review，accepted_facet_labels 为 null。本草案继承 semantic-dev-v1-confirmed-1 的七条用户确认语义记录，但本次新增维度尚未确认。

审核时需要逐条确认：

1. facet 是否识别了用户想获取的信息类别；
2. binding 是否准确表达目标对象是否唯一；
3. 是否存在把事实陈述误判为信息需求的情况；
4. 是否需要新增维度，还是应该保留为契约缺口。

接受某个 facet 不会改变历史检索标签、v3/v3.1 决策结果或旧记录内容。

## 复核命令

标准 PowerShell：

~~~powershell
Set-Location D:\Ai_agent_program\demo1
.\\.venv\Scripts\python.exe -X utf8 -m unittest tests.evaluation.test_semantic_request_facets -q
~~~

本步骤只使用标准库读取 JSON 和现有 confirmed records，未调用模型、未执行检索、未计算准确率。测试通过只表示版本、样本映射、枚举、Span 和审核状态满足草案结构。

草案确认后，下一步再考虑 v1.1 序列化或 sidecar adapter；在此之前不接入 SemanticParser、Gate 或生产运行时。
