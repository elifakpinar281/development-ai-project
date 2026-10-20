# Evaluation Results Summary

Document your team's evaluation results below.

## Summary Overview

- **Date of Evaluation:** YYYY-MM-DD
- **Model Evaluated:** (e.g., `llama3.2`)
- **Total Test Cases Executed:** 0
- **Passed:** 0
- **Failed:** 0
- **Pass Rate:** 0%

## Performance by Category

| Category | Total Cases | Passed | Failed | Pass Rate |
| :--- | :---: | :---: | :---: | :---: |
| Successful Cases | 0 | 0 | 0 | 0% |
| Difficult Cases | 0 | 0 | 0 | 0% |
| Failure / Edge Cases | 0 | 0 | 0 | 0% |

## Key Findings & Failure Analysis

### What worked well
- Observation 1
- Observation 2

### Observed failure modes
- Description of case where the system failed and why.

## Action Items & Improvements
- [ ] Planned fix or enhancement based on evaluation findings.

### Domain checks and case history (Martina)

**Setup:** Evaluation database `spotthephish_eval`, `EVAL_CACHE_MODE=replay`, with cache and case history reset before every trial using `python -m evaluation.run_p3_eval`. Model: `qwen2.5:3b` (Ollama), commit `0bd105f`, 3 runs per case, evaluation date `2026-10-20`. Grading is code-based and covers verdicts, risk ranges, tool observations, history rows, and error leakage. Critical cases are reported separately.

**Agent with model**

Model: `qwen2.5:3b` | Commit: `0bd105f` | Date: `2026-10-20` | Runs per case: 3 | Cache: `replay`

| Case                      | Category | Passed (model runs) | Fallback runs | Verdicts | Median tool calls | Median s | Failed checks |
|---------------------------|---|---:|---:|---|---:|---:|---|
| martina-01 **(critical)** | successful_case | 1/3 | 0 | phishing | 3 | 65.3 | min_risk, observed:check_lookalike_domain=warning, observed:check_url_reputation=warning |
| martina-02                | successful_case | 0/1 | 2 | phishing | 4 | 66.8 | observed:check_lookalike_domain=warning |
| martina-03                | successful_case | 0/2 | 1 | likely safe, phishing | 2.5 | 54.8 | evidence_cites:check_domain_age, observed:check_domain_age=warning, verdict_in |
| martina-04                | successful_case | 1/2 | 1 | phishing | 3.0 | 55.8 | observed:check_domain_age=warning |
| martina-05 **(critical)** | difficult_case | 0/1 | 2 | phishing | 2 | 51.0 | evidence_cites:lookup_case_history, observed:lookup_case_history=warning |
| martina-06                | failure_case | Not run (no model) | 3 | — | — | — | — |
| martina-07                | failure_case | 1/1 | 2 | likely safe | 3 | 61.1 | — |
| martina-08                | failure_case | 3/3 | 0 | likely safe, phishing | 3 | 61.4 | — |
| martina-09 **(critical)** | difficult_case | 2/2 | 1 | likely safe | 2.5 | 43.5 | — |
| martina-10                | successful_case | 1/3 | 0 | phishing | 4 | 77.4 | observed:check_lookalike_domain=warning |
| martina-11                | difficult_case | 1/1 | 2 | likely safe | 0 | 19.2 | — |
| martina-12                | difficult_case | 2/2 | 1 | likely safe | 2.0 | 29.8 | — |
| case-06 **(critical)**    | successful_case | Not run (no model) | 3 | — | — | — | — |
| case-07 **(critical)**    | difficult_case | 1/1 | 2 | phishing | 2 | 53.9 | — |
| case-08                   | successful_case | 2/2 | 1 | phishing | 1.0 | 37.1 | — |
| case-09 **(critical)**    | successful_case | 2/2 | 1 | phishing | 1.0 | 37.5 | — |
| case-10                   | difficult_case | 2/2 | 1 | phishing | 4.0 | 89.8 | — |
| case-11                   | difficult_case | 2/2 | 1 | phishing | 5.0 | 115.5 | — |
| case-12 **(critical)**    | difficult_case | 2/2 | 1 | suspicious | 3.0 | 82.3 | — |
| case-13 **(critical)**    | difficult_case | 1/1 | 2 | likely safe | 0 | 30.5 | — |
| case-14                   | successful_case | 2/2 | 1 | phishing | 4.5 | 125.0 | — |

**Baseline without a model: offline fallback** (0 model calls, 1 run per case)

| Case                                            | Fallback verdict | What it shows |
|-------------------------------------------------|---|---|
| martina-01 — PayPal lookalike                   | phishing (90) | Lookalike detection alone is sufficient in this case. |
| martina-02 — HAMK lookalike                     | phishing (70) | Lookalike detection identifies the suspicious domain. |
| martina-03 / martina-04 / case-14 — New domains | likely safe (0) | The fallback lacks domain-age and reputation checks. |
| martina-05 — Campaign repeat                    | likely safe (0) | The fallback does not use case history. |
| martina-10 — Homoglyph                          | phishing (100) | Offline lookalike detection works. |
| case-06 / case-07 — Prompt injection            | phishing (80) | The injection scan and lookalike detection contribute to the result. |
| case-12 — Injection in harmless email           | suspicious (60) | The injection rule raises a warning. |
| case-10 — Password-protected ZIP                | likely safe (35) | The attachment warning remains below the suspicious threshold. |
| case-11 — Payment fraud                         | likely safe (0) | No technical indicator is detected; language analysis is needed. |
| martina-12 — Harmless email containing “office” | phishing (70) | A brand-matching heuristic causes a false positive. |

**Key findings**

- **What worked:** The agent successfully detected several lookalike-domain and phishing cases, including `case-08`, `case-09`, and `case-14`. Language analysis also helped identify payment fraud and suspicious attachment scenarios that the offline fallback missed. Tool-assisted checks provide useful capabilities beyond static rules.
- **Failure modes:** (1) The critical case `martina-01` passed only 1 of 3 model runs and failed the minimum-risk and expected tool-observation checks. (2) The critical campaign-repeat case `p3-05` failed the expected case-history checks. (3) Domain-age observations and evidence were inconsistent in `p3-03` and `p3-04`. (4) The offline fallback missed new-domain phishing and payment fraud. (5) The brand heuristic produced a false positive for `p3-12`.
- **Model availability:** A total of 28 of 63 trials used the offline fallback instead of the AI model. This limits the conclusions that can be drawn about model performance and should be investigated separately.
- **Campaign repeat:** Median tool calls were 2 for `martina-05`, compared with 3 for `martina-04`. The expected case-history lookup was not reliably observed in the repeat case.

**Limitations:** Seeded domain ages for fictional `.example` domains are assumptions. The results therefore demonstrate application behaviour, not the accuracy of real RDAP or Safe Browsing data. Three runs per case at temperature 0 provide limited information about variability, and the test set is small. In addition, fallback executions are not equivalent to model executions and should be reported separately when assessing AI performance.

**Conclusion:** Tool use is justified for domain age, URL reputation, and case history, which are not available in the offline fallback. The main priorities are to investigate intermittent model availability, improve tool-use consistency, and rerun the critical cases after the fixes.
