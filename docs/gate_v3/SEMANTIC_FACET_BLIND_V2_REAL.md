# Semantic Facet Blind v2 Real-Model Evaluation

## Result

The user-authorized real-model run completed for all 18 frozen, label-free requests. Every response produced a valid strict prediction record, all responses ended with `finish_reason=stop`, and every frozen Facet label and binding matched the confirmed sidecar.

This is a targeted development-set result. The package was authored after inspecting earlier development failures and was not independently double-annotated, so `independent_test_set=false`, `numeric_resume_ready=false`, and `semantic_accuracy=null` remain authoritative.

## Scope and protocol

- Dataset version: `semantic-facet-blind-v2-confirmed-1`
- Cases: 18
- Accepted Facet instances: 17
- Empty expected Facet cases: SFB-009 and SFB-012
- Input package: `artifacts/semantic-facet-blind-v2-requests/`
- Prompt version: `semantic-facet-prompt-v1`
- Endpoint: `https://api.deepseek.com`
- Requested model alias: `deepseek-v4-flash`
- Returned model: `deepseek-flash` for all 18 responses
- Model revision: `unavailable-provider-alias`
- System fingerprint: `aeb56401ca74e127821c4f9126dcb669` for all 18 responses
- Temperature: `0`
- Requested `max_tokens`: `4096`
- Timeout: 30 seconds
- Automatic retries: 0
- Python: `3.12.14`
- Platform: Windows 11, AMD64
- Code HEAD at run: `170190ec3f1819386afd2fa439f8c54ce1f43d5e`
- Executed runner SHA-256: `45e8f0785837099eb2a45edc1563fd58ae87f9936d0d34064baffa834942a0bc`
- Evaluator SHA-256: `a00b663f6f5ef056fe508d27a5a52305430e70d3f7b348e20dfc54624c54d4e5`

The provider returned a model name different from the requested alias and did not expose a pinned revision. The run is therefore traceable to the recorded request, prompt, endpoint, returned model, and system fingerprint, but not to an immutable provider revision.

## Input and output hashes

- Records SHA-256: `e3d27d7184ac37af0d1c2e2963ced12f31e2f11e8cd37b74048bc183076eb7b3`
- Accepted sidecar SHA-256: `02903019c5beb4c90718fadbd2ec69c162aaa3149ba2d774db8b05943ac3303a`
- Confirmation SHA-256: `3132da699f5c793bd99dda697a9ac00694398d493d47e15ecdb591a2fd592a31`
- Prompt SHA-256: `1e327a051e0f2f0cdd0137930e9931fa665a749bace653977f5adc42edf6af2a`
- Requests SHA-256: `b03be546d1891fb423fdc893a7323bfed31e205c8eff70c0aba28ef39f03e86c`
- Predictions SHA-256: `73911a38fc5913504aa3acee30e17a1e754d43e2ffc9103d977c86d700f4c2f0`
- Provenance SHA-256: `e4bdb968eb063ae155245750d156594518d651db4452038716252c0a5a81f9cf`

## Expected checks and observed results

| Check | Expected | Observed | Result |
| --- | ---: | ---: | --- |
| API responses | 18/18 | 18/18 | passed |
| Strict prediction records | 18/18 | 18/18 | passed |
| `finish_reason=stop` | 18/18 | 18/18 | passed |
| Truncated or empty model outputs | 0 | 0 | passed |
| Prediction coverage | 18/18 | 18/18 | passed |
| Facet label precision | report only | 17/17 = 1.000 | passed on this set |
| Facet label recall | report only | 17/17 = 1.000 | passed on this set |
| Facet label F1 | report only | 1.000 | passed on this set |
| Binding accuracy | report only | 17/17 = 1.000 | passed on this set |
| Case exact match | report only | 18/18 = 1.000 | passed on this set |

The evaluator reports `semantic_accuracy=null` by design. These values measure agreement with the frozen Facet annotations. They do not measure end-to-end answer accuracy, retrieval quality, or production behavior.

## Response operational evidence

