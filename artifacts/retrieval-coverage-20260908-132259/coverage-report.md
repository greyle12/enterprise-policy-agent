# 补充证据覆盖消融实验

模式：真实 BGE 已保存排名的确定性后处理重放；本次无模型推理。
规则在观察失败案例后设计，未经独立人工复核；结果仅为开发探索，不新增简历成绩。
前四名保持不变；仅第五位可补入同制度同版本、已授权 RRF Top-20 中的关联条款。
Recall@5 按查询宏平均，分母为全部已标注正相关 Chunk；Grade 1/2/3 均为正相关。
MRR@5 为首个正相关排名倒数的宏平均；另报告分级 nDCG@5 以检测排序退化。
无答案、无权限、无标注样本不在原正例套件内；缺标签或输入错误直接阻止实验。未标注不等于无关。
权限/有效期沿用历史快照，不能作为当前权限校验或在线端到端验收。

## legacy_dev

仓库原有标注。文档称单一开发者标注；未发现独立人工复核记录。长期用于 CI/候选窗口实验，属于开发集。

|方案|N|Recall@5|MRR@5|nDCG@5|
|---|---:|---:|---:|---:|
|vector|20|90.00%|0.8292|0.8408|
|bm25|20|90.00%|0.9250|0.8962|
|hybrid|20|92.50%|0.9250|0.9055|
|reranked|20|92.50%|0.9500|0.9196|
|coverage|20|100.00%|0.9500|0.9392|

改变 4 条；Recall 提升 3 条；任一指标退化：[]。

全部改动或未满召回/首相关非第一案例：

- RET-001：普通员工去北京出差，每晚住宿费上限是多少？；Recall 0.500 → 1.000；补入正例 ['TRAVEL_POLICY_001__v1_0__article_007']；移出正例 []；仍缺 []；事件 [{"rule_id": "city_category_for_lodging_table", "anchor": "TRAVEL_POLICY_001__v1_0__article_008", "target": "TRAVEL_POLICY_001__v1_0__article_007", "action": "supplemented", "evicted": ["TRAVEL_POLICY_001__v1_0__article_006"]}]
- RET-002：部门负责人在上海出差的酒店住宿标准是多少？；Recall 0.500 → 1.000；补入正例 ['TRAVEL_POLICY_001__v1_0__article_007']；移出正例 []；仍缺 []；事件 [{"rule_id": "city_category_for_lodging_table", "anchor": "TRAVEL_POLICY_001__v1_0__article_009", "target": "TRAVEL_POLICY_001__v1_0__article_007", "action": "supplemented", "evicted": ["TRAVEL_POLICY_001__v1_0__article_005"]}]
- RET-003：普通员工去北京住酒店的标准是多少，住宿费超标又要怎么处理？；Recall 1.000 → 1.000；补入正例 []；移出正例 []；仍缺 []；事件 [{"rule_id": "city_category_for_lodging_table", "anchor": "TRAVEL_POLICY_001__v1_0__article_008", "target": "TRAVEL_POLICY_001__v1_0__article_007", "action": "supplemented", "evicted": ["TRAVEL_POLICY_001__v1_0__article_006"]}]
- RET-006：采购预算八万元需要谁审批，至少要取得几家供应商报价？；Recall 1.000 → 1.000；补入正例 []；移出正例 []；仍缺 []；事件 []
- RET-007：采购金额三万元，需要至少找几家合格供应商报价？；Recall 1.000 → 1.000；补入正例 []；移出正例 []；仍缺 []；事件 []
- RET-014：费用发生超过三十天才报销，需要说明和履行什么审批？；Recall 0.500 → 1.000；补入正例 ['EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025']；移出正例 []；仍缺 []；事件 [{"rule_id": "finance_review_for_overdue_expense", "anchor": "EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027", "target": "EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025", "action": "supplemented", "evicted": ["EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_022"]}]

## exploratory_unreviewed

助手依据仓库原条款生成并核对引用存在性，未经过人工复核；仅供探索，不与原 20 条合并或宣称独立测试集。

|方案|N|Recall@5|MRR@5|nDCG@5|
|---|---:|---:|---:|---:|
|vector|6|80.56%|0.8333|0.7800|
|bm25|6|69.44%|0.5889|0.5176|
|hybrid|6|61.11%|0.5833|0.6021|
|reranked|6|86.11%|0.9167|0.8968|
|coverage|6|94.44%|0.9167|0.9185|

改变 1 条；Recall 提升 1 条；任一指标退化：[]。

全部改动或未满召回/首相关非第一案例：

- RET-021：我不是领导，去北京出差住一晚酒店最多能报多少？；Recall 0.500 → 1.000；补入正例 ['TRAVEL_POLICY_001__v1_0__article_007']；移出正例 []；仍缺 []；事件 [{"rule_id": "city_category_for_lodging_table", "anchor": "TRAVEL_POLICY_001__v1_0__article_009", "target": "TRAVEL_POLICY_001__v1_0__article_007", "action": "supplemented", "evicted": ["TRAVEL_POLICY_001__v1_0__article_014"]}]
- RET-024：出差结束三十多天和普通费用发生三十多天才报销，各自会怎么处理？；Recall 0.667 → 0.667；补入正例 []；移出正例 []；仍缺 ['EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025']；事件 [{"rule_id": "finance_review_for_overdue_expense", "anchor": "EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027", "target": "EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025", "action": "outside_authorized_candidate_window"}]

