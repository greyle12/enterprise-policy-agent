# 检索评测证据

模式：`bge`；状态：`completed`。
开发集与未人工复核探索集分别报告；不代表生产质量，也不代表回答准确率。
offline 的 Vector/Reranker 是词法替身，BM25 是实际算法。禁止把替身差值写成 BGE 提升。

代码 HEAD：`2779d0d555af045167526fd92891e96e6980dfce`；代码内容指纹：`4f2cd32b4d5ca2ae528d788c8008aaa7fa9c4cab17f087f2589a1cbddf7c1f7a`。

Recall@5 = Top-5 命中的已标注相关 Chunk 数 / 该查询全部已标注相关 Chunk 数，按查询宏平均。
Grade 1/2/3 均为正相关；MRR@5 为首个正相关项排名倒数的宏平均，未命中记 0。
运行错误保留样本并记 0；无答案/无权限/无标注不适用本正例排名套件，当前各为 0 条。
非法或不可见标签在推理前报错，不静默删除。未标注的返回项只按未命中计算，不等于人工判为无关。

每路精确检索最多 20 条，RRF 常数 60，去重后最多 20 条参与重排，最终 K=5。
两种融合方案使用相同候选窗口；所有方案使用同一语料、增强检索文本和检索前权限范围。
耗时仅为诊断：含查询向量化（BM25 除外），排除模型加载/建库，固定顺序、单次采样；不用于延迟简历结论。

## legacy_dev

仓库原有标注。文档称单一开发者标注；未发现独立人工复核记录。长期用于 CI/候选窗口实验，属于开发集。

| 方案 | N | Recall@5 | MRR@5 | Recall 差值（百分点） | MRR 差值（百分点） | 错误 |
|---|---:|---:|---:|---:|---:|---:|
| vector | 20 | 90.00% | 0.8292 | +0.00 | +0.00 | 0 |
| bm25 | 20 | 90.00% | 0.9250 | +0.00 | +9.58 | 0 |
| hybrid | 20 | 92.50% | 0.9250 | +2.50 | +9.58 | 0 |
| reranked | 20 | 92.50% | 0.9500 | +2.50 | +12.08 | 0 |

全部非满召回、首相关项非第一名或运行错误（未挑选）：

- RET-001 / vector：普通员工去北京出差，每晚住宿费上限是多少？ Recall@5=0.500，首相关排名=1，缺失：TRAVEL_POLICY_001__v1_0__article_007；错误：None。
- RET-001 / bm25：普通员工去北京出差，每晚住宿费上限是多少？ Recall@5=0.500，首相关排名=1，缺失：TRAVEL_POLICY_001__v1_0__article_007；错误：None。
- RET-001 / hybrid：普通员工去北京出差，每晚住宿费上限是多少？ Recall@5=0.500，首相关排名=1，缺失：TRAVEL_POLICY_001__v1_0__article_007；错误：None。
- RET-001 / reranked：普通员工去北京出差，每晚住宿费上限是多少？ Recall@5=0.500，首相关排名=1，缺失：TRAVEL_POLICY_001__v1_0__article_007；错误：None。
- RET-002 / vector：部门负责人在上海出差的酒店住宿标准是多少？ Recall@5=1.000，首相关排名=3，缺失：无；错误：None。
- RET-002 / reranked：部门负责人在上海出差的酒店住宿标准是多少？ Recall@5=0.500，首相关排名=1，缺失：TRAVEL_POLICY_001__v1_0__article_007；错误：None。
- RET-006 / vector：采购预算八万元需要谁审批，至少要取得几家供应商报价？ Recall@5=1.000，首相关排名=4，缺失：无；错误：None。
- RET-006 / bm25：采购预算八万元需要谁审批，至少要取得几家供应商报价？ Recall@5=0.000，首相关排名=None，缺失：PROCUREMENT_POLICY_001__v1_0__article_013, PROCUREMENT_POLICY_001__v1_0__article_018；错误：None。
- RET-006 / hybrid：采购预算八万元需要谁审批，至少要取得几家供应商报价？ Recall@5=0.500，首相关排名=2，缺失：PROCUREMENT_POLICY_001__v1_0__article_013；错误：None。
- RET-006 / reranked：采购预算八万元需要谁审批，至少要取得几家供应商报价？ Recall@5=1.000，首相关排名=2，缺失：无；错误：None。
- RET-007 / reranked：采购金额三万元，需要至少找几家合格供应商报价？ Recall@5=1.000，首相关排名=2，缺失：无；错误：None。
- RET-008 / vector：采购总金额二十五万元，原则上应该采用什么采购方式？ Recall@5=0.000，首相关排名=None，缺失：PROCUREMENT_POLICY_001__v1_0__article_019；错误：None。
- RET-008 / hybrid：采购总金额二十五万元，原则上应该采用什么采购方式？ Recall@5=1.000，首相关排名=2，缺失：无；错误：None。
- RET-014 / vector：费用发生超过三十天才报销，需要说明和履行什么审批？ Recall@5=0.500，首相关排名=2，缺失：EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025；错误：None。
- RET-014 / bm25：费用发生超过三十天才报销，需要说明和履行什么审批？ Recall@5=0.500，首相关排名=1，缺失：EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025；错误：None。
- RET-014 / hybrid：费用发生超过三十天才报销，需要说明和履行什么审批？ Recall@5=0.500，首相关排名=1，缺失：EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025；错误：None。
- RET-014 / reranked：费用发生超过三十天才报销，需要说明和履行什么审批？ Recall@5=0.500，首相关排名=1，缺失：EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025；错误：None。
- RET-016 / vector：API 密钥、数据库密码和加密私钥属于什么数据等级？ Recall@5=1.000，首相关排名=2，缺失：无；错误：None。
- RET-016 / hybrid：API 密钥、数据库密码和加密私钥属于什么数据等级？ Recall@5=1.000，首相关排名=2，缺失：无；错误：None。
- RET-020 / bm25：请假超过五个工作日需要经过哪些审批人？ Recall@5=1.000，首相关排名=2，缺失：无；错误：None。

