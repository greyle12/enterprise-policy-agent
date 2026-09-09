# 门控 v3 设计提案：结构化需求与确定性决策

状态：design_only。已核对20条契约确认记录，冻结校验通过。
本文件是新增设计提案，不覆盖旧契约；当前没有v3代码或新评测成绩。

## 问题与选择

v2的need/veto丢失了主语、对象和作用域，跨句条件无法继承，“不用复核”的疑问被当作排除。
v3将解析与决策分离。第一实施步骤采用有限、可解释的确定性解析器和四值输出，
只识别有明确语言依据的模式，不声称通用中文理解。语义覆盖不足输出UNRESOLVED。
本轮不引入LLM判别器，不要求新模型调用；后续若比较模型解析器必须另立冻结实验。
这是检索证据选择，不改变审批要求或权限。

## 结构化对象（拟议接口，尚未实现）

parse_query(query: str) -> QueryIntent
决定函数 decide_intent(intent: QueryIntent, relation: RuleRelation) -> GateDecision
实验选择函数 select_v3(query, baseline, candidates, allowed, links, *, window) -> SelectionResult

QueryIntent:
- clauses: 子句文本与原查询[start,end)字符偏移；保留标点位置，不删除否定词再重建文本。
- requests: topic, act, referent, source_spans。
  topic限于city_category/lodging_amount/room_sharing/expense_deadline/reason_statement/approval/finance_review。
  act区分ASK_REQUIREMENT、ASK_EXEMPTION、EXCLUDE_TOPIC；不把问句否定当成EXCLUDE_TOPIC。
- conditions: key, value, referent, status, source_spans。
  status区分GIVEN、QUESTIONED、UNKNOWN、CONFLICTED。GIVEN仅指用户给定问答前提，不代表数据库事实。
- ambiguities: 不能可靠绑定的指代、冲突、未知作用域及其证据位置。

RuleRelation使用已冻结的两条规则及原文来源，不生成新关系。
GateDecision: decision(ALLOW/DENY/UNRESOLVED/NOT_APPLICABLE), reason_code, evidence_spans, unresolved_reasons。
不输出未经校准的概率“置信度”。每个确定决策需可追溯的原文证据。
门控输入不包含标签、案例编号、预期答案、检索成绩。

## 解析边界

1. 同一实体的明确条件可跨句继承；多个目的地或指代不唯一时不猜测。
2. 问“能否免除”创建ASK_EXEMPTION，明确“不讨论”才创建EXCLUDE_TOPIC。
3. 双重否定必须保留完整作用范围；无法判断时记录ambiguity。
4. “只问”是查询范围声明，不能用另一无关子句出现关键词来覆盖。
5. 多个有效需求分别绑定对象；任何相关未排除需求可支持ALLOW。
6. 冲突和未知只在影响当前规则判断时阻断，不能因不相关话题含歧义全部拒绝。
7. 输入空白、未知规则、非法结构作为错误处理，不伪装成ALLOW。

## 决策优先级

- 先判断是否属于给定补充关系；目标是主要查询证据则NOT_APPLICABLE。
- 影响本关系的条件冲突、指代或范围未解 -> UNRESOLVED。
- 存在绑定正确的有效需求，且需要目标证据（包括豁免询问）-> ALLOW。
- 仅剩明确排除目标话题，或金额查询所需城市类别已给定且无人询问其真伪 -> DENY。
- 信息不足 -> UNRESOLVED，而不是默认猜测DENY或声称识别成功。

## 选择策略（仅实验提案）

在权限、候选窗口、锚点和目标缺失检查全部通过后，处理冻结选择器的唯一提案：
- ALLOW：接受该提案。
- DENY：恢复原始Top5。
- UNRESOLVED：保留冻结提案，但记录fallback_applied=true；不计作正确ALLOW或成功拒绝。
- NOT_APPLICABLE：不执行本补充关系，保持原始Top5；不删除已有主要证据，不影响原检索。

UNRESOLVED保留提案旨在避免语义不确定造成新增漏召回，但可能保留多余证据，必须单独报告。
运行错误不是语义不确定：实验应记录错误并阻断成功验收，不静默回退伪造成绩。
前4位保留、最多补1条、最终K=5、20/40显式上限和授权候选约束继续不变。

## 评测与验收（不是实测成绩）

决策层：保留20条已确认数据，19条二分类+1条不适用；新增UNRESOLVED样例经用户确认后固定。
报告四值混淆矩阵、全部样本N、确定决策覆盖率、确定决策正确率、总体正确率、误拒绝/漏拦。
总体正确率不能排除UNRESOLVED来提高；选择回退行为与语义正确率分开计。
当前没有不确定标签，因此不能声称已验证弃权能力。
检索层：使用既有原始候选同时比较raw/frozen/v1/v2/v3，报告20/40全部结果与控制组。
未触发提案不算门控成功；UNRESOLVED回退引起的多余补充也保留。仅报告选择计时，不当作新推理。

工程验收：冻结校验通过；所有决策证据span可还原原文；无越权候选；前4位/最多1条不变；
已知歧义样例输出UNRESOLVED；坏输入明确失败；所有回归入报告。禁止用预期分数硬编码答案。
质量验收不预设漂亮数字：若存在必要证据误拒绝或明显泛化缺口，继续保持实验功能。

## 分步实施

第一步：只实现结构化数据对象、保守解析、四值决策与合成边界测试；不接入选择器。
第二步：冻结新语义复核样例后运行决策层评测，完整保留UNRESOLVED与错误。
第三步：单独接入实验回放，比较真实候选上的质量和回退成本。
任何一步不修改FastAPI、原门控、冻结规则和标签，不自动推送或部署。

拟新增文件：scripts/gate_v3_intent.py、tests/evaluation/test_gate_v3_intent.py；
后续另增scripts/replay_gate_v3.py与独立报告，不修改旧脚本的冻结指纹。

## 风险

结构化并不自动解决语言理解；确定性解析仍可能过拟合表达。
跨句继承可能错误绑定实体；未知默认保留提案会牺牲精简度。
已观察数据只能用于开发回归；真正泛化需冻结实现后另外标注未见集。
本设计尚未实施，不能推断检索提升。
