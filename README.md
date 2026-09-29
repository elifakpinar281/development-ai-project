# SpotThePhish

Group project for the Development of AI Applications course (Autumn 2026).

## Team members

- Elif Akpinar (amk1020345@student.hamk.fi)
- Jaiesh Badireddy (amk1020036@student.hamk.fi)
- Riana Debre (amk1020017@student.hamk.fi)
- Martina Spiegel (amk1020344@student.hamk.fi)

## Problem

### Intended users

#### University Staff Members
Administrative staff, lecturers, researchers and other university employees regularly receive emails involving student information, accounts, invoices, documents and external organisations.
Keeping this information safe from phishing is essential. These users may notice that an email looks unusual, but they often lack the technical knowledge to inspect it more closely.

#### Students
Students regularly receive messages about account access, passwords, payments and other university services, which makes them frequent targets of phishing attacks.
They are responsible for their own accounts rather than for the organisation, but a compromised student account can still be used to attack others.

#### Employees
Employees who handle business email often have no technical expertise.
In smaller organisations without a security team, they have to judge suspicious emails themselves.
A successful phishing attack can expose credentials, financial information or customer data.

#### IT Support
IT support staff receive reports of suspicious emails from users.
Instead of inspecting every reported email from scratch, they can use SpotThePhish to obtain a first assessment with supporting evidence.

### Problem statement
Phishing is one of the most common entry points for cyber attacks.
It works because the final decision is made by a person who cannot see the technical warning signs.
Phishing messages imitate organisations, manipulate users and steal sensitive information using techniques such as deceptive hyperlinks, domain impersonation and malicious attachments.

The information needed to judge an email is spread across places that users rarely look at:

- SPF, DKIM and DMARC authentication results
- inconsistencies between the From, Reply-To and Return-Path headers
- differences between the displayed link text and the actual link destination
- suspicious or newly registered domains
- suspicious attachment names and extensions
- external reputation information about URLs and domains

Conventional email filters run such checks automatically, but they give little or no explanation for their decisions and some phishing messages still get through.
At that point, a person has to make the decision.

SpotThePhish addresses this with an interactive, evidence-based analysis of a single email.
Instead of a bare binary classification, it identifies the relevant technical and linguistic indicators and derives a recommendation from them.

### Why AI is appropriate
Phishing analysis combines technical and linguistic analysis.
SpotThePhish therefore combines an LLM with deterministic processing instead of relying on the language model alone.

#### Email analysis stays deterministic
Attributes such as From, To, Reply-To, Subject, URLs, attachments and authentication results are extracted into a defined data schema and analysed with deterministic code.
For example, From and Reply-To can be compared directly, URLs can be inspected for suspicious characters and domains can be checked against external or stored information.
These results are facts, not guesses.

#### Adaptive analysis
Different emails require different checks.
A message with a suspicious link needs URL and domain analysis, while an urgent payment request needs sender and authentication analysis.
The model looks at the email and the results so far and decides which checks are relevant, so not every check has to run on every email.

#### Language and interpretation
Phishing relies on social engineering such as urgency, authority, threats and requests for credentials or payment.
These patterns are phrased differently in every email and cannot be captured reliably with fixed keyword rules, but an LLM can recognise them. The LLM also combines the technical findings with its language analysis and explains the result in plain language, because raw security results are not understandable to non-specialist users.

## Solution

SpotThePhish is a local web application in which the user pastes an email or uploads an `.eml` file. An investigation agent analyses the email, decides which security checks to run, observes the results and stops when it has enough evidence.
The application then returns:

- a verdict: phishing, suspicious or likely safe
- a risk score from 0 to 100
- a list of evidence items, each linked to the check that produced it
- a plain-language explanation and a recommended action
- the full investigation trace, showing which checks were run and why

The model proposes the next action and the final assessment.
The application code controls which tools exist, validates every tool call, limits the number of steps and applies safety rules that the model cannot override.

### Scope

**In scope**
- Analysis of a single email (pasted raw text or `.eml` upload), mainly in English.
- Read-only security checks. No links are opened in a browser and no attachments are executed.
- Explained verdict with evidence and investigation trace.
- Evaluation on public phishing and legitimate email datasets.

**Out of scope**
- Mailbox integration or automatic actions such as deleting, blocking or reporting emails.
- Visiting live phishing pages or scanning attachment contents for malware.
- Replacing enterprise email filters. SpotThePhish is a second-opinion assistant for the moment a user is unsure.

## Main user workflow

1. **User input:** The user pastes an email or uploads an `.eml` file in the Gradio interface.
2. **Validation and parsing:** The service layer (`src/services/ai_service.py`) rejects empty or non-email input, then deterministically parses sender, reply-to, return-path, authentication headers, subject, body text, links and attachment metadata.
3. **Investigation loop:** The model (called through the model client and Ollama) receives the parsed email as untrusted data and selects the next check from the allowed tools. The application validates the request, executes the tool and adds the observation to the investigation state. This repeats until the model finishes or the step limit (6 tool calls) is reached.
4. **Verdict validation:** The model's final report is validated against a Pydantic schema. Every evidence item must reference an actual tool observation, and deterministic rules enforce minimum risk levels (for example, a URL listed as malicious by a reputation service always results in phishing).
5. **Result:** The UI shows the verdict, risk score, evidence, explanation, recommendation and investigation trace. The validated verdict is stored in the local case history.

If the model fails (invalid output, timeout, Ollama unavailable), the application falls back to a fixed pipeline that runs all offline checks and shows a rule-based result, clearly labelled as a fallback.

## Architecture

Below is the initial planned architecture. It will be kept up to date in [`docs/architecture.md`](docs/architecture.md).

```text
User
  ↓ paste email / upload .eml
Gradio UI (app/ui.py)
  ↓ shows verdict, evidence, explanation, investigation trace
Application / AI Service (src/services/ai_service.py)
  | → Email Parser (src/email_parser.py) for deterministic parsing
  | → Investigation Agent (src/capabilities/agent.py): observes, decides and acts, proposes actions, maximum of 6 tool calls
  | → Tool Registry (src/capabilities/tools.py):
  |    - check_email_authentication (SPF, DKIM, DMARC, From, Reply-To, Return-Path)
  |    - analyze_links (target URL compared against displayed text, IP addresses, shorteners, punycode)
  |    - check_lookalike_domain (edit distance)
  |    - check_domain_age (RDAP registration information)
  |    - check_url_reputation (external reputation service)
  |    - inspect_attachments (attachment metadata, suspicious extensions)
  |    - lookup_case_history (earlier verdicts for the same domains / campaign)
  | → Case History Store (src/capabilities/memory.py): SQLite, local only
  | → Verdict Validator (src/schemas/responses.py): Pydantic validation, evidence grounding, safety rules
  ↓
Model Client (src/models/model_client.py)
  ↓
Ollama (Local LLM Server) -> qwen3:8b
```

> **Core Architectural Rule:** The user interface must NEVER communicate directly with the model client or Ollama. All interactions must pass through the service layer (`ai_service.py`).

- **UI isolation:** The UI only calls the service layer, which handles orchestration.
- **The application owns execution:** The model can only propose actions from a fixed list of read-only tools. Arguments are validated before execution, and invalid requests are rejected and reported back to the model as an observation.
- **Email content is untrusted data:** Emails are passed to the model in delimited data blocks. Instructions inside an email are treated as a phishing signal, not as instructions.
- **Safe by design:** No tool opens a URL in a browser, downloads a page or executes an attachment.
- **External APIs are optional:** If an API key is missing or a service is down, the tool returns "unavailable", the agent continues with the remaining evidence and the UI states which checks could not be run.

## Model

- **Model used:** `qwen3:8b`, running locally through Ollama (fallback for weaker hardware: `qwen3:4b`)
- **Selection rationale:**
  - **Privacy:** Emails may contain personal and confidential data. Running the model locally through Ollama keeps the email and its analysis on the user's machine instead of sending them to a third-party LLM API.
  - **Tool calling and structured output:** The agent loop depends on reliable JSON actions and a schema-conformant final report. Qwen3 supports tool calling and structured output in Ollama.
  - **Reasoning capacity:** The agent must combine several pieces of evidence rather than classify an email in one step. The 8B model offers more capacity for this than smaller models while still running on typical laptops with 16 GB RAM.
  - **Configurability:** The model is set through environment variables, so `qwen3:8b` and `qwen3:4b` can be compared without changing the application.

## Additional AI capability

- [ ] RAG (Retrieval-Augmented Generation)
- [x] Tools / External API integration
- [ ] Model Context Protocol (MCP)
- [x] Agentic workflow (Model-selected actions based on observations)
- [x] Memory / Persistent state
- [ ] Multimodal interaction (Text + Images)
- [ ] Other: ______________________

### Capability justification

#### Tools
An LLM alone cannot know whether a domain was registered yesterday, whether DKIM verification failed or whether a URL is on a blocklist. It would guess, and a guessed security verdict is dangerous.
The tools provide verifiable facts, and the model's job is to interpret and explain them.

#### Agent
Phishing emails attack in different ways. Some rely on a malicious link, some on a fake sender identity and some on a dangerous attachment.
Which checks are useful therefore cannot be decided in advance:

- **The relevant checks depend on the email.** Link checks are pointless for an email without links and attachment checks are pointless without attachments.
- **Each result changes what to check next.** A suspicious finding calls for a deeper check, while a clear finding makes further checks unnecessary.

A fixed workflow would run every check on every email. This is slower, sends more data to external services and fills the explanation with irrelevant findings. The agent selects only the relevant checks and stops as soon as the evidence is clear. The application code still decides which tools are allowed, limits the number of steps and applies the final safety rules.

