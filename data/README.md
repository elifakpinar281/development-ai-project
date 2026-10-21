# Data

This folder and the `evaluation` folder contain all data that SpotThePhish uses for testing and evaluation. The application itself needs no dataset to run. It analyses the single email that the user pastes or uploads.

## Overview

| Data | Location | Committed | Purpose |
|---|---|---|---|
| Phishing validation emails | `data/phishing_validation_emails.csv` | Yes | Language-based detection and false positive rate |
| Hand-crafted test emails | `evaluation/cases` | Yes | Header, link, domain, attachment and prompt injection checks |
| Raw corpora and private samples | `data/raw` | No | Local experiments only |
| Case history | PostgreSQL, database `spotthephish` | No | Fingerprints of earlier verdicts |
| Evaluation cache | PostgreSQL, database `spotthephish_eval` | No | Recorded and seeded results of the online checks |

## Phishing validation emails

The file `phishing_validation_emails.csv` is the Phishing Validation Emails Dataset by Miltchev et al. (2024). It is published on Zenodo under the licence CC BY 4.0 with the DOI [10.5281/zenodo.13474746](https://doi.org/10.5281/zenodo.13474746). The licence allows us to share the file in this repository as long as the source is named.

The file has 2000 rows and two columns. The column `Email Text` contains a short email text, and the column `Email Type` contains the label `Phishing Email` or `Safe Email`. There are 1000 emails of each class. The texts are short and have no headers, no sender and no attachments. Some of them are artificially generated.

The evaluation script takes a stratified sample with a fixed seed, so half of the sample is phishing and half is legitimate, and the same seed always gives the same emails. The label `Phishing Email` is counted as phishing and the label `Safe Email` as legitimate.

Because the texts have no headers, this dataset can only test the language-based part of the analysis. It cannot test authentication, sender comparison or attachment checks. The hand-crafted cases cover these parts.

## Hand-crafted test emails

The folder `evaluation/cases` contains `.eml` files that the team wrote by hand. Almost every file is a complete email with headers, so every check of the application can be tested. Only `elif-06` has no headers on purpose, because it tests a body that a user pasted without them. The cases numbered 01 to 18 are listed in `evaluation/martina_test_cases.json`. The cases starting with `elif` are listed in `evaluation/elif_test_cases.json`. Each entry describes the purpose of the case and the expected behaviour. The labels for the evaluation runner are in `evaluation/eval_cases.json`.

All suspicious senders and links use the reserved ending `.example`, which can never belong to a real organisation. Legitimate cases may use well-known real domains such as `hamk.fi` or `github.com`, but only with harmless content. The names in the emails are invented. The results of the online checks for the fictional domains are inserted with `evaluation/seed_cache.py`, so the evaluation does not depend on real lookups (see Decision 8 in `docs/project-decisions.md`). The `elif` cases test the agent and do not depend on these online checks. In replay mode their domain checks report unavailable.

## Data that is not committed

Real email corpora contain real names and addresses. Any such data, and any private sample a team member wants to try, belongs in `data/raw`. This folder is listed in `.gitignore` and is never committed.

The case history and the evaluation cache live in PostgreSQL and not in files. The case history stores only fingerprints, which are the sender domain, the link domains, a hash of the normalised subject, the verdict, the risk score and the time. It never stores the body or the addresses of an email.

## Rules for this repository

We do not commit confidential data, real personal emails or personal data without consent. We do not commit files larger than 10 MB. We never store API keys, tokens or passwords in data files. Keys belong only in the local `.env` file, which is excluded by `.gitignore`.