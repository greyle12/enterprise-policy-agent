# Semantic Facet Challenge v1: Real-Model Evaluation

## Status

This bounded real-model run completed successfully. All 12 user-confirmed challenge questions returned a parseable prediction, and every expected facet and binding matched the frozen annotations.

This is a targeted development-set challenge result, not a general semantic-accuracy claim. The frozen package records `independent_test_set=false`, `numeric_resume_ready=false`, and `semantic_accuracy=null`.

## Evaluation scope

- Dataset version: `semantic-facet-challenge-v1-confirmed-1`
- Source dataset version: `semantic-facet-challenge-v1-source-1`
- Cases: 12
- Accepted facet instances: 14
- Paired challenge groups: 6
- Confirmation status: user-confirmed
- Retrieval executed: no
- Parser runtime integration: no
- Evaluator inference: no; evaluation was offline against the frozen sidecar

The challenge cases were intentionally paired to test the following distinctions:

| Group | Distinction | Cases |
| --- | --- | --- |
| P1 | time condition versus explicit deadline request | SFC-001/SFC-002 |
| P2 | time condition versus explicit deadline request for procedure | SFC-003/SFC-004 |
| P3 | explicit object versus omitted object | SFC-005/SFC-006 |
| P4 | explicit object versus omitted object for materials | SFC-007/SFC-008 |
| P5 | explicit process candidates versus vague candidates | SFC-009/SFC-010 |
| P6 | reported process candidates versus unspecified process content | SFC-011/SFC-012 |

## Reproducibility evidence

### Frozen input

- Records SHA-256: `39a6e84d611f916b4de028df9cce5a144b6ee8f414fb6a180ef77432950672c1`
- Accepted annotations SHA-256: `9221b5eadd62a01b865c94d4c5f75febb0c906ea1e04b459af788d897b313811`
- Confirmation SHA-256: `a0fa7b14a5b3f4406a1f0564470ccb6bd538016acccde86a04e726286639d4d8`
- Prompt SHA-256: `1e327a051e0f2f0cdd0137930e9931fa665a749bace653977f5adc42edf6af2a`
- Label-free requests SHA-256: `760a97507cc98d0fbd17236fd02c74e36a3a12baafc161f1346b631082a3640f`

### Runtime

- Code HEAD at run start: `fb6a15be5cb4b7a0e5e3a2529658295f636fdcd0`
- Executed runner SHA-256: `345d47604b05c7e72a4e765114b4917ffee935c95c82c7bcd07d72951193da5b`
- Packaged runner SHA-256 after Ruff whitespace normalization: `45e8f0785837099eb2a45edc1563fd58ae87f9936d0d34064baffa834942a0bc`
- Evaluator SHA-256: `a00b663f6f5ef056fe508d27a5a52305430e70d3f7b348e20dfc54624c54d4e5`
- Python: `3.12.14`
- OS: Windows 11 (`Windows-11-10.0.26200-SP0`)
- Temperature: `0`
- Requested max tokens: `4096`
- Request timeout: 30 seconds
- Automatic retries: 0

### Provider identity

- Endpoint: `https://api.deepseek.com`
- Requested model alias: `deepseek-v4-flash`
- Returned model: `deepseek-flash` for all 12 responses
- Model revision: `unavailable-provider-alias`
- System fingerprint: `aeb56401ca74e127821c4f9126dcb669` for all 12 responses

The returned model name differs from the requested alias. Because the provider did not expose a stable revision in this run, the result is reproducible only to the recorded request, prompt, provider endpoint, returned model, and fingerprint—not to a pinned model revision.

## Expected checks and observed results

| Check | Expected | Observed | Result |
| --- | ---: | ---: | --- |
| API responses | 12/12 | 12/12 | passed |
| Strict prediction records | 12/12 | 12/12 | passed |
| `finish_reason=stop` | 12/12 | 12/12 | passed |
| Truncated/empty model outputs | 0 | 0 | passed |
| Prediction coverage | 12/12 | 12/12 | passed |
| Facet label precision | report only | 14/14 = 1.000 | passed on this set |
| Facet label recall | report only | 14/14 = 1.000 | passed on this set |
| Facet label F1 | report only | 1.000 | passed on this set |
| Binding accuracy | report only | 14/14 = 1.000 | passed on this set |
| Case exact match | report only | 12/12 = 1.000 | passed on this set |

The evaluator reports `semantic_accuracy=null` by design. The values above are facet-label, binding, coverage, and exact-match agreement with the frozen development annotations; they are not end-to-end answer accuracy and are not a production gate.

## Case-level result

| Case | Expected | Observed | Exact |
| --- | --- | --- | --- |
| SFC-001 | MATERIAL/BOUND | MATERIAL/BOUND | yes |
| SFC-002 | MATERIAL/BOUND, DEADLINE/BOUND | MATERIAL/BOUND, DEADLINE/BOUND | yes |
| SFC-003 | PROCEDURE/BOUND | PROCEDURE/BOUND | yes |
| SFC-004 | PROCEDURE/BOUND, DEADLINE/BOUND | PROCEDURE/BOUND, DEADLINE/BOUND | yes |
| SFC-005 | PROCEDURE/BOUND | PROCEDURE/BOUND | yes |
| SFC-006 | PROCEDURE/UNBOUND | PROCEDURE/UNBOUND | yes |
| SFC-007 | MATERIAL/BOUND | MATERIAL/BOUND | yes |
| SFC-008 | MATERIAL/UNBOUND | MATERIAL/UNBOUND | yes |
| SFC-009 | PROCESS_CHOICE/BOUND | PROCESS_CHOICE/BOUND | yes |
| SFC-010 | PROCESS_CHOICE/UNBOUND | PROCESS_CHOICE/UNBOUND | yes |
| SFC-011 | PROCESS_CHOICE/BOUND | PROCESS_CHOICE/BOUND | yes |
| SFC-012 | PROCESS_CHOICE/UNBOUND | PROCESS_CHOICE/UNBOUND | yes |

The model correctly preserved all six paired distinctions in this run, including omitted-object `UNBOUND` cases and reported-but-explicit process candidates.

## Artifacts

- Frozen confirmed package: `docs/gate_v3/semantic-facet-challenge-v1-confirmed/`
- Label-free request package: `artifacts/semantic-facet-challenge-v1-requests/`
- Raw responses and candidate outputs: `artifacts/semantic-facet-challenge-v1-real/response-*.json` and `candidate-*.jsonl`
- Run provenance: `artifacts/semantic-facet-challenge-v1-real/provenance.json`
- Per-request status: `artifacts/semantic-facet-challenge-v1-real/run.json`
- Offline evaluation: `artifacts/semantic-facet-challenge-v1-real/evaluation.json`

## Limitations and decision

The expected outcome for this bounded challenge was achieved: complete responses, no truncation, and correct facet/binding outputs for all 12 cases. This result supports keeping the semantic interface and real-model runner as an experimental evaluation path.

It does not justify switching v3.1 into production, replacing v2, claiming semantic model accuracy, or asserting generalization. The annotations were confirmed from a targeted challenge draft, the set is not independent, the provider model revision is unavailable, and the evaluator does not execute the parser runtime.

No labels or failed cases were removed or changed based on the result. No production runtime or retrieval path was modified.

After the API run, Ruff normalized one line break in the untracked runner. The change was whitespace-only; `provenance.json` retains the exact hash of the runner used for inference, and the packaged hash above identifies the normalized source delivered with this step.
