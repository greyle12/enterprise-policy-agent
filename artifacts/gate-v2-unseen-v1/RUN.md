# v2冻结后的新增问法：标注确认阶段

按ZIP相对路径添加新增文件；同名文件存在时先比较，不覆盖不同内容。
未修改v2/v1、规则、旧标签、旧候选或生产默认配置。未提交或推送。

```powershell
python -X utf8 -m scripts.verify_gate_v2_unseen
```

预期passed=true, n=8, status=pending_user_confirmation, inference_executed=false。
冻结哈希按字节比较，换行变化也会阻断，不要重算哈希绕过差异。
REVIEW.md给出完整问法，review-draft.json附标签理由与制度原文。

请确认全部8条或指出修正编号。用户确认前不运行新查询；确认后另存确认记录并统一真实评测。
原草案不覆盖，确认前语义修正须保留版本；评测后不改标签/规则/样本来追求指标。
后续评测固定20/40、K=5、前4位与最多1条补充；新问法必须生成新候选。
比较raw/frozen/v1/v2，报告4条控制和4条需要补充查询、实际触发机会、全部回归及替换。
Recall分母为每查询所有已标注正相关Chunk，宏平均；MRR首相关倒数；未标注不视作人工判负。
无答案/无权限样本本批没有，不推断相关能力。计时与真实推理、回放选择分别声明。

这是作者知晓门控弱点后生成的小规模定向集，虽然问法未参与v2设计，也不是独立第三方盲测。
当前没有新质量成绩。v2对CHG-009的问题仍保留，不增加专项例外。
建议提交：test(retrieval): freeze v2 and draft unseen clause challenges
