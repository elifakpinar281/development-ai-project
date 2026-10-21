# Evaluation

The evaluation answers three questions. 

1. Does SpotThePhish detects phishing reliably?
2. Is the agent justified compared with simpler designs?
3. Does the application behave safely in difficult and failure situations?

The design of the evaluation is explained in Decisions 8 and 14 of [docs/project-decisions.md](../docs/project-decisions.md).

## Compared configurations

The same emails go through three configurations.

| Configuration | What it does | Code |
|---|---|---|
| agent | The model chooses the checks step by step. This is the application. | `src/capabilities/agent.py` |
| fixed_pipeline | Every check runs on every email, then the model decides once | `src/capabilities/baselines.py` |
| llm_only | The model decides from the email alone, without any checks | `src/capabilities/baselines.py` |

All three use the same prompts, the same verdict validation and the same safety rules. They also run through the same AnalysisService as the app. Differences in the results therefore come only from how the evidence is collected.

## Cases

There are two kinds of input.

The hand-crafted cases are .eml files in the folder cases. Their ground truth is listed in eval_cases.json. Every entry has an id, the file, the label phishing or legitimate, a category and a short note. To add a case, put the .eml file into the folder and add an entry to the list. The false positive rate can only be computed when there are legitimate cases as well.

The Zenodo sample is drawn from `data/phishing_validation_emails.csv`. It is stratified, so half of it is phishing and half is legitimate, and the seed makes it repeatable. These emails have no headers, so they test the language-based part of the analysis.

## Behaviour test cases

Besides the labelled cases, every team member documents behaviour test cases with the expected behaviour, the actual result and a status of pass, partial or fail. These are martina_test_cases.json and elif_test_cases.json. Some of these cases are checked by a run with the real model. Others, such as Ollama not running or invalid model output, are covered by an automated test, which is named in the entry.

## Running the evaluation

Start Ollama and the database first, as described in the main README. Then run the commands from the project folder.

All hand-crafted cases in all three configurations:

```bash
python -m evaluation.run_evaluation
```

Additionally 20 emails from the Zenodo dataset, 10 phishing and 10 legitimate:

```bash
python -m evaluation.run_evaluation --zenodo 20
```

A quick check with one configuration and the first two cases:

```bash
python -m evaluation.run_evaluation --configs agent --limit 2
```

Reproducible online checks. The cache is seeded once and then replayed:

```bash
python -m evaluation.seed_cache
python -m evaluation.run_evaluation --cache replay
```

The option suspicious-as decides how a suspicious verdict is counted. The default is phishing, because the user is then warned. The option write-history is off by default, so earlier runs cannot influence later ones. Only the repeated campaign case 05-campaign-repeat needs it, and 04-campaign-first must be analysed first. Further options are no-domain-server, seed and output.

Without a graphics card, qwen2.5:3b needs about a minute per email and configuration, and qwen3:8b around three minutes. Start with a few cases.

To look at single emails in detail, the script try_agent prints the verdict, the evidence, the warnings and the full trace:

```bash
python -m scripts.try_agent "evaluation/cases/19-*.eml"
```

## Grading the behaviour test cases

The script grade_cases runs every test case that has an .eml file and grading rules, and checks the rules automatically. A case passes only when every rule holds. When the model fails and the offline fallback answers, the case is marked partial at best.

```bash
# cases 19 to 30
python -m evaluation.grade_cases --cases evaluation/elif_test_cases.json

# the cases of Person 3
python -m evaluation.grade_cases --cases evaluation/martina_test_cases.json

# selected cases, three runs each to check that the results are stable
python -m evaluation.grade_cases --only case-21 case-25 --runs 3
```

Cases with a special scenario, such as a stopped database, and failure cases without an .eml file are listed as not run. They are covered by automated tests or need a manual setup. The script writes results.md and results.json to a new folder results/grading-date-time. The case history is not written during grading.

## Results

Every run creates a new folder results/date-time with four files.

| File | Content |
|---|---|
| `summary.md` | Tables for detection quality, efficiency and reliability and the result of every case |
| `metrics.json` | All metrics per configuration and the settings of the run |
| `records.csv and records.jsonl` | One row per case and configuration with verdict, risk, latency, model calls, tool calls, safety rules and unavailable checks |

The tables of summary.md are copied into [evaluation_results.md](evaluation_results.md). The run that is reported there is also committed, so every number can be traced back.