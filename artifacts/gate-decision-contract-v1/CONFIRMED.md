# 决策契约确认完成

用户于2026-09-09回复“确认”，确认CONTRACT.md与20条复核草案。
11 ALLOW、8 DENY、1 NOT_APPLICABLE；没有UNRESOLVED样本。
原草案的pending状态不覆写，最新确认由user-confirmation.json及confirmed-lock.json记录。
哈希确认是版本一致性检查，不是加密签名或第三方标注证明。

新增文件按相对路径添加，同名存在先比较勿覆盖。依赖上一步完整契约包。

```powershell
python -X utf8 -m scripts.verify_confirmed_gate_contract
```

预期passed=true、status=user_confirmed、failed_checks=[]；代码及标签有漂移则非零退出。
字节哈希含换行差异，不重算绕过。旧草案检查与新确认检查职责不同。
本步只做确认存档与一致性验证；Ruff通过，没有新门控、模型推理或新成绩。
确认配对并不改变已报告数值，也不将开发集变成独立测试集。

下一实施步骤需要单独确定v3方案，特别是UNRESOLVED的运行时处置；本确认不自动授权该实现。
可选明确范围：查询需求解析成结构化决策，再让现有选择器消费；新实现与v1/v2并列实验。
不改生产默认窗口、旧标签或冻结代码；不提交、推送。
建议提交：docs(retrieval): record confirmed gate decision contract