## exploratory_unreviewed

助手依据仓库原条款生成并核对引用存在性，未经过人工复核；仅供探索，不与原 20 条合并或宣称独立测试集。

| 方案 | N | Recall@5 | MRR@5 | Recall 差值（百分点） | MRR 差值（百分点） | 错误 |
|---|---:|---:|---:|---:|---:|---:|
| vector | 6 | 80.56% | 0.8333 | +0.00 | +0.00 | 0 |
| bm25 | 6 | 69.44% | 0.5889 | -11.11 | -24.44 | 0 |
| hybrid | 6 | 61.11% | 0.5833 | -19.44 | -25.00 | 0 |
| reranked | 6 | 86.11% | 0.9167 | +5.56 | +8.33 | 0 |

全部非满召回、首相关项非第一名或运行错误（未挑选）：

- RET-021 / vector：我不是领导，去北京出差住一晚酒店最多能报多少？ Recall@5=0.500，首相关排名=1，缺失：TRAVEL_POLICY_001__v1_0__article_007；错误：None。
- RET-021 / bm25：我不是领导，去北京出差住一晚酒店最多能报多少？ Recall@5=0.500，首相关排名=1，缺失：TRAVEL_POLICY_001__v1_0__article_008；错误：None。
- RET-021 / hybrid：我不是领导，去北京出差住一晚酒店最多能报多少？ Recall@5=0.000，首相关排名=None，缺失：TRAVEL_POLICY_001__v1_0__article_007, TRAVEL_POLICY_001__v1_0__article_008；错误：None。
- RET-021 / reranked：我不是领导，去北京出差住一晚酒店最多能报多少？ Recall@5=0.500，首相关排名=2，缺失：TRAVEL_POLICY_001__v1_0__article_007；错误：None。
- RET-022 / bm25：火车票找不到了，财务会让我补啥才能报出差的钱？ Recall@5=0.000，首相关排名=None，缺失：TRAVEL_POLICY_001__v1_0__article_018；错误：None。
- RET-022 / hybrid：火车票找不到了，财务会让我补啥才能报出差的钱？ Recall@5=0.000，首相关排名=None，缺失：TRAVEL_POLICY_001__v1_0__article_018；错误：None。
- RET-023 / vector：采购管理制度第十八条对 8 万元采购的有效报价数量和书面记录有什么要求？ Recall@5=1.000，首相关排名=2，缺失：无；错误：None。
- RET-024 / vector：出差结束三十多天和普通费用发生三十多天才报销，各自会怎么处理？ Recall@5=0.333，首相关排名=2，缺失：EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025, EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027；错误：None。
- RET-024 / bm25：出差结束三十多天和普通费用发生三十多天才报销，各自会怎么处理？ Recall@5=0.667，首相关排名=3，缺失：EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025；错误：None。
- RET-024 / hybrid：出差结束三十多天和普通费用发生三十多天才报销，各自会怎么处理？ Recall@5=0.667，首相关排名=2，缺失：EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025；错误：None。
- RET-024 / reranked：出差结束三十多天和普通费用发生三十多天才报销，各自会怎么处理？ Recall@5=0.667，首相关排名=1，缺失：EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025；错误：None。
- RET-025 / bm25：我今天不在，能让同事用我的公司登录名帮我操作一下吗？ Recall@5=1.000，首相关排名=5，缺失：无；错误：None。
