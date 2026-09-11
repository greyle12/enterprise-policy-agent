# 门控 v3 决策层复核草案

状态：`user_confirmed`。本目录是 v3 第 2 步的独立复核集；确认前版本仍保留在 `review-draft.json`。

## 复核范围

本批 9 条问法只标注“当前查询是否足以对指定冻结关系作出确定判断”。
候选条款 ID 仅用于核对制度来源，不是检索相关性标注；没有新增向量、BM25、RRF 或
Reranker 评测，也没有修改 v1/v2 标签。

所有草案的建议值都是 `UNRESOLVED`，原因分别覆盖：

- 无前文的指代；
- 跨句指代没有唯一绑定对象；
- 城市类别已知/未知冲突；
- 只有未知条件但没有明确目标请求；
- 省略动作或多个办理流程无法唯一确定。

样本由助手在 v3 Step 1 冻结后编写，并逐条与现有制度条款核对；尚无独立第二标注者或用户确认。
用户已逐条确认 `proposed_decision` 保持 `UNRESOLVED`；确认记录和哈希锁见
`user-confirmation.json` 与 `confirmed-lock.json`。

## 确认边界

本次确认只授权本批决策层评测，不授权：

- 修改 v1/v2 冻结规则、20 条确认契约或旧评测报告；
- 接入原选择器、FastAPI 或生产默认配置；
- 运行真实模型推理或检索回放；
- 把 `UNRESOLVED` 当作正确的 `ALLOW` 或 `DENY`。

草案已固定为 `review.json`，四值混淆矩阵报告见
`decision-report-confirmed/four-value-report.json`；后续检索回放仍需另立步骤。
