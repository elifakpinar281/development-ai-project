
Model: qwen2.5:3b | commit: 54fb166 | date: 2026-10-22 | runs per case: 3 | eval cache: replay

| Case | Category | Passed (model runs) | Fallback runs | Verdicts | Median tool calls | Median s | Failed checks |
|---|---|---|---|---|---|---|---|
| martina-01 **(critical)** | successful_case | 2/3 | 0 | phishing | 2 | 54.1 | observed:check_url_reputation=warning |
| martina-02 | successful_case | 3/3 | 0 | phishing | 2 | 54.6 | - |
| martina-03 | successful_case | 1/3 | 0 | phishing | 3 | 73.5 | evidence_cites:check_domain_age, observed:check_domain_age=warning |
| martina-04 | successful_case | 3/3 | 0 | phishing | 5 | 69.2 | - |
| martina-05 **(critical)** | difficult_case | 1/3 | 0 | phishing | 5 | 101.9 | evidence_cites:lookup_case_history, observed:lookup_case_history=warning |
| martina-06 | failure_case | 0/3 | 0 | phishing | 2 | 63.7 | unavailable:check_domain_age, warning:Some checks could not run |
| martina-07 | failure_case | 3/3 | 0 | phishing | 3 | 81.2 | - |
| martina-08 | failure_case | 3/3 | 0 | phishing | 3 | 76.1 | - |
| martina-09 **(critical)** | difficult_case | 3/3 | 0 | likely safe | 1 | 53.3 | - |
| martina-10 | successful_case | 3/3 | 0 | phishing | 3 | 99.8 | - |
| martina-11 | difficult_case | 0/3 | 0 | phishing | 2 | 75.4 | observed:check_lookalike_domain=warning, verdict_in |
| martina-12 | difficult_case | 1/3 | 0 | likely safe, phishing | 0 | 80.2 | max_risk, verdict_in |
| case-06 **(critical)** | successful_case | 3/3 | 0 | phishing | 4 | 84.5 | - |
| case-07 **(critical)** | difficult_case | 3/3 | 0 | phishing | 2 | 71.9 | - |
| case-08 | successful_case | 3/3 | 0 | phishing | 4 | 77.7 | - |
| case-09 **(critical)** | successful_case | 3/3 | 0 | phishing | 1 | 50.7 | - |
| case-10 | difficult_case | 2/3 | 0 | phishing | 2 | 49.2 | observed:inspect_attachments=warning |
| case-11 | difficult_case | 3/3 | 0 | phishing | 2 | 72.8 | - |
| case-12 **(critical)** | difficult_case | 0/3 | 0 | phishing | 1 | 41.3 | verdict_in |
| case-13 **(critical)** | difficult_case | 2/3 | 0 | likely safe, phishing | 1 | 39.5 | max_risk, verdict_in |
| case-14 | successful_case | 1/3 | 0 | phishing | 3 | 52.0 | observed:check_domain_age=warning |

Campaign repeat: median tool calls 5 (martina-05) vs 5 (martina-04).
Critical cases with at least one failed run: martina-01, martina-05, case-12, case-13

| Category | Valid runs | Passed | Failed | Pass rate | Fallback runs |
|---|---:|---:|---:|---:|---:|
| difficult_case | 27 | 15 | 12 | 56% | 0 |
| failure_case | 9 | 6 | 3 | 67% | 0 |
| successful_case | 27 | 22 | 5 | 81% | 0 |
| **All** | 63 | 43 | 20 | 68% | 0 |

Wrong verdict: model runs 9 of 63, fallback runs 0 of 0
  martina-11 run 1 (completed): phishing
  martina-11 run 2 (completed): phishing
  martina-11 run 3 (completed): phishing
  martina-12 run 1 (completed): phishing
  martina-12 run 2 (completed): phishing
  case-12 run 1 (completed): phishing
  case-12 run 2 (completed): phishing
  case-12 run 3 (completed): phishing
  case-13 run 1 (completed): phishing