- Average request time: 2.339 seconds
- Minimum request time: 1.156 seconds
- Maximum request time: 8.171 seconds
- Average completion tokens: 317.2
- Minimum completion tokens: 114
- Maximum completion tokens: 840
- All 18 responses shared the same recorded system fingerprint.

## Category analysis

| Category | Cases | Exact | FP | FN | Binding mismatches |
| --- | ---: | ---: | ---: | ---: | ---: |
| 时间条件 | 3 | 3/3 | 0 | 0 | 0 |
| 跨句指代 | 3 | 3/3 | 0 | 0 | 0 |
| 引用说法 | 3 | 3/3 | 0 | 0 | 0 |
| 明确排除 | 3 | 3/3 | 0 | 0 | 0 |
| 流程候选 | 3 | 3/3 | 0 | 0 | 0 |
| 责任主体 | 3 | 3/3 | 0 | 0 | 0 |

The model preserved all six intended distinctions:

- Time conditions were not promoted to `DEADLINE`; explicit deadline questions were recognized.
- A unique pronoun or ordinal reference was `BOUND`, while an ambiguous reference was `UNBOUND`.
- Quoted material was ignored when only reported, included when explicitly adopted by the user, and produced an empty array for pure transcription.
- Explicit exclusions were respected, including the double-negative procedure case and the all-excluded empty cases.
- Explicit process candidates were `PROCESS_CHOICE/BOUND`; unspecified candidates were `PROCESS_CHOICE/UNBOUND`; a selected candidate followed by a step request was `PROCEDURE/BOUND`.
- The responsibility category handled clear, ambiguous, and multi-facet requests as annotated.

## Decision

The run meets the operational and label-agreement expectations for this targeted set. It provides evidence that the current prompt, strict output contract, and evaluator can handle the tested semantic boundaries with the selected provider response.

It does not support switching v3.1 into production, replacing v2, declaring general semantic accuracy, or treating this set as an independent test set. No labels or failed cases were removed or edited after observing the model result. Retrieval and parser runtime integration were not executed.

## Artifacts

- Frozen package: `docs/gate_v3/semantic-facet-blind-v2-confirmed/`
- Label-free requests: `artifacts/semantic-facet-blind-v2-requests/`
- Raw responses, candidates, provenance, run status, predictions, and evaluation: `artifacts/semantic-facet-blind-v2-real/`

PowerShell reproduction commands:

```powershell
Set-Location D:\Ai_agent_program\demo1
$env:PYTHONPATH = (Resolve-Path .\.venv\Lib\site-packages).Path
$taskPython = 'C:\Users\Grey\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:FACET_RUN_INPUT = 'artifacts/semantic-facet-blind-v2-requests'
$env:FACET_RUN_OUTPUT = 'artifacts/semantic-facet-blind-v2-real-reproduction'
$env:FACET_MAX_TOKENS = '4096'
$env:LLM_MODEL = 'deepseek-v4-flash'
& $taskPython -X utf8 -m scripts.run_semantic_facet_inference
& $taskPython -X utf8 -m scripts.evaluate_semantic_request_facets --records docs/gate_v3/semantic-facet-blind-v2-confirmed/records.jsonl --accepted docs/gate_v3/semantic-facet-blind-v2-confirmed/accepted.json --confirmation docs/gate_v3/semantic-facet-blind-v2-confirmed/confirmation.json --manifest docs/gate_v3/semantic-facet-blind-v2-confirmed/manifest.json --predictions artifacts/semantic-facet-blind-v2-real-reproduction/predictions.jsonl --project-root . --prediction-kind real_model --prediction-source 'DeepSeek API real inference' --model-id deepseek-v4-flash --model-revision unavailable-provider-alias --output artifacts/semantic-facet-blind-v2-real-reproduction/evaluation.json
```

The reproduction command requires separate authorization for sending these 18 questions and a new output directory; the recorded run above is the authoritative result for this step.

## Next step

The next separate step should create a genuinely independent semantic holdout with fresh authoring and, where possible, a second annotation pass. It should include the same six boundary families plus cases not derived from the current failures. Only after that package is frozen should another real-model run be used to test generalization.
