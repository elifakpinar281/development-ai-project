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

### Domain checks and case history (Person 3)

Setup: `DATABASE_URL` points to `spotthephish_eval`, `EVAL_CACHE_MODE=replay`, cache seeded with `python -m evaluation.seed_cache --reset-history`, model: `<model>`, runs per case: `<n>`.

| Case | Input | Expected | Actual result | Tools called | Pass? |
|---|---|---|---|---|---|
| p3-01 | lookalike `paypa1.example` | lookalike flagged, reputation lists link, verdict phishing (hard rule, score >= 90) | | | |
| p3-02 | lookalike `hamk-student-login.example` | lookalike + domain age evidence, phishing or suspicious | | | |
| p3-03 | new domain, no brand | domain age flagged, at least suspicious | | | |
| p3-04 | campaign, first report | normal investigation, verdict stored in case history | | | |
| p3-05 | campaign, repeat (other sender/link) | history match on subject pattern, "known campaign", fewer tool calls | | | |
| p3-06 | online checks unavailable | tools return `unavailable`, verdict still produced, UI lists unavailable checks, no raw error | | | |

Notes on the results: `<what failed and why, which tool or rule was responsible>`