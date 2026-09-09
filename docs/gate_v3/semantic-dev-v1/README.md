# 语义解析开发验收集：首版待审核草案

版本：semantic-dev-v1-draft-1。状态：pending_review。共 7 条，均为已有开发材料的再利用；已确认语义标签数为 0。不是独立留出集，没有运行语义解析模型，没有语义准确率、检索提升或端到端效果结论，numeric_resume_ready=false。

## 本步骤解决的问题

将历史失败和歧义对象整理为可逐条讨论的语义标注草案。records.jsonl 只放现有严格契约允许的字段；review.json 单独保存 case_id、行号、来源、待确认状态和审核问题。历史标签不能自动升级为本次语义金标。

source-excerpts.json 保留选中 COV 案例在窗口20/40的原始相关性标签、指标、v3.1 trace、回归列表，以及三个 V3U 原始确认条目。它还保存原报告的历史汇总及模型锁；其中任何历史数字、确认状态或 model revision 都不属于本次新评测。

## 样本选择和保留原则

这是按已观察问题选取的非随机开发子集，不能计算泛化成绩。COV-009 是尚存的检索回归；COV-008 是已修复误补的控制；COV-007/010 是不确定回退案例（COV-010 窗口20没有提案，窗口40回退）。V3U 条目是历史已确认的歧义控制标签，不声称它们本身是运行失败。

缺失的 v3.1 八条未见问法材料不重建、不改写、不纳入。引用说法/VERIFY_ASSERTION 的范围在本集很有限，不能认为已覆盖“有人说不用审批，请核实”等所有表达；问号作用域及混合否定也未形成充分覆盖。后续新问法须另起版本并审核。

原始 query、judgments、proposed_decision、rationale 和失败结果不改动。审核意见写入新版本/审查记录；不根据结果删样本、改历史标签。当前7条均保留，包括尚不能完全表达的记录。

## 标注规范

1. 以原始 query 为唯一证据，Span 使用 Python 字符索引的半开区间。节点 text 原样摘取；不得用制度常识补出未说出的动作、审批人或要求。
2. USER_REQUEST 表达当前用户动作。VERIFY_ASSERTION 仅拟用于核实命题或可行性，不能把一切问号或“怎么处理”都归入核实；本版 COV-009/V3U-006 的解释须单独审核。
3. DENY_REQUEST 表达用户拒绝某请求或排除回答范围。“不需要审批流程”在 COV-008 中拟标范围排除，不表示制度已豁免审批。
4. REPORTED_ASSERTION + REPORT_ASSERTION 表达转述。V3U-009 中“都说过了”不补写为“都批准了”；转述者与实际断言内容应区分，未知断言内容保留 missing_outputs。
5. ENTITY/CONDITION 保留对象和条件。实体节点的存在不表示其政策适用性已确定；条件只作用于有原文依据的对象，差旅与普通费用分别保留作用域。
6. REFERS_TO 只拟绑定可解释的先行事件。事件绑定不等于审批/财务复核目标已知。无唯一先行对象的“那个要求”保留缺失，不编造对象。
7. TARGETS 从用户动作指向对象。scope_id 和 node_ids 保持对应；边、节点及作用域 Span 都可回查原句。图无环是结构约束，不是语义正确保证。
8. missing_outputs 的 reason 以 ambiguous 或 contract_gap 区分原句歧义与契约表达能力不足。当前动作枚举缺少开放式流程、材料、人员及时间询问，因此这些请求保留缺口。本步骤不扩展枚举。
9. ParseStatus.PARTIAL 表示结构中明确存在缺失；review_status=pending_review 表示尚未获人工确认，两者互不替代。所有7条草案为 PARTIAL，不能因此计算“解析失败率”。
10. parser_id/parser_revision 均为 null，草案由 AI 助手协助编写；不是默认解析器的推理结果。历史 BGE 模型信息只描述原候选证据来源。

## 逐条审核

| 行 / case_id | 原问法 | 草案重点与待确认问题 |
| --- | --- | --- |
| 1 / COV-007 | 一般费用发生四十五天后才报销，需要补什么说明，部门和财务分别要做什么？ | 条件、说明、部门、财务分别保留；是否同意具体分工询问是动作契约缺口，不等同于核实？ |
| 2 / COV-008 | 只查普通费用发生多久后，财务可以原则上拒绝受理报销，不需要审批流程。 | 是否确认排除回答中的审批流程，而非制度免审批？拒收时间询问暂为动作缺口。 |
| 3 / COV-009 | 费用已经发生一百天，还能直接走普通报销吗，制度对这种超时怎么处理？ | 可行性拟标 VERIFY_ASSERTION；“这种超时”拟指向一百天事件。是否接受这两个判断？处理流程仍未表达。 |
| 4 / COV-010 | 因特殊原因赶不上差旅报销期限，应在什么时候向哪些人说明，普通费用的超时报销又由谁审批？ | 是否同意差旅与普通费用分开作用域，保留时间/人员/流程动作缺口？ |
| 5 / V3U-004 | 普通费用拖了四十天，这个现在怎么办？ | “这个”拟绑定逾期事件，但具体办理目标未知。是否接受事件级绑定？ |
| 6 / V3U-006 | 普通费用拖了四十天，那个要求还要吗？ | 拟标 VERIFY_ASSERTION，但“那个要求”对象缺失。是否接受该动作判断？ |
| 7 / V3U-009 | 普通费用交晚了五十天，部门和财务都说过了，按哪个流程？ | 拟保留转述，未知“过了”的具体含义与流程；是否同意不解释成审批已通过？ |

可按 case_id 回复“接受草案”或给出具体修正及原文依据。缺口可接受为缺口，不要求强行补齐。即使接受某条，也不改变历史门控标签；需要后续显式记录 reviewer、reviewed_at、accepted_semantic_label 和新版本哈希，才形成语义审核结果。当前文件全部保持 pending_review。

## 文件和复核命令

- records.jsonl：7条契约对象，行号由 review.json 映射。
- review.json：7条待审核记录，accepted_semantic_label 均为 null。
- source-excerpts.json：原始来源摘录及历史证据元数据。
- validation.json：本次实际结构检查输出，仅表示契约有效性。
- manifest.json：版本、数量、当前代码 HEAD、环境、原始来源及交付文件哈希。

标准 PowerShell 命令（项目虚拟环境可用时）：

~~~powershell
Set-Location D:\Ai_agent_program\demo1
.\.venv\Scripts\python.exe -X utf8 -m scripts.check_semantic_parse_records .\docs\gate_v3\semantic-dev-v1\records.jsonl
~~~

本机虚拟环境 Python 入口此前不可用，本次使用现有 Python 3.12 运行时、标准库和仓库契约执行：

~~~powershell
& 'C:\Users\Grey\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -X utf8 -m scripts.check_semantic_parse_records .\docs\gate_v3\semantic-dev-v1\records.jsonl
~~~

预期 status=passed、record_count=7、valid_record_count=7、invalid_record_count=0、records_with_missing_outputs=7。passed 仅说明格式、Span 和引用关系符合契约，不表示语义标注已经正确或确认。

## 回滚与下一阶段边界

本步骤只新增此目录，未接入模型、运行时或检索选择器。撤回本步骤仅需在审查后移除此新目录，不涉及旧报告或数据库。最大的使用风险是把待审核草案或历史确认标签误用为语义金标，或据此宣称独立测试通过。审核完成后再讨论动作契约缺口与解析器实验；本步不自动开始后续阶段。
