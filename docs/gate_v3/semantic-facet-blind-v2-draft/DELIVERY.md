# 本步骤交付与实际检查

解决问题：提供推理前可人工审阅、可校验的第二批语义候选数据。问句与待确认标签分文件保存，避免未来导出请求时夹带标签。当前状态 pending_review，没有模型调用、语义成绩或生产接入。

新增文件：本目录 questions.jsonl、review.json、REVIEW.md、validation.json、DELIVERY.md，以及 scripts/check_semantic_facet_blind_draft.py 和 tests/evaluation/test_semantic_facet_blind_draft.py。只新增本步骤文件，历史修改未动。

实际检查：18 条、六类各三条、17 个 Facet；SFB-009 和 SFB-012 的拟议标签为空数组。ID、标签对齐、枚举、原文 Span、无标签问句字段检查通过。扫描 docs/gate_v3 下 JSON/JSONL 中的 query 字段，包含 89 条记录（包含不同版本中的重复记录，不表示 89 个独立样本），路径与逐文件哈希见 validation.json。规范化精确重复为 0。

字符近似阈值预置为 0.65，仅用于人工审阅，不作为成绩或语义去重证明。三对提示均为本批内部：SFB-005/004（0.6800）、SFB-006/005（0.7018）、SFB-018/017（0.7547）。这些对照用于观察同一主题下唯一指代、歧义指代和多维请求的差异，全部保留。指定历史语料范围内无超过该阈值的提示；这不能排除所有语义重复。

Ruff format/check 通过；pytest 8 passed，0 warning。测试覆盖 Span 错位、标签泄漏、未确认边界、重复 ID、标签错位、非法 binding 和规范化重复提示。无生产代码改动，因此回归主要风险是草稿标签的人工判断及字符近似方法漏报；独立测试集标志保持 false。HEAD 为 170190ec3f1819386afd2fa439f8c54ce1f43d5e；依赖环境与文件哈希保存在 validation.json。

PowerShell 复验命令（本机 .venv Python 入口不可用，沿用已验证的 Python 3.12 运行时和项目 site-packages）：

```powershell
Set-Location D:\Ai_agent_program\demo1
$env:PYTHONPATH = (Resolve-Path .\.venv\Lib\site-packages).Path
$taskPython = 'C:\Users\Grey\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $taskPython -X utf8 -m scripts.check_semantic_facet_blind_draft
& $taskPython -X utf8 -m pytest -q -p no:cacheprovider tests/evaluation/test_semantic_facet_blind_draft.py
.\.venv\Scripts\ruff.exe check scripts/check_semantic_facet_blind_draft.py tests/evaluation/test_semantic_facet_blind_draft.py
```

预期输出：status=passed、sample_count=18、facet_instance_count=17、精确重复 0，以及 8 passed。历史语料目录后续变化时，扫描记录数和近似提示可能改变，应保留原报告再复验。

下一步：用户审阅 REVIEW.md 的具体问句、拟议标签及理由；确认后将这一版本冻结，再检查现有适配器对空标签真值的支持，导出请求并在具体外发授权后推理。真实结果按六类分别分析；运行失败与语义不匹配分别记录，原样保留。不得把本次数据检查通过作为模型效果。

Git 建议提交信息：test(evaluation): prepare second semantic facet review set。待用户要求提交时仅暂存上述七个文件，先展示暂存清单，再提交。此次没有执行暂存、提交或推送。
