# 15 条标注的用户确认记录

你已明确回复“同意这 15 条草案”。本包记录 COV-002～COV-016 的 34 条 AI 辅助标签为 `user_confirmed_ai_assisted`，并保留 COV-001 的现有记录。

这表示你确认了标签草案，不表示独立双人标注，也不声明已经逐一检查全部 199 个授权 Chunk。没有伪造 reviewer、reviewed_at 或 checked_all_authorized_chunks；没有运行新检索，没有新 Recall/MRR 成绩。

## 文件

- `artifacts/retrieval-coverage-review-v1/review.user-confirmed.json`：合并后的候选评测标签；COV-001 保留当前本地记录。
- `user-confirmation.json`：确认事件和边界。
- `user-confirmation-checks.json`：结构检查结果。
- `PATCH-MANIFEST.json`：文件指纹。

## PowerShell 安全应用

建议先把本 ZIP 解压到临时目录，确认本地 review.json 没有新增其它标注；不要直接把 ZIP 解压到项目根目录覆盖文件：

```powershell
$zip = Join-Path $env:USERPROFILE "Downloads\enterprise-policy-agent-15-labels-user-confirmed.zip"
$confirmDir = Join-Path $env:TEMP ("coverage-confirmed-" + [guid]::NewGuid().ToString("N"))
Expand-Archive -Path $zip -DestinationPath $confirmDir
$source = Join-Path $confirmDir "artifacts\retrieval-coverage-review-v1\review.user-confirmed.json"
$target = ".\artifacts\retrieval-coverage-review-v1\review.user-confirmed.json"
Copy-Item $target "$target.before-user-confirmation-$(Get-Date -Format 'yyyyMMdd-HHmmss').bak" -ErrorAction SilentlyContinue
Copy-Item $source $target -Force
```

如果目标文件不存在，先创建父目录；如果你已经修改过该文件，使用 Compare-Object 后手工合并，不要覆盖其它 case：

```powershell
Compare-Object `
  (Get-Content ".\artifacts\retrieval-coverage-review-v1\review.ai-draft.json") `
  (Get-Content $target)
```

完成后运行现有复核门禁。由于尚未填写完整人工声明，预期仍为 `blocked_pending_human_review`；这不是标签丢失：门禁有意区分“用户确认 AI 提案”和“完整人工核验”。

```powershell
python -X utf8 -m scripts.manage_retrieval_coverage_review validate `
  --review-dir ".\artifacts\retrieval-coverage-review-v1" `
  --report ".\artifacts\retrieval-coverage-review-v1\validation-user-confirmed.json"
```

若要继续新的真实 BGE 评测，应在报告中声明：`label_status=user_confirmed_ai_assisted`、目标验证集、非独立测试集、未完成全语料完整性证明；不要把结果与此前 20 条开发集成绩合并。

本包不含密钥、虚拟环境、模型缓存或原始 BGE 结果 ZIP，不执行提交、推送或部署。
