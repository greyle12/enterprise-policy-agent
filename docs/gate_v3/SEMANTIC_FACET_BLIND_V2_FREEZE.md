# Semantic Facet Blind v2 Freeze and Boundary Evaluation

## Result

The reviewed 18-case package was frozen successfully and loaded by the existing strict semantic Facet adapter. The adapter accepted empty `accepted_facets` for SFB-009 and SFB-012, while retaining their source records as `PARTIAL` with `request_facet` in `missing_outputs`.

The dataset remains a targeted review set. Although it is stored separately from the earlier development packages, it was authored after inspecting earlier failures and was not independently double-annotated. Therefore `independent_test_set=false`, `numeric_resume_ready=false`, and no model score is claimed by this step.

## Frozen package

- Dataset version: `semantic-facet-blind-v2-confirmed-1`
- Source dataset version: `semantic-facet-blind-v2-source-1`
- Cases: 18
- Accepted Facet instances: 17
- Empty Facet truth cases: SFB-009, SFB-012
- Confirmation: user reply `接受，并允许执行上述冻结与验收步骤`
- Model inference: false
- Retrieval executed: false
- Parser runtime integration: false
- Adapter manifest verification: passed

The original pending-review files remain in `docs/gate_v3/semantic-facet-blind-v2-draft/`. The confirmed package is in `docs/gate_v3/semantic-facet-blind-v2-confirmed/`.

## Hashes and provenance

- Questions SHA-256: `e29ac7253973de429aaa476a56b7bcf78552cebd144f3bd48c02a847513b697c`
- Review labels SHA-256: `85f5c9ef52b63a9c884b4575071931a892120fe7bf7f42b2cdcef2ca79134f09`
- Frozen records SHA-256: `e3d27d7184ac37af0d1c2e2963ced12f31e2f11e8cd37b74048bc183076eb7b3`
- Frozen accepted sidecar SHA-256: `02903019c5beb4c90718fadbd2ec69c162aaa3149ba2d774db8b05943ac3303a`
- Confirmation SHA-256: `3132da699f5c793bd99dda697a9ac00694398d493d47e15ecdb591a2fd592a31`
- Label-free requests SHA-256: `b03be546d1891fb423fdc893a7323bfed31e205c8eff70c0aba28ef39f03e86c`
- Prompt SHA-256: `1e327a051e0f2f0cdd0137930e9931fa665a749bace653977f5adc42edf6af2a`
- Code HEAD at freeze/export: `170190ec3f1819386afd2fa439f8c54ce1f43d5e`
- Fixture evaluator SHA-256: `828acc7be66d93bf02aabb826b1740ca6d370ea1d598dcdae92d606bf4a9ec30`

The questions and review hashes above are recorded in the draft validator output. The frozen package manifest is authoritative for the generated records, accepted sidecar, confirmation, proposal, and prompt.

## Boundary fixtures

The fixture runner generated three prediction files and evaluated them against the frozen sidecar. It did not call a model.

| Fixture | Purpose | Coverage | Facet result | Binding | Exact |
| --- | --- | ---: | --- | --- | ---: |
| complete | Accepted labels reproduced, including both empty arrays | 18/18 | 17 TP, 0 FP, 0 FN | 17/17 | 18/18 |
| extra-facet | Add `DEADLINE/BOUND` to empty-truth SFB-009 | 18/18 | 17 TP, 1 FP, 0 FN; precision 17/18 | matched Facet only | 17/18 |
| missing-prediction | Omit the SFB-012 prediction line | 17/18 | 17 TP, 0 FP, 0 FN | 17/17 | 17/18 |

The missing-prediction case keeps Facet metrics at 17/17 because the omitted case has an empty truth set; coverage and exact-match metrics expose the missing model output. This confirms that an empty prediction is different from an absent prediction.

## Checks

- `scripts/freeze_semantic_facet_blind.py`: freeze command passed.
- `scripts/export_semantic_facet_requests.py`: exported 18 label-free requests; model-visible files are only `prompt.txt` and `requests.jsonl`.
- `scripts/evaluate_semantic_facet_blind_fixtures.py`: three corrected boundary reports generated in `artifacts/semantic-facet-blind-v2-evaluation-final/`.
- `artifacts/semantic-facet-blind-v2-evaluation/` and `artifacts/semantic-facet-blind-v2-evaluation-corrected/` are retained as superseded diagnostic runs. The first version omitted SFB-018 while exercising the missing-prediction branch; the corrected version was generated before final Ruff whitespace normalization. Both are excluded from the authoritative results below.
- Ruff format/check: passed for all new Python files.
- Pytest: `18 passed` across freeze, adapter, and evaluator boundary tests.

PowerShell commands:

```powershell
Set-Location D:\Ai_agent_program\demo1
$env:PYTHONPATH = (Resolve-Path .\.venv\Lib\site-packages).Path
$taskPython = 'C:\Users\Grey\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $taskPython -X utf8 -m scripts.freeze_semantic_facet_blind
& $taskPython -X utf8 -m scripts.export_semantic_facet_requests --records docs/gate_v3/semantic-facet-blind-v2-confirmed/records.jsonl --accepted docs/gate_v3/semantic-facet-blind-v2-confirmed/accepted.json --confirmation docs/gate_v3/semantic-facet-blind-v2-confirmed/confirmation.json --manifest docs/gate_v3/semantic-facet-blind-v2-confirmed/manifest.json --prompt docs/gate_v3/semantic-facet-prompt-v1.txt --project-root . --output artifacts/semantic-facet-blind-v2-requests
& $taskPython -X utf8 -m scripts.evaluate_semantic_facet_blind_fixtures --project-root . --output artifacts/semantic-facet-blind-v2-evaluation
& $taskPython -X utf8 -m pytest -p no:cacheprovider --basetemp .pytest_tmp-blind-v2-20260911 -q tests/evaluation/test_freeze_semantic_facet_blind.py tests/evaluation/test_semantic_facet_blind_evaluation.py tests/evaluation/test_semantic_request_facets_adapter.py
```

## Decision and next step

The adapter and evaluator contract meet the expected boundary behavior. Empty truth is scored as an actual empty set, an extra Facet is counted as a false positive, and a missing prediction reduces coverage and exact match. No label was changed after observing a model result because no model was run.

The next separate step is to review the frozen package as the exact external payload, then run the 18 label-free requests through the same real-model runner if external sending is authorized for these specific questions. The resulting model report must retain the `independent_test_set=false` boundary and compare error categories separately.