This claim will be tested: the evaluation compares the agent with a fixed pipeline and an LLM-only baseline (see [Evaluation](#evaluation)).

#### Memory / persistent state
Phishing arrives in campaigns, where the same email reaches many people in an organisation.
SpotThePhish stores a compact record of every validated verdict locally in SQLite.
The agent can query it with the `lookup_case_history` tool: if the same sender domain, link domain or subject pattern was already judged phishing, the agent can finish with fewer checks and the result tells the user that this is a known campaign.

The history only stores what is needed for matching (domains, subject fingerprint, verdict, timestamp), not full email bodies.
A previous verdict is treated as evidence, not as an automatic answer, so a wrong earlier result cannot silently repeat itself.
This capability is built after the agent core works, so it cannot endanger the beta milestone.

#### Why not RAG
The needed knowledge is not in a document collection. It comes from the email itself and from live checks, so tools are the appropriate capability.

## Setup

### 1. Create the Conda environment

```bash
conda env create -f environment.yml
```

### 2. Activate the environment

```bash
conda activate dev-ai-project
```

### 3. Configure environment variables

Copy `.env.example` to create your local `.env` configuration file:

On Linux / macOS:
```bash
cp .env.example .env
```

On Windows (Command Prompt / PowerShell):
```powershell
copy .env.example .env
```

Ensure `.env` contains valid values for `OLLAMA_BASE_URL` and `MODEL_NAME`:
```env
OLLAMA_BASE_URL=http://localhost:11434
MODEL_NAME=qwen3:8b
```

Never commit `.env`. It is already excluded by `.gitignore`.

### 4. Start Ollama and pull the model

Make sure Ollama is installed and running locally, then pull the model:

```bash
ollama pull qwen3:8b
```

On computers with less memory, use `qwen3:4b` and set `MODEL_NAME=qwen3:4b` in `.env`.

### 5. Run the application

Run the application from the root directory of the project:

```bash
python -m app.main
```

Then open your browser at `http://localhost:7860`.

### 6. Run automated tests

```bash
pytest
```

## Evaluation
This section describes the planned methodology. Results will be added to [`evaluation/evaluation_results.md`](evaluation/evaluation_results.md).

The evaluation answers three questions:

1. **Does SpotThePhish detect phishing reliably?** Precision, recall, F1 and false positive rate on a labelled dataset.
2. **Is the agent justified?** Three configurations are compared on the same emails.
3. **Does it behave safely in difficult and failure situations?** Test cases with expected behaviour, recorded as pass, partial or fail.

### Dataset
The evaluation uses three data sources, because each one covers a different part of the application:

| Source | Content | Used to evaluate |
|---|---|---|
| [Phishing validation emails dataset](https://doi.org/10.5281/zenodo.13474746) (Miltchev et al., 2024, CC BY 4.0) | 2,000 labelled emails, text only (no headers) | Language-based detection, LLM-only baseline, false positive rate |
| Nazario phishing corpus and SpamAssassin public corpus (raw emails) | Real phishing emails (Nazario) and legitimate emails (SpamAssassin) with sender, receiver, subject, body and extracted URLs | Sender and link checks on real emails, false positive rate on real legitimate mail |
| Hand-crafted `.eml` cases (planned) | About 20 modern attack examples written by the team, using fictional `.example` domains, with full headers and attachment metadata | Authentication and attachment checks, modern attack types, difficult and adversarial cases |

The validation dataset contains only email text, so it cannot test header, link or attachment checks. The raw corpora add real senders and links but no authentication headers or attachments, and many of their domains no longer exist. The hand-crafted cases cover the remaining checks. 
Reputation and domain-age results change over time, so tool responses for the evaluation set are cached and stored with the results.

The raw dataset contain real names and email addresses, so they are kept locally in `data/raw/` and excluded from the repository via `.gitignore`. 
Dataset sources and licenses will be documented in [`data/README.md`](data/README.md). 
No real personal emails are committed to the repository.

### Compared configurations

| Configuration | Description |
|---|---|
| LLM-only | The model classifies the email without any tools |
| Fixed pipeline | All tools run on every email, then the model writes the verdict |
| Agent (SpotThePhish) | The model selects the checks step by step and stops when the evidence is clear |

### Metrics

| Aspect | Measure |
|---|---|
| Detection quality | Precision, recall, F1 and false positive rate for phishing |
| Efficiency | Average tool calls and latency per email |
| Evidence grounding | Share of evidence items that match an actual tool observation (manual check on 30 results) |
| Explanation quality | Manual rating (clear, correct, actionable) on a sample of results |
| Case history | For repeated campaign emails: correct match rate and reduction in tool calls compared with the first report |

A false negative (phishing classified as safe) is the most harmful error, so **recall on phishing is the primary metric**.

### Test cases

Behaviour-focused test cases will be added to [`evaluation/test_cases.json`](evaluation/test_cases.json) during development. They are planned in three categories:

- **Successful cases:** typical phishing types (credential link, CEO fraud, malicious attachment) and a normal legitimate email.
- **Difficult cases:** alarming but legitimate emails (real password resets), phishing from compromised legitimate accounts, emails without headers, non-English emails.
- **Failure and adversarial cases:** prompt injection inside the email, empty input, non-email text, Ollama not running, missing API key, invalid tool calls proposed by the model.

Each case will record the expected behaviour, the actual result and a status (pass, partial or fail).

## Known limitations

- The phishing validation dataset contains only email text and is partly artificially generated, so it cannot test header, link or attachment checks, and results on it may not fully reflect real emails.
- Older public corpora contain many links and domains that no longer exist, so live reputation and domain-age checks cannot be evaluated on them. The hand-crafted cases partly compensate for this.
- Emails pasted without headers cannot be checked for authentication results, which weakens the assessment.
- Phishing sent from compromised legitimate accounts passes authentication and domain checks, so detection then depends mainly on the language analysis.
- A local 8B model may produce invalid actions or miss subtle manipulation. The validation, step limits and fallback pipeline limit the impact, but they do not eliminate errors.

## Future improvements

- Browser or mail-client extension for one-click analysis
- Reporting system for IT support
- Organisation-specific allowlists of known senders, built on the case history

## Presentation video

The link will be added before the final submission (22 October 2026).