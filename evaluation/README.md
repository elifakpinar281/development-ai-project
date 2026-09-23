# Evaluation Framework

Systematic evaluation is essential for assessing whether your AI application reliably solves your target user's problem.

## How to Conduct Evaluation

Students are expected to define and run manual or script-assisted evaluations against representative test cases.

### Test Case Categories

Your evaluation dataset in [`test_cases.json`](test_cases.json) should cover three primary categories:

1. **Successful Cases (Typical Inputs):** Standard queries or inputs that your application is designed to handle successfully under normal operating conditions.
2. **Difficult Cases (Edge Cases):** Ambiguous queries, long inputs, unusual domain terms, or edge cases that test model robustness and retrieval/tool quality.
3. **Failure / Adversarial Cases:** Empty inputs, malformed requests, out-of-scope questions, or connection drops that verify controlled error handling.

## Evaluation Process

1. **Define Test Cases:** Populate `test_cases.json` with realistic inputs and expected behaviors tailored to your user problem.
2. **Execute System:** Run each test case through your application interface.
3. **Log & Review:** Record `actual_result` and assign a `status` (`pass`, `fail`, `partial`).
4. **Summarize Results:** Synthesize findings in [`evaluation_results.md`](evaluation_results.md).
