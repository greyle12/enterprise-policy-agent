# 语义开发集逐条审核结果

版本 semantic-dev-v1-confirmed-1。七条记录均由当前用户逐条回复“接受”；确认的是原草案，语义记录不作修改。原目录 semantic-dev-v1 保持原样。

| 审核顺序 | case_id | 结果 |
| --- | --- | --- |
| 1 | COV-008 | 接受原草案 |
| 2 | COV-009 | 接受原草案 |
| 3 | V3U-009 | 接受原草案 |
| 4 | COV-007 | 接受原草案 |
| 5 | COV-010 | 接受原草案 |
| 6 | V3U-004 | 接受原草案 |
| 7 | V3U-006 | 接受原草案 |

review.json 将每个接受结果绑定到 records.jsonl 的文件哈希和行号；confirmation.json 保存原草案文件哈希、审核顺序和范围。无法获得逐条消息的精确时间戳，reviewed_at 保留 null；recorded_at 仅为记录落盘时间，不冒充用户接受时间。逐条澄清见 review.json 的 acceptance.clarification。

七条 ParseStatus 仍为 PARTIAL：用户接受了保留歧义和 contract_gap 的标注，不意味着这些缺口已经解决。accepted_count=7 是人工审核数量，不是模型正确数或语义准确率。此集仍为 AI 辅助、用户确认的开发集，不是独立留出集，不是独立双人标注，numeric_resume_ready=false。

## 来源核验说明

核验发现原草案 manifest.json 顶层 review_sha256 误指向历史 gate-v3 review.json 的哈希。原清单 files 数组中的草案 review.json 哈希正确。本版本保留旧文件不改写，重新按完整文件路径绑定父版本及本版本哈希，避免同名文件混淆。父草案 review.json 实际 SHA-256 为 32bca1e1ca02f7e83dc319a44f8d371632b1ab0f22b6145a7822122918d1101b。

历史检索标签和结果继续引用 ../semantic-dev-v1/source-excerpts.json；不因本次审核而更改。manifest.json 记录父版本、当前代码 HEAD、环境和本次交付文件哈希；validation.json 只表示结构检查结果。

## 复核

~~~powershell
Set-Location D:\Ai_agent_program\demo1
& 'C:\Users\Grey\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -X utf8 -m scripts.check_semantic_parse_records .\docs\gate_v3\semantic-dev-v1-confirmed\records.jsonl
~~~

预期 passed，record_count=7，records_with_missing_outputs=7，error_count=0。检索及语义模型均未运行，模型和 revision 不适用。

本目录是增量交付，完整来源依赖已提交的 semantic-dev-v1。下一阶段须另行决定如何表达开放式流程、材料、时间及人员询问，再规划解析器实验。本步不接生产运行时、不切换 Gate、不自动提交或推送。撤回时仅涉及本新增目录，原草案不受影响。
