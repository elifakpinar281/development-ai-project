
Model: qwen2.5:3b | commit: 54fb166 | date: 2026-10-22 | runs per case: 3 | eval cache: replay

| Case | Category | Passed (model runs) | Fallback runs | Verdicts | Median tool calls | Median s | Failed checks |
|---|---|---|---|---|---|---|---|
| martina-06 | failure_case | 3/3 | 0 | likely safe, phishing | 4 | 76.2 | - |
| martina-11 | difficult_case | 0/3 | 0 | phishing | 0 | 39.9 | observed:lookup_case_history=warning, verdict_in |
| martina-12 | difficult_case | 3/3 | 0 | likely safe | 0 | 32.2 | - |
| case-12 **(critical)** | difficult_case | 0/3 | 0 | phishing | 1 | 41.1 | verdict_in |
| case-13 **(critical)** | difficult_case | 2/2 | 1 | likely safe | 0.5 | 39.7 | - |
Critical cases with at least one failed run: case-12

| Category | Valid runs | Passed | Failed | Pass rate | Fallback runs |
|---|---:|---:|---:|---:|---:|
| difficult_case | 11 | 5 | 6 | 45% | 1 |
| failure_case | 3 | 3 | 0 | 100% | 0 |
| **All** | 14 | 8 | 6 | 57% | 1 |

Wrong verdict: model runs 6 of 14, fallback runs 0 of 1
  martina-11 run 1 (completed): phishing
  martina-11 run 2 (completed): phishing
  martina-11 run 3 (completed): phishing
  case-12 run 1 (completed): phishing
  case-12 run 2 (completed): phishing
  case-12 run 3 (completed): phishing
