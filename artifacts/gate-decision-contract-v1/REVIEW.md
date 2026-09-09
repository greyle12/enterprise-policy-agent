# 人工复核表

ALLOW=允许关联证据；DENY=当前范围无需关联证据。完整原文与理由见review.json。

|编号|类别|问法|草案决策|
|---|---|---|---|
|CHG-001|double_negation|不是不需要审批流程：这笔普通费用拖了四十天，请说清部门和财务各要做什么。|ALLOW|
|CHG-002|local_topic_exclusion_other_need|我不问同住安排，只想知道普通员工去南京住酒店每晚限额。|ALLOW|
|CHG-003|topic_exclusion|不要展开审批流程，只告诉我普通费用拖到多少天后可能被拒收。|DENY|
|CHG-004|condition_question|苏州到底算不算一类城市？普通员工每晚住宿标准对应多少钱？|ALLOW|
|CHG-006|known_condition|已确认目的地属于三类城市，部门负责人住一晚酒店的上限是多少？|DENY|
|CHG-007|multiple_needs|普通员工到青岛出差，每晚能报多少住宿费？两名同性同事还必须合住吗？|ALLOW|
|CHG-008|multiple_needs|普通费用拖到一百天会不会被拒收？如果属于特殊情况继续办，部门审批和财务复核分别有什么要求？|ALLOW|
|CHG-009|known_condition|普通员工与部门负责人在已确定的二类城市出差，各自每晚住宿上限是多少？|DENY|
|CHG-010|implicit_requirement|一笔普通费用放了五十天才想起来报，现在要经过哪些人才能继续往下办？|ALLOW|
|CHG-011|exemption_question|普通费用过了四十天才交，我写好原因之后是不是就能直接报了？|ALLOW|
|CHG-012|topic_exclusion|普通费用晚交了四十天，我只想知道要不要写明迟交原因，其他环节先不讲。|DENY|
|V2U-001|cross_clause_known_condition|目的地的类别已经核实，是二类。出差人是普通员工。请只给每晚住宿金额上限。|DENY|
|V2U-002|unknown_condition|此次目的地是厦门，但类别还没查。出差人是部门负责人。每晚住宿费用最高多少？|ALLOW|
|V2U-003|exemption_question_reference|普通费用交晚了五十天，部门负责人已经批过。这是否意味着不用再交财务复核？|ALLOW|
|V2U-004|topic_exclusion_reference|这里说的是两名同性普通员工合住标准间。这种安排能由公司强制执行吗？只回答是否强制。|DENY|
|V2U-005|ellipsis|普通员工，宁波，一晚。酒店最多报多少？|ALLOW|
|V2U-006|topic_exclusion|普通费用，四十二天后交。只问原因说明：要写吗？|DENY|
|V2U-007|negated_exemption|不是要免掉财务复核，也不想略过部门审批。普通费用迟交六十天，完整要求是什么？|ALLOW|
|V2U-008|topic_exclusion|不是问报销能否通过，也不是问由谁审批；只问费用超过多少天可能不予受理。|DENY|
|CHG-005|primary_evidence|一类城市包括哪些地方？请只列出制度里的城市名单。|NOT_APPLICABLE|