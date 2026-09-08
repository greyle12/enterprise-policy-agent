# COV-001 复核更新

本次更新只记录 COV-001：
- `TRAVEL_POLICY_001__v1_0__article_007`：Grade 2，用于确认深圳属于一类城市。
- `TRAVEL_POLICY_001__v1_0__article_008`：Grade 3，直接给出普通员工住宿上限。

状态仍为 `pending`；没有填写复核者、复核时间，也没有声明已检查全部授权 Chunk。不要把这个更新当作整套人工复核完成。

## 安全应用（PowerShell）

解压本 ZIP 后，先查看 `PATCH-MANIFEST.json`。在项目根目录执行：

```powershell
$updated = Join-Path $patchDir "artifacts\retrieval-coverage-review-v1\review.json"
$target = ".\artifacts\retrieval-coverage-review-v1\review.json"
$backup = "$target.before-cov001-$(Get-Date -Format 'yyyyMMdd-HHmmss').bak"
Copy-Item $target $backup
Compare-Object (Get-Content $target) (Get-Content $updated)
# 确认差异只有 COV-001 后再替换：
Copy-Item $updated $target -Force
Copy-Item (Join-Path $patchDir "artifacts\retrieval-coverage-review-v1\validation-cov001.json") `
  ".\artifacts\retrieval-coverage-review-v1\validation-cov001.json" -Force
```

如果当前 `review.json` 已有其他人工标注，不要直接覆盖；手工合并 COV-001 两条 judgments，保留其他 case 和原 review 状态。备份文件可用于恢复。

验证：

```powershell
python -X utf8 -m scripts.manage_retrieval_coverage_review validate `
  --review-dir ".\artifacts\retrieval-coverage-review-v1" `
  --report ".\artifacts\retrieval-coverage-review-v1\validation-cov001-check.json"
```

预期仍是 `blocked_pending_human_review`、`blocked_case_count: 16`，因为只有第一条已有标签，整套复核尚未完成。
