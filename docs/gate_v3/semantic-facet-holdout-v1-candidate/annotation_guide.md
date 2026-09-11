# Semantic Facet Holdout v1：双人标注说明

本目录是新的留出集候选，不是已确认的独立测试集。问题文本由 AI 辅助起草，必须由两名标注者分别完成，不能参考模型输出；只有两份标注完成并经过冲突裁决后，才可以进入冻结步骤。

## 标注对象

只标注用户当前真正提出的信息请求维度，不根据背景事实、引用他人的问题、可行性判断或模型可能的回答来补标签。每个维度最多出现一次，并记录它与目标对象是否能从本句中唯一回查。

允许的 Facet：

- `PROCEDURE`：询问怎么办、处理步骤、办理环节或提交方式。
- `MATERIAL`：询问需要准备、提交或补充的材料、凭证、附件或说明。
- `DEADLINE`：询问时间点、期限、天数阈值、保留时长或逾期后的时间条件。
- `RESPONSIBLE_ROLE`：询问由哪个部门、岗位、人员或审批主体负责、签字或复核。
- `PROCESS_CHOICE`：在两个或多个办理流程、渠道或路径之间询问应选哪一个。

绑定状态：

- `BOUND`：请求维度和目标对象均能由当前原文唯一确定。
- `UNBOUND`：请求维度明确，但目标对象、候选流程或指代内容不唯一。
- `MISSING`：当前没有安全可标注的该请求维度；不要为了凑标签而推断。

## 双人流程

1. 标注者 A 和 B 独立填写 `accepted_facets`，每项包含 `facet`、`binding`、`source_spans` 和简短理由。
2. 两人全部提交后，才允许查看对方结果并填写 `adjudication`。
3. 裁决必须保留分歧，不得因为模型结果而改写问题或标签；若无法形成稳定共识，应保留为 unresolved 并记录原因。
4. 标注完成前，`independent_test_set`、`independent_double_annotation` 和 `numeric_resume_ready` 必须保持 `false`。

## 当前边界

`questions.jsonl` 是唯一可发送给模型的文件，只有 `case_id` 和 `query` 两个字段。`annotations.template.json` 与本说明属于人类标注资料，不得与模型请求一起发送。当前步骤不调用外部 API、不生成 accepted sidecar、不运行语义模型，也不产生准确率。
