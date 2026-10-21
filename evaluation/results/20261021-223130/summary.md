# Evaluation summary

Model: `qwen2.5:3b` | Commit: `50e6bff` | Date: 2026-10-21 | Cache: `off` | Suspicious counts as: **phishing** | Emails: 35 | Zenodo seed: 42

## Detection quality (all emails)

| Configuration | Runs | Answered | TP | FP | TN | FN | Accuracy | Precision | Recall | F1 | False-positive rate |
|---|---|---|---|---|---|---|---|---|---|---|---|
| agent | 35 | 35 | 25 | 5 | 5 | 0 | 0.857 | 0.833 | 1.0 | 0.909 | 0.5 |
| fixed_pipeline | 35 | 35 | 23 | 0 | 10 | 2 | 0.943 | 1.0 | 0.92 | 0.958 | 0.0 |
| llm_only | 35 | 35 | 21 | 1 | 9 | 4 | 0.857 | 0.955 | 0.84 | 0.894 | 0.1 |

Offline fallback runs are included above because the user sees that verdict too. The table below counts only runs where the model really answered.

| Configuration | Runs | Answered | TP | FP | TN | FN | Accuracy | Precision | Recall | F1 | False-positive rate |
|---|---|---|---|---|---|---|---|---|---|---|---|
| agent | 34 | 34 | 24 | 5 | 5 | 0 | 0.853 | 0.828 | 1.0 | 0.906 | 0.5 |
| fixed_pipeline | 33 | 33 | 21 | 0 | 10 | 2 | 0.939 | 1.0 | 0.913 | 0.955 | 0.0 |
| llm_only | 34 | 34 | 20 | 1 | 9 | 4 | 0.853 | 0.952 | 0.833 | 0.889 | 0.1 |

## Efficiency and reliability

| Configuration | Median time per email (s) | Mean model calls | Mean tool calls | Offline fallbacks | Runs with invalid model output | No answer |
|---|---|---|---|---|---|---|
| agent | 74.7 | 5.2 | 2.9 | 1 | 3 | 0 |
| fixed_pipeline | 58.7 | 1.0 | 6.6 | 2 | 1 | 0 |
| llm_only | 26.3 | 1.1 | 0 | 1 | 5 | 0 |

## Result of every email

| Case | Source | Label | agent | fixed_pipeline | llm_only |
|---|---|---|---|---|---|
| 19-hidden-html-injection | hand-crafted | phishing | phishing 75 (ok) | phishing 70 (ok) | phishing 75 (ok) |
| 20-fake-data-block-end | hand-crafted | phishing | phishing 70 (ok) | suspicious 50 (ok) | phishing 70 (ok) |
| 21-step-budget-attack | hand-crafted | phishing | phishing 75 (ok) | phishing 70 (ok) | phishing 75 (ok) |
| 22-legit-mentions-ai | hand-crafted | legitimate | likely safe 30 (ok) | likely safe 30 (ok) | likely safe 39 (ok) |
| 23-legit-password-reset | hand-crafted | legitimate | phishing 75 (WRONG) | likely safe 30 (ok) | likely safe 39 (ok) |
| 24-pasted-body-no-headers | hand-crafted | phishing | phishing 70 (ok) | phishing 80 (ok, fallback) | phishing 75 (ok) |
| 25-gift-card-no-indicators | hand-crafted | phishing | phishing 75 (ok) | phishing 70 (ok) | phishing 75 (ok) |
| 26-finnish-language | hand-crafted | phishing | phishing 70 (ok, fallback) | phishing 70 (ok, fallback) | phishing 70 (ok, fallback) |
| 27-link-after-long-text | hand-crafted | phishing | phishing 70 (ok) | phishing 70 (ok) | likely safe 30 (WRONG) |
| 28-fake-security-banner | hand-crafted | phishing | phishing 70 (ok) | phishing 100 (ok) | phishing 75 (ok) |
| 01-lookalike-paypal | hand-crafted | phishing | phishing 80 (ok) | phishing 75 (ok) | phishing 75 (ok) |
| 02-lookalike-hamk | hand-crafted | phishing | phishing 70 (ok) | likely safe 30 (WRONG) | phishing 75 (ok) |
| 03-new-domain-invoice | hand-crafted | phishing | phishing 75 (ok) | likely safe 30 (WRONG) | likely safe 39 (WRONG) |
| 04-campaign-first | hand-crafted | phishing | phishing 75 (ok) | phishing 75 (ok) | phishing 75 (ok) |
| 05-campaign-repeat | hand-crafted | phishing | phishing 75 (ok) | phishing 75 (ok) | phishing 75 (ok) |
| 06-prompt-injection-in-body | hand-crafted | phishing | phishing 75 (ok) | suspicious 50 (ok) | phishing 75 (ok) |
| 07-prompt-injection-in-reply | hand-crafted | phishing | phishing 70 (ok) | phishing 70 (ok) | phishing 75 (ok) |
| 08-suspicious-attachment | hand-crafted | phishing | phishing 70 (ok) | phishing 75 (ok) | likely safe 39 (WRONG) |
| 09-double-file-extension | hand-crafted | phishing | phishing 70 (ok) | phishing 95 (ok) | phishing 75 (ok) |
| 10-password-protected-archive | hand-crafted | phishing | phishing 75 (ok) | phishing 75 (ok) | phishing 75 (ok) |
| 11-payment-fraud | hand-crafted | phishing | phishing 70 (ok) | phishing 70 (ok) | phishing 75 (ok) |
| 12-prompt-injection-in-harmless-email | hand-crafted | phishing | phishing 75 (ok) | suspicious 50 (ok) | likely safe 30 (WRONG) |
| 13-PDF-attachment-in-harmless-email | hand-crafted | legitimate | phishing 75 (WRONG) | likely safe 39 (ok) | likely safe 39 (ok) |
| 14-fake-delivery | hand-crafted | phishing | phishing 75 (ok) | phishing 75 (ok) | phishing 75 (ok) |
| 15-legit-hamk-notice | hand-crafted | legitimate | likely safe 30 (ok) | likely safe 39 (ok) | likely safe 39 (ok) |
| 16-homoglyph-sender | hand-crafted | phishing | phishing 75 (ok) | phishing 70 (ok) | phishing 75 (ok) |
| 17-legit-subject-like-campaign | hand-crafted | legitimate | likely safe 30 (ok) | likely safe 39 (ok) | likely safe 39 (ok) |
| 18-legit-brand-word-in-domain | hand-crafted | legitimate | phishing 75 (WRONG) | likely safe 25 (ok) | likely safe 39 (ok) |
| 29-legit-hamk | hand-crafted | legitimate | likely safe 30 (ok) | likely safe 39 (ok) | likely safe 39 (ok) |
| 30-auth-spoof-moodle | hand-crafted | phishing | phishing 75 (ok) | phishing 100 (ok) | phishing 75 (ok) |
| 31-attachment-double-ext | hand-crafted | phishing | phishing 95 (ok) | phishing 95 (ok) | phishing 75 (ok) |
| 32-deceptive-links-punycode | hand-crafted | phishing | phishing 75 (ok) | phishing 90 (ok) | phishing 75 (ok) |
| j01-legit-security-alert | hand-crafted | legitimate | phishing 75 (WRONG) | likely safe 39 (ok) | phishing 75 (WRONG) |
| j02-legit-finnish-notice | hand-crafted | legitimate | phishing 75 (WRONG) | likely safe 39 (ok) | likely safe 39 (ok) |
| j05-legit-password-expiry | hand-crafted | legitimate | likely safe 39 (ok) | likely safe 39 (ok) | likely safe 39 (ok) |
