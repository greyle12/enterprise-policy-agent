# 本步骤：冻结与标注确认

将新增 scripts 与 artifacts 文件按相对路径添加；已存在同名文件时先比较，勿覆盖不同内容。
不包含旧代码、模型缓存、密钥，不改生产窗口。依赖前一步门控代码包。

PowerShell 项目根目录：

```powershell
python -X utf8 -m scripts.verify_gate_challenge_freeze
```

预期 passed=true, case_count=12, status=pending_user_confirmation, evaluation_authorized=false。
哈希按实际字节记录，换行转换也会使校验失败；遇到漂移应比较文件，不重算指纹绕过检查。

请阅读 REVIEW.md；review-draft.json 包含标注理由及全部引用原文。助手已核对原文，尚无用户逐条确认。
请确认全部12条或列出需要修正的编号。确认记录将另存，草案不可覆写。

确认后的单独评测步骤：
- 固定同一语料、权限、模型和新查询，为20/40分别生成新真实候选；不能借用旧查询候选。
- 比较无规则、冻结规则、冻结明确排除门控，最终K=5、保留前4位、最多补1条。
- 全量宏平均Recall/MRR及逐条排名，分开报告5条无需补充控制和7条需要补充查询。
- 报告门控触发机会数；若没有补充提案，不计作成功拦截。
- 标签与候选冻结后禁止因结果修改；失败案例完整保留。后续改门控须建新版本。

这是定向挑战集：问法未参与既有门控设计，但作者知道门控，不是第三方盲测。
新标签不是独立人工标注。当前未运行新查询门控和推理，没有质量成绩。
Git建议只加入本包文件，提交信息：test(retrieval): freeze gate and draft targeted challenge set
