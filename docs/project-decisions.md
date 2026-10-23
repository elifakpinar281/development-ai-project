# Project decisions

This log records the important technical decisions of the team. Every decision follows the same structure. It states what we decided, which alternatives we considered, why we chose this option and which trade-offs we accept.

| No. | Decision | Area |
|---|---|---|
| 1 | Model selection | Model |
| 2 | Additional AI capability | Capabilities |
| 3 | Architecture | Architecture |
| 4 | Domain tools as an MCP server | Tools |
| 5 | Case history in PostgreSQL | Memory |
| 6 | Handling external-service failures with custom exceptions | Tools |
| 7 | Domain checks: heuristics, services and thresholds | Tools |
| 8 | Reproducible evaluation with an evaluation cache | Evaluation |
| 9 | Structured output, temperature 0, thinking off | Model |
| 10 | Design of the agent loop | Agent |
| 11 | Tool arguments must come from the email | Agent |
| 12 | Grounded evidence, score bands and safety rules | Agent |
| 13 | Model exceptions and an offline fallback | Agent |
| 14 | Three configurations for the evaluation | Evaluation |
| 15 | Deterministic email parsing and safe metadata inspection | Parser & Tools |
 


## Decision 1 — Model selection

**Decision:**
We use `qwen2.5:3b`, running locally through Ollama. The model is set through the `MODEL_NAME` environment variable, so `qwen3:8b` can be used instead without any code change.

**Alternatives considered:**
- Cloud-hosted LLM APIs (e.g. OpenAI, Anthropic, Gemini)
- `qwen3:8b` as the main model (our first choice)
- No LLM at all, only rule-based checks

**Why we chose this:**
- **Privacy:** Emails can contain personal and confidential data. Running the model locally keeps the email and its analysis on the user's machine instead of sending it to a third-party API.
- **Waiting time:** We started with `qwen3:8b`. On our laptops without a graphics card, one analysis took around 3 minutes, because the agent needs several model calls. A user who wants to know whether to click a link will not wait that long. `qwen2.5:3b` needs about 60 seconds for the same analysis.
- **The application does the safety work:** The facts come from deterministic tools, every decision is validated, every evidence item must be grounded and the safety rules cannot be overridden. The model mainly chooses checks, reads the language of the email and explains the result, so a small model is sufficient. The larger model still judges difficult emails better, so the choice is a trade-off between speed and accuracy (see Evaluation below).
- **Structured output:** Our agent loop needs reliable JSON decisions and a schema-conformant final report. Qwen2.5 and Qwen3 both support structured output in Ollama.
- **Configurability:** Because the model is set via environment variables, we can compare `qwen2.5:3b` and `qwen3:8b` without changing the application.
- **Evaluation:** We ran our test emails with both models and the same code. `qwen3:8b` passed 9 of 10 without a false alarm, `qwen2.5:3b` 6 of 10, but the 8B model was about 1.5 to 2 times slower. A later repetition with three runs per email and an empty case history confirmed this: `qwen3:8b` judged all ten emails correctly, while `qwen2.5:3b` gave a wrong verdict in 5 of 29 runs. The 3B model stays the default for laptops without a graphics card, and `qwen3:8b` is recommended when the hardware allows it.

Trade-off: a small model can produce invalid actions or miss subtle manipulation more often than a larger one. We limit this with schema validation, a step limit, grounding checks and the offline fallback (see Decisions 10 to 13).

---

## Decision 2 — Additional AI capability

**Decision:**
We combine three capabilities:
1. **Tools / external API integration:** seven read-only tools (email authentication, link analysis, lookalike domains, domain age via RDAP, URL reputation, attachment inspection, case history lookup).
2. **Agentic workflow:** the model chooses the next check based on what it has observed, with a maximum of 6 tool calls.
3. **Memory / persistent state:** a case history of validated verdicts in PostgreSQL, queried through the `lookup_case_history` tool (see Decision 5).
4. **Model Context Protocol (MCP):** the four domain and memory tools are exposed through an MCP server (see Decision 4).

**Why it is needed:**
- **Tools:** An LLM alone cannot know whether a domain was registered yesterday, whether DKIM failed or whether a URL is on a blocklist. It would guess, and a guessed security verdict is dangerous. Tools provide verifiable facts, and the model interprets and explains them.
- **Agent:** Phishing emails attack in different ways (malicious link, fake sender, dangerous attachment), so the useful checks depend on the email and on each previous result. A fixed workflow would run every check on every email, which is slower, sends more data to external services and fills the explanation with irrelevant findings. The agent picks only relevant checks and stops when the evidence is clear.
- **Memory:** Phishing arrives in campaigns. If the same sender domain, link domain or subject pattern was already judged phishing, the agent can finish with fewer checks and tell the user it is a known campaign. Only matching fingerprints are stored (no full email bodies), and a previous verdict counts as evidence, not as an automatic answer, so a wrong earlier result cannot silently repeat.

**Alternatives considered:**
- **RAG:** Rejected. The needed knowledge is not in a document collection. It comes from the email itself and from live checks, so tools fit better.
- **MCP:** Used for the tools that depend on external systems (RDAP, a reputation service, a database). The email-analysis tools stay direct in-process tools. See Decision 4 for the reasoning.
- **LLM-only classification:** Cannot verify technical facts and may hallucinate them. We keep it as a baseline in the evaluation.
- **Fixed pipeline (all tools on every email):** Simpler, but slower and less focused. It is also an evaluation baseline, so we can test whether the agent is actually justified.
- **Stateless analysis (no memory):** Simpler, but every repeated campaign email would be analysed from scratch.

---

## Decision 3 — Architecture

**Decision:**
A layered architecture: Gradio UI (`app/ui.py`) → service layer (`src/services/ai_service.py`) → email parser, investigation agent, tool registry, case history store and verdict validator → model client → Ollama. Technical analysis is deterministic, and the LLM interprets and explains the findings.

**Why:**
- **UI isolation:** The UI only calls the service layer and never touches the model client or Ollama. This keeps the UI independent of model details and makes each layer easier to test and replace.
- **The application owns execution:** The model can only propose actions from a fixed list of read-only tools. The application validates the arguments, executes the tool and limits the steps. Invalid requests are rejected and reported back to the model as an observation.
- **Email content is untrusted data:** Emails are passed to the model in delimited data blocks. Instructions inside an email (e.g. "ignore previous instructions") are treated as a phishing signal, not as commands.
- **Safe by design:** No tool opens a URL, downloads a page or executes an attachment.
- **Deterministic grounding:** Parsing, authentication results, link inspection and edit distance are done in Python, so these results are facts and not guesses.
- **Verdict validation:** The final report is checked against a Pydantic schema, every evidence item must reference a real tool observation, and safety rules enforce minimum risk levels (e.g. a URL flagged as malicious always results in phishing).
- **Graceful degradation:** If Ollama fails, or an external API is unavailable or has no key, the app does not crash. It uses an offline fallback pipeline or an "unavailable" observation, and the UI clearly labels this.


---

---

## Decision 4 — Domain tools as an MCP server

**Decision:**
The four domain and memory tools (`check_lookalike_domain`, `check_domain_age`, `check_url_reputation`, `lookup_case_history`) run in their own MCP server (`src/mcp_servers/domain_server.py`, stdio transport). The investigation agent connects as an MCP client, discovers the tools with `list_tools()` and calls them with `call_tool()`. The email-analysis tools (authentication, links, attachments) stay direct, in-process tools. The agent merges both sets into one tool list.

**Alternatives considered:**
- **All tools as direct Python functions:** Simplest option, with no extra process. But the domain tools are the ones that talk to external systems (RDAP, a reputation service, a database), and our course material names exactly this case as the reason to use MCP.
- **All tools, including the email tools, over MCP:** Would be uniform, but the email tools have no external dependency. A protocol hop for pure in-process functions would add latency and complexity without any benefit.
- **A custom HTTP service (FastAPI):** More work, no standard discovery format, and the agent would need custom client code.

**Why we chose this:**
- **Fits the course criterion:** Lecture 5 recommends MCP when external systems expose capabilities and standard discovery is valuable, and a direct tool when the capability belongs inside the application. Our two groups of tools match these two cases.
- **Decoupling:** The agent does not import the domain tool code. It receives the tool names, descriptions and input schemas from the server. The tools can be changed, tested and run without the agent.
- **Least privilege:** The server exposes read and check operations only. Writing to the case history (`save_verdict`) is deliberately not a tool. Only the application writes after the verdict has been validated, so the model cannot manipulate the history (Lecture 5/6: the application controls execution and permissions).
- **The application still validates:** MCP standardises discovery and calls, but the application checks the tool name and arguments, and the tools validate their inputs again. For `lookup_case_history` the application, not the model, fills the arguments from the parsed email.
- **Controlled failure:** If the server cannot be started or a call times out, the client returns a `ToolResult` with status `unavailable` instead of raising an error (see Decision 6).

**Trade-offs:**
- The server is an additional process, started once per investigation. This costs start-up time per run.
- The SDK's default environment for child processes drops variables such as `DATABASE_URL`, so the client passes the environment on explicitly.
- We pin `mcp>=1.2,<2`. Version 2 of the SDK renamed the server API, and our code and the course examples use the v1 API.
- The lookalike check needs no network, so the application also runs it directly in process before the model starts (observation 3, same function as the MCP tool), and the offline fallback uses it too. The model can still call the MCP tool for single domains. In the first evaluation run the model skipped the check in 2 of 8 valid runs on lookalike cases, although it is the cheapest and most reliable check.
- **Tool descriptions are the model's only guide:** The prompt lists every tool by name and description, and for MCP tools the description is the docstring of the server function. A review found that the four domain tools had no docstring, so the model saw only their names. In the first evaluation run it skipped the lookalike check in 2 of 8 valid runs on lookalike cases, which this probably contributed to. Each tool now has a short description of what it checks and when it is useful, and a test checks that every tool of the server has one.

---

## Decision 5 — Case history in PostgreSQL

**Decision:**
Validated verdicts are stored as fingerprints in a PostgreSQL table (`case_history`): sender domain, link domains, a hash of the normalised subject, verdict, risk score and timestamp. The agent can query it with the read-only tool `lookup_case_history`. The table never contains the email body or addresses of recipients.

**Alternatives considered:**
- **SQLite (our original plan):** Zero setup. But the case history is read by the MCP server process and written by the application process, and the evaluation needs one shared, resettable database for all team members.
- **No memory:** Simpler, but a campaign that reaches many users would be analysed from scratch every time.
- **Storing the full email:** Would allow better matching, but emails contain personal data. Fingerprints are enough for matching and are the minimum needed.
- **A vector store for similar emails:** This would be RAG-style retrieval over past emails. It needs the email text and is more than the problem requires.

**Why we chose this:**
- **Lifetime of the information:** Lecture 2 and 6 distinguish session state from persistent memory. A verdict about a campaign should outlive a session and be usable by later analyses, so it belongs in persistent storage.
- **Privacy by design:** Only matching fingerprints are stored, and a test checks that the table has no body column and that body text never appears in the stored data.
- **Campaign matching that works:** The subject is normalised before hashing (lower case, reply prefixes removed, digits replaced). "Parcel 8841 could not be delivered" and "RE: Parcel 5530 could not be delivered" produce the same fingerprint, so a campaign with changing tracking numbers is recognised. Link domains of well-known brands are ignored for matching, because they appear in countless legitimate emails. The same applies to the sender domain: sender domains of well-known organisations (`KNOWN_BRANDS`) and of free-mail providers are neither stored nor matched. The evaluation showed why. A gift card scam sent from a real `hamk.fi` account was stored as phishing, and the case history then reported earlier phishing cases for `hamk.fi` when a genuine course announcement arrived, which was rated phishing (see evaluation_results.md). A hacked university account would otherwise make every later university email look suspicious.
- **A previous verdict is evidence, not an answer:** The tool returns a finding and a risk contribution, and the agent still decides. A wrong earlier verdict therefore cannot silently repeat itself. A match on sender domain or subject pattern with an earlier "phishing" verdict counts more (risk 70) than a link-domain match alone (risk 55).
- **Fallback verdicts are not stored:** They are rule-based and less reliable, and storing them would spread their errors.
- **Why PostgreSQL:** The history and the evaluation cache are shared state used by several processes and several team members. PostgreSQL gives concurrent access, array columns with a GIN index for link-domain matching, and JSONB for cached results. Docker Compose makes the setup reproducible. Separate databases (`spotthephish`, `spotthephish_eval`, `spotthephish_test`) keep development data, evaluation runs and tests apart.

**Trade-offs:**
- Docker (or a local PostgreSQL) is now required to run the full application.
- If the database is unavailable, `lookup_case_history` returns `unavailable` and saving a verdict raises a controlled `DatabaseUnavailableError`. The analysis itself continues without the history.
- The history can only be as good as past verdicts. Campaign matching by subject can also produce false matches for generic subjects. In the evaluation, the repeated-campaign case `martina-05` needed a median of 5 tool calls, the same as the first email of the campaign (`martina-04`), so we cannot claim that the case history reduces the number of checks. The model also used it unreliably: the lookup was observed with a warning in only 1 of 3 runs of `martina-05`. In the harmless email `martina-11`, which shares its subject pattern with an earlier phishing case, the model never called the lookup, so the false match for generic subjects was not exercised. The history also depends on the model completing an analysis and saving a validated verdict, because fallback verdicts are not stored.
- Verdicts from the offline fallback are not stored, so the case history is only filled when the AI model was used. The repeated-campaign evaluation cases therefore need the model.
---

## Decision 6 — Handling external-service failures with custom exceptions

**Decision:**
Failures of the domain tools are modelled as custom exceptions (`src/exceptions.py`). Internal code raises specific exceptions such as `ServiceTimeoutError`, `RateLimitError`, `MissingApiKeyError`, `ServiceResponseError`, `DatabaseUnavailableError` or `InvalidDomainError`. A single decorator, `tool_boundary`, catches them and converts them into a `ToolResult`. A tool function therefore never raises to its caller.

**Alternatives considered:**
- **Returning error `ToolResult`s everywhere by hand:** Every tool repeats the same try/except blocks, and it is easy to forget one case.
- **Generic exceptions (`Exception`, `RuntimeError`):** Hard to tell apart, and raw error messages might reach the user.
- **Letting exceptions propagate to the agent:** Would crash the investigation because of one unavailable service.

**Why we chose this:**
- **Controlled failure states (Lecture 2):** Each exception knows its result status. Invalid input maps to `error`. A service that is down, rate-limited, missing a key or not reachable maps to `unavailable`. The agent receives an observation, continues with the remaining evidence, and the UI lists which checks could not be run.
- **Distinguishes causes:** "No API key", "rate limit" and "timeout" are different situations with different user-facing messages, which a single generic error would hide.
- **No secrets in messages, logs or configuration output:** The reason texts are fixed and safe to display, and only the exception type is logged. The Safe Browsing key is sent in the `x-goog-api-key` header, not in the URL. Our first version put it in the URL. The HTTP library logs every request URL at INFO level, and during an evaluation run the key appeared in the terminal output. We therefore changed to the header, set the `httpx` and `httpcore` loggers to WARNING (in the application and in the MCP server, which is a separate process), excluded the key and the database URL from the `repr` of the configuration object, and revoked and replaced the exposed key. Tests check that the key is sent in the header and not in the URL, that it does not appear in any result, that the loggers are silenced and that the configuration `repr` does not show secrets.
- **Failures are never cached:** Because failures raise, the evaluation cache cannot freeze a temporary outage as a result.
- **Unexpected bugs are contained:** Any other exception is caught at the same boundary and reported as `unavailable` with only its type name.
- **Input validation protects the services:** A domain is validated and normalised before it is placed into an RDAP request URL, and a URL must be a plain http/https URL. Invalid input never leads to a network request.

**Trade-offs:**
- We do not retry automatically. Each call is bounded by `HTTP_TIMEOUT` (default 5 seconds), and the agent has a budget of 6 tool calls. Retries would multiply the waiting time for the user. The course recommends bounded retries mainly for model output, and for our tools "unavailable" is the safer outcome.
- A missing optional service (for example no Safe Browsing key) lowers detection quality. The UI shows this, but it cannot be repaired by the application.

---

## Decision 7 — Domain checks: heuristics, services and thresholds

**Decision:**
- **Lookalike domains** are detected offline by comparing the domain with a list of well-known brands (including `hamk.fi`). The rules and their risk contributions are: character substitution such as `paypa1`, `amaz0n` or Cyrillic lookalike letters, also inside punycode domains (90); a brand name in a subdomain, for example `paypal.com.secure-id.net` (80, the full host name is examined because the registrable domain alone would hide this trick); an edit distance of 1 (85) or 2 (65) for names with at least 5 characters; the same name under a different domain ending (60); and a brand name of at least 4 characters embedded in a longer name such as `hamk-student-login` (60). Brand names that are also ordinary words (`office`, `apple`, `zoom`) only count as embedded or as a subdomain when the name also contains an account word such as `login`, `verify`, `secure` or `365`. So `lecture-office.example` is not flagged, while `office365-login.example` is.
- **Domain age** is read from RDAP. Domains younger than 7 days get a high risk contribution (90), under 30 days 75, under 90 days 45, under a year 10.
- **URL reputation** uses the Google Safe Browsing Lookup API when an API key is configured.

**Alternatives considered:**
- **Edit distance only:** Misses `paypal-secure-login.com` and character substitutions, and flags short names too often.
- **Machine-learning domain classifier:** Needs training data and is harder to explain. Our users need an explanation of the evidence, so a deterministic rule that states why a domain was flagged fits better.
- **WHOIS instead of RDAP:** Free-text formats that differ between registries and are hard to parse reliably.
- **Other reputation services:** Several require registration, too. Safe Browsing is widely used and has a free tier for non-commercial use.

**Why we chose this:**
- **Facts, not guesses:** Each rule produces a specific finding ("`paypa1.com` imitates `paypal.com` (character substitution)") that is shown as evidence and checked by the verdict validator.
- **Works offline:** The lookalike check needs no network, so the offline fallback can still use it.
- **No key needed for domain age:** RDAP works without registration, so the application remains useful without any API key. The reputation check is optional and reports `unavailable` when no key is configured.
- **Privacy:** Only a domain (RDAP) or a single URL (Safe Browsing) leaves the machine. The email text and addresses are never sent to these services.
- **Common threshold:** A result at or above a risk contribution of 40 is shown as a warning, below it as a normal result. A missing RDAP record is only a weak signal (25), because not every registry publishes RDAP data.

**Trade-offs:**
- The risk values and thresholds are our initial, hand-chosen settings. They are not statistically tuned. The evaluation showed that detection quality depends on the evidence available to the agent. When online checks are unavailable, the offline fallback can still detect lookalike domains, but it cannot reliably establish domain age or current URL reputation. The thresholds should therefore be treated as engineering choices rather than statistically calibrated probabilities of phishing. Future work should evaluate them against a larger, labeled dataset.
- The brand list is small and must be maintained. A legitimate company that shares a name with a listed brand can be flagged.
- Several limits of the external services are listed under "Known limitations".
- The first version of the rules produced false alarms: our brand list contains `op.fi`, and a two-letter brand name appears inside many normal words (`shop.fi`, `stop.com`), and a short name was also within edit distance 2 of almost any short domain. We therefore apply the "embedded name" rule only to brand names with at least 4 characters and the edit-distance rule only to names with at least 5 characters. A legitimate domain that merely contains a brand name (for example `hamkkeri.example`) is still flagged. We accept this because the tool only reports a similarity signal, and the verdict also depends on other evidence.
- Two rules changed after the evaluation. The offline fallback rated the harmless email from `lecture-office.example` as phishing, because a brand word inside a domain was scored 70, the phishing threshold. A brand word alone is a weaker signal than a character substitution, so it now scores 60 (suspicious), and generic words need an account word. A legitimate subdomain that contains a brand name (for example `github.company.example`) is still reported.

---

## Decision 8 — Reproducible evaluation with an evaluation cache

**Decision:**
The two online tools (domain age and URL reputation) can run in three modes, set with `EVAL_CACHE_MODE`: `off` (normal), `record` (call the real service and store successful results in PostgreSQL) and `replay` (answer only from the stored results). Our hand-written test emails use fictional `.example` domains, whose results are inserted deliberately with a seed script (`evaluation/seed_cache.py`). Evaluation runs use their own database.

**Alternatives considered:**
- **Evaluating against the live services:** Domain ages change every day, blocklists change, and the numbers of two runs would differ.
- **Mocking the tools in the evaluation:** Would not exercise the real tool code path and the MCP integration.
- **Using real phishing domains in the hand-written cases:** Unsafe, and the domains are often already taken down.

**Why we chose this:**
- **Repeatable results (Chapter 7):** A comparison between configurations (LLM-only, fixed pipeline, agent) is only meaningful if tool results are identical in all runs. The cache keeps the environment fixed and the configuration recorded.
- **Controlled test conditions:** For a case such as "newly registered domain", we need to decide that the domain is 2 days old. Seeding makes this explicit and reviewable.
- **Failure testing:** An empty cache in `replay` mode produces exactly the "service unavailable" situation that we want to evaluate for failure handling.
- **Shared across the team:** The cache lives in the evaluation database and can be exported as a SQL dump, so every team member obtains the same numbers on their own machine.
- **Honest separation of test types (Chapter 7):** Deterministic tests (the tools, the exceptions, the database, the MCP server) run on every change without a model. Live evaluation with the model is a separate step.

**Trade-offs:**
- The cache reflects the state at recording time. It shows how the application behaves with those results, not how accurate the live services are today.
- Seeded results for fictional domains are assumptions that we define ourselves. This is documented, and we do not use them to claim the quality of the real services.
- The history of earlier verdicts must be reset before a run (or disabled), otherwise earlier runs influence later ones. Only the repeated-campaign cases rely on the history on purpose.
- In replay mode the cache is stored in the same database as the case history. In the "database down" case, the cached tools are therefore unavailable too, while in normal operation only the case history would be affected.

---

## Testing approach for the domain tools and memory (Martina)

- **Deterministic tests (no model needed):**
  - Each tool: detected cases, clean cases and invalid input (empty, malformed, too long, path-like input, non-http URLs).
  - External failures simulated for each service: timeout, rate limit, HTTP error, unreadable response, missing API key, database unreachable.
  - Custom exceptions: hierarchy, status mapping, the boundary converting exceptions and containing unexpected bugs, no secret in any result.
  - Privacy: the case history has no body column and stores no body text.
  - Memory: campaign match through a subject variant, ignored brand links, fallback verdicts not stored, writing can be disabled, controlled error if the database is down.
  - Evaluation cache: record, then replay, cache miss, seeding.
  - MCP: the server lists exactly the four tools, each with a description, the write function is not exposed, the synchronous client works and unknown tools are rejected.
  - **Hand-crafted evaluation cases:** 21 cases in `evaluation/martina_test_cases.json` (12 `martina-*` cases for domain checks and case history, 9 `case-*` cases for attachments, prompt injection and fraud). Graders are code-based and critical cases are reported separately. Results: `evaluation/evaluation_results.md`.
  - Secrets: the Safe Browsing key is sent in a header and not in the URL, the `httpx` and `httpcore` loggers are silenced, and the configuration `repr` hides the key and the database URL.
  - Lookalike rules: a brand in a subdomain (also with confusable letters and inside a URL), generic brand words alone and together with an account word, and known subdomains such as `login.hamk.fi` as clean cases.
  - Memory: sender domains of well-known organisations are neither stored nor matched.

---

## Decision 9 - Structured output, temperature 0 and thinking off

**Decision:**
The application talks to the model through structured output in Ollama. Every request contains a JSON schema, AgentDecision for each step and ModelVerdict for the final answer, and Ollama limits the reply to that schema. The schema for a decision lists the real tool names, so the model cannot even write an unknown name. The schema for the verdict allows only two shapes of evidence. A tool item must carry the number of an observation that actually ran, and an email item must carry a quote of at least four characters. We added this after our evaluation showed that the 3B model often wrote evidence items without a quote or without an observation number (see evaluation_results.md). We do not use the native tool calling format of Ollama. The settings are temperature 0, thinking off for Qwen3 models (Qwen2.5 has no thinking mode), a context window of 8192 tokens and a timeout of 300 seconds per call. All settings can be changed in .env. All access to Ollama goes through one class, OllamaModelClient.

**Alternatives considered:**
With native tool calling, the model returns tool calls, but the final verdict is free text. Small local models sometimes write tool calls into the text instead, so we would need two parsing paths. JSON requested only in the prompt, without a schema, is simpler, but a small local model then produces invalid JSON much more often. Thinking mode improves reasoning on hard cases, but it makes every step several times slower, and an analysis needs several model calls.

**Why we chose this**
The schema prevents most format errors before they happen. The application still validates every answer with Pydantic, because a schema cannot check meaning. There is only one parsing path for steps and for the final answer. Temperature 0 makes repeated runs nearly identical, which keeps the evaluation fair. The agent already writes a short reason for each step, which gives a visible trace without thinking mode. Because the rest of the application depends only on the ModelClient interface, the tests can use a scripted fake model.

**Trade-offs**
Without thinking mode, the model may miss subtle cases that longer reasoning would catch. If a model version does not support the think setting, the client repeats the request once without it.

---

## Decision 10 - Design of the agent loop

**Decision:**
The AgentAnalyzer runs a loop in the ReAct style. The model decides and the application acts. The flowchart is in section 4 of architecture.md.

1. The application runs the prompt injection scan, the payment fraud scan and the offline lookalike check, which become observations 1 to 3.
2. In each step, the model returns either a tool call with its arguments and a one-sentence reason, or the signal to finish.
3. The application checks the call (Decision 11), runs it and returns the result as a numbered observation.
4. After finishing, or when the budget is used up, the model writes the final verdict in a new conversation that contains only the email and the results of the checks, not its own reasons from the investigation. The verdict is then validated (Decision 12).

**Rules the application enforces:**

- The budget is six steps. Every decision that is not a finish uses a step, including refused and repeated calls. The loop therefore always ends.
- The same tool with the same arguments is not run twice. The model is pointed to the earlier observation instead.
- An invalid answer gets exactly one correction attempt. A second invalid answer ends the run with a ModelError, and the service switches to the fallback.
- The verdict is requested in a fresh context. During the investigation the model writes a reason for every step, for example "the email contains a suspicious link". In the first version the verdict was requested in the same conversation, so the model read its own guesses again and tended to confirm them. The team evaluation showed a false-positive rate of 0.50 for the agent against 0.00 for the fixed pipeline, which decides with only the email and the check results. The agent now judges the same way, so the investigation chooses the evidence and a separate step weighs it. In the next evaluation run the false-positive rate of the agent dropped from 0.50 to 0.00, but its recall dropped from 1.00 to 0.84. The four missed phishing emails were all rated likely safe with a risk of 39, the highest score of that band, which shows that the small model was unsure. We keep the change, because a false alarm on every second genuine email makes the tool unusable, and we list the lower recall as a known limitation.
- The arguments of lookup_case_history are always filled in by the application from the email. The model only decides whether to run it.
- Every analysis creates a fresh run object, so one email can never influence the next one.

**Why we chose this**

The agent is the capability we argue for, so the application must stay in control of what runs and when it stops. Counting refused calls prevents endless loops and keeps the longest possible waiting time bounded. The trace records every step with its kind, the reason given by the model and its duration. The interface shows it, and the evaluation counts tool calls and failures from it.

**Trade-offs**
Six steps can be too few for an email with many links and domains. In that case the model has to choose the most important checks. A refused call costs a step, so a confused model ends with less evidence.


---

## Decision 11 - Tool arguments must come from the email

**Decision:**
The ToolRegistry in src/capabilities/tool_registry.py checks every proposed tool call before it runs. A call is refused with InvalidToolCallError when the tool is unknown or when an argument is missing, unknown or has the wrong type. A call is also refused when a domain or link argument does not appear in the email. A parent domain of a host in the email is allowed, for example paypa1.com for login.paypa1.com. The reason for the refusal is sent back to the model, so it can choose again.


**Alternatives considered:**
Trusting the arguments of the model would be simpler. However, text inside a phishing email could then steer the agent to look up an attacker's server or send unrelated data to an external service. Letting the application choose all arguments would be safe, but then the model could no longer decide which of several links is worth checking, which is the point of the agent.

**Why we chose this**
Even a manipulated model can only check what is already in the email. Only domains and links from the email are ever sent to external services. All argument problems are found in one place, before any network request, and the model receives an exact explanation. The email checks and the MCP tools are merged into one registry, so the agent does not need to know where a tool runs.

**Trade-offs**
A domain that is only mentioned in an attachment name or an image cannot be checked, because it does not appear in the parsed text.

**Testing findings:**

The tool registry tests cover valid and invalid tool calls, including unknown tools, missing or incorrectly typed arguments, domains that do not appear in the email, and URLs that are not part of the email. They also verify that a parent domain of a host present in the email is allowed.

The agent tests verify that refused calls consume the step budget and that the rejection reason is returned to the model as an observation. Prompt-injection tests also check that instructions embedded in email content cannot directly execute tools or bypass the application's validation rules.

These checks reduce the risk of prompt injection leading to unauthorised tool calls. However, they do not guarantee that the model will always choose the most useful check or interpret every email correctly. The application therefore retains control over tool execution, and the final verdict is independently validated against the evidence collected during the run.

---

## Decision 12 - Grounded evidence, score bands and safety rules

**Decision:**
The final answer of the model must match the ModelVerdict schema. The VerdictValidator then checks every evidence item. An item that cites a tool must name an observation of this run with the status success or warning, so an unavailable check cannot be cited. An item that quotes the email must appear word for word in the subject or body. Case, spacing and typographic quotes are ignored in this comparison. Our evaluation showed that the 3B model missed a gift card scam from a real colleague address, because all technical checks were clean. A rule in the prompt fixed this case but made the model flag a genuine password reset, even after we narrowed the rule. We therefore moved this knowledge out of the prompt into a deterministic scan, scan_payment_fraud, and the safety rule payment_fraud. The scan only fires when a request for money, gift cards, bank details or codes appears together with urgency, secrecy or an excuse why the sender cannot be called, so a password reset or a registration deadline does not trigger it. Because the scan was designed after looking at our own test cases, we checked it on data it was not built for: it flagged none of the 1,000 legitimate Zenodo texts and found the independent CEO fraud case of Martina (see evaluation_results.md). The model still rated the password reset as phishing after this change, which shows that this error comes from the model's own judgement and not from the rule. Repeated evidence items are removed before the result is shown.

After that, the fixed rules in src/capabilities/verdict_policy.py are applied. The score is moved into the band of the verdict. Likely safe covers 0 to 39, suspicious covers 40 to 69 and phishing covers 70 to 100. The verdict wins, because it is what the user acts on. The safety rules can only raise a verdict and never lower it.

| Rule | Condition | Minimum result |
|---|---|---|
| url_listed_as_malicious | The reputation service lists a link | Phishing with risk 90 |
| known_phishing_campaign | The same sender or subject was judged phishing before | Suspicious with risk 60 |
| prompt_injection | The email contains instructions aimed at AI tools | Suspicious with risk 50 |
| payment_fraud | The email asks for money, gift cards, bank details or codes under pressure or in secret | Suspicious with risk 60 |
| high_risk_finding | A check returned a warning with risk 80 or more | Suspicious with risk 50 |

The rules look at all tool results of the run and not only at the cited ones. The model therefore cannot avoid a rule by leaving a finding out. When a rule raises the verdict, the explanation says so, and the recommended action is replaced with the standard action for the new verdict.

**Alternatives considered**
Allowing evidence only from tools would make grounding stricter. However, the language analysis of urgency, threats and requests for passwords is exactly what the model adds, and exact quotes make this analysis checkable too. Dropping invalid evidence silently would be friendlier, but it would hide a model failure. Letting the model decide everything is not acceptable for a security verdict that text inside the email could talk down.

**Why we chose this**
The user only sees evidence that the application has verified. This is the main difference to chatbot-style phishing checkers. The evaluation can also measure grounding automatically, because every evidence item has a checkable source.

**Trade-offs**
The safety rules can raise a legitimate email to suspicious when one tool gives a high risk value by mistake. This is the price for never letting a clear warning be ignored.


---

## Decision 13 - Model exceptions and an offline fallback

**Decision:**
OllamaModelClient translates every Ollama or network error into our own exceptions. ModelUnavailableError means that Ollama cannot be reached or returned a server error. ModelNotFoundError means that the model is not installed. ModelTimeoutError means that a call took too long. The agent raises InvalidModelOutputError or VerdictValidationError when a corrected answer is still invalid. All five inherit from ModelError.

The AnalysisService catches ModelError in one place and runs the OfflineAnalyzer instead. The fallback runs every check that needs neither model nor network. These are the prompt injection scan, the payment fraud scan, the lookalike check and the email checks of Riana. The score is the highest warning plus 10 for every further warning, and the same safety rules are applied. The result is labelled as a fallback, and the warning tells the user what went wrong.

**Rules:**
An exception class only exists if some code catches it specifically. The agent catches InvalidToolCallError. The service catches ModelError and InvalidEmailInputError. The tool_boundary decorator catches ToolError (Decision 6).

Each model exception carries a reason for the log and a user message for the interface. Raw library errors never reach the user. The original error is kept with raise from, so the log shows the real cause.

The method analyze of the AnalysisService never raises. An unexpected bug becomes the status failed with a friendly message and a full traceback in the log. The tests call the inner method, so bugs still make the tests fail.

Every analyzer implements the same Analyzer interface, so switching to the fallback needs no special code.


**Why we chose this**
The user always gets a usable answer, even without the model, and is told clearly that it is less thorough. The fallback never sends data over the network and never depends on the model, so it works in exactly the situation it is built for. Fallback verdicts are not saved to the case history, so a weaker result cannot influence later analyses.

**Trade-offs**
The fallback cannot check domain age, so a very new sender domain without other warning signs can be rated likely safe in fallback mode. It also cannot judge the wording of an email.

---

## Decision 14 - Three configurations for the evaluation

**Decision**
The agent is compared with two baselines. In the configuration llm_only, the model decides from the email alone. In the configuration fixed_pipeline, every check runs on every email and the model then decides once. All three are Analyzer classes built by the same factory. They share the prompts, the verdict validation and the safety rules, and they run through the same AnalysisService as the app. The script evaluation/run_evaluation.py runs every case through every configuration. It reports precision, recall, F1, false positive rate, accuracy, latency, model calls, tool calls, fallbacks and invalid outputs.


**Alternatives considered**
Separate evaluation scripts with their own prompts would be faster to write, but differences in the results could then come from the prompts and not from the design. Evaluating only the agent would not show whether the agent is justified.


**Why we chose this**
Differences in the results come only from how evidence is collected. The shared base class ModelAnalyzer contains the run state, the model calls and the verdict request with repair. The agent and both baselines only implement how they gather evidence. Running through the service means that input checks, fallbacks and warnings are counted exactly as a user would experience them. Fallbacks are reported separately. The dataset sample uses a fixed seed and the temperature is 0. The online checks can be replayed from the cache, and the case history is not written during evaluation unless this is requested.


**Trade-offs**
A full run of all three configurations takes a long time on a computer without a graphics card. We therefore evaluate a sample of the Zenodo dataset and not all 2000 emails. A suspicious verdict counts as phishing by default, because the user is then warned. The option suspicious-as legitimate shows the stricter view.


## Testing approach for the agent, model client and service (Elif)

My part is tested on two levels. Automated tests check every rule of the agent without a model. Hand-crafted cases check how the real model behaves in the situations that are most risky for an AI analyser.

### Automated tests

All tests run with pytest and need neither Ollama, the network nor the database. A scripted fake model and fake tools are defined in tests/fakes.py, so every rule can be tested on its own and with exactly the model answer that is needed.

The model client tests check the settings sent to Ollama and the translation of every error type into our own exceptions. One test uses the real Ollama library against a port where nothing is listening, so the case that Ollama is not running is tested for real.

The agent tests cover a normal run, finishing without tools, unknown tools, wrong arguments, domains that are not in the email and repeated calls. They also cover unavailable and crashing tools and the step limit. Further tests check invalid JSON and invented evidence that are either corrected or fail twice. The last group checks that prompt injection cannot make an email safe and that an email cannot close its own data block.

The registry, validator and prompt tests check the argument rules, every grounding rule, the score bands and every safety rule. The service tests check rejected input, every switch to the fallback and unexpected bugs. The integration tests use the real factory with the parser and email checks of Riana and the lookalike check of Martina. Only the model is scripted.

### Hand-crafted cases

The cases of Martina already cover lookalike domains, domain age, campaigns, attachments, payment fraud, failing services and simple prompt injection. My twelve cases in evaluation/elif_test_cases.json therefore focus on the agent itself. They ask whether the model can be manipulated, whether it stays within its limits and whether it adds value where the tools find nothing.

| Case | What it tests |
|---|---|
| case-19 | An instruction to the AI hidden in an HTML comment and an invisible block |
| case-20 | A fake end of the data block followed by new instructions |
| case-21 | A polite checklist that tries to waste the step budget on twelve unrelated domains |
| case-22 | A legitimate course announcement about AI, as a false positive check for the injection scan |
| case-23 | A legitimate password reset with alarming wording |
| case-24 | A phishing body pasted without any headers |
| case-25 | A gift card scam from a real colleague address without any link, which only the language analysis can detect |
| case-26 | A phishing email in Finnish |
| case-27 | A phishing link at the end of a long text that is cut off in the prompt |
| case-28 | A fake security banner in the body that claims SPF and DKIM passed, while the real headers failed |
| case-34 | Ollama not running or the model not installed |
| case-35 | Invalid model output, unknown tools, foreign domains, invented evidence and invalid input |

Cases 19 to 28 are emails in evaluation/cases and are run with the real model. Each entry contains grading rules in the same format as the cases of Martina, together with the result of the offline fallback. Cases 34 and 35 are failure situations that are covered by automated tests, which are named in each entry.

Two cases show limits on purpose. In case-21 the checklist contains no typical injection phrase, so the pattern scan does not detect it, and only the step budget protects the agent. In case-25 the offline fallback rates the email likely safe, because no tool finds anything and the fallback cannot judge language. This case shows why the language model is needed. The results of the runs are summarised in evaluation/evaluation_results.md.

---

## Decision 15 - Deterministic email parsing and safe metadata inspection (Riana)

**Decision**
All email parsing and local inspection tools are strictly deterministic and implemented in Python (`src/email_parser.py` and `src/capabilities/tools.py`). 
1. **RFC 822 & 2047 parsing:** Headers, MIME compartments, and encoded words are decoded into typed Pydantic models (`ParsedEmail`, `EmailAddress`, `AuthenticationResults`, `LinkInfo`, `AttachmentMetadata`).
2. **Safe passive attachment inspection:** Attachments are inspected purely through header metadata and filename strings. Payloads are never executed, opened, or written to disk.
3. **HTML sanitization & fallback:** For HTML-only emails, BeautifulSoup strips script and style tags and extracts clean plain text. Links are extracted from HTML `<a>` tags first to preserve `href` vs displayed text mismatches.
4. **Headerless input tolerance:** If a user pastes raw body text without RFC headers, the parser detects this (`has_headers=False`) and proceeds with link and body analysis instead of crashing.

**Alternatives considered**
- **Letting the LLM parse raw emails directly:** Rejected. LLMs frequently hallucinate header alignment, miss obfuscated Punycode domains, and miscalculate IP address links. Technical data must be extracted as hard facts.
- **Active sandbox execution of attachments:** Rejected. Dynamic detonation introduces extreme security risks, high latency, and heavy infrastructure requirements. Metadata checks (double extensions, macro formats, executable extensions) catch typical attacks safely.

**Why we chose this**
- **Privacy and security:** Deterministic local parsing ensures email headers and attachments never leave the machine.
- **Grounded facts:** Tool outputs provide verifiable facts with discrete risk scores (0–100) that feed into the agent's observation loop.
- **Offline reliability:** The 3 in-process tools have zero network or API dependencies, powering the offline fallback when Ollama is down.

---

## Testing approach for email parsing, local checks, and pipeline fallback (Riana)

My part is tested on two levels: automated unit tests covering parser edge cases and tool logic, and hand-crafted evaluation cases tested with both the offline fallback and the live AI model.

### Automated tests

All tests run with pytest and need neither Ollama, the network nor the database:
- **Parser edge cases (`tests/test_email_parser.py`):**
  - Raw pasted text without headers (`has_headers=False`, fallback subject).
  - HTML-only emails with script/style stripping and plain text derivation.
  - RFC 2047 encoded header words (e.g. UTF-8 base64 and ISO-8859-1 subjects).
  - Link extraction: anchor text deception, raw IP addresses, shorteners, Punycode homographs.
- **Tool tests (`tests/test_tools.py`):**
  - `check_email_authentication`: Pass/fail states for SPF, DKIM, and DMARC; Reply-To and Return-Path domain mismatches; display name spoofing against European brands on free webmail.
  - `analyze_links`: Flagging anchor text discrepancies, URL shorteners, and raw IP URLs.
  - `inspect_attachments`: Identifying double extensions (`.pdf.exe`), executable binaries, macros, and archives.
  - `run_offline_fallback`: Verifying mathematical risk aggregation and valid `Verdict` generation.

### Hand-crafted cases

My five cases in `evaluation/riana_test_cases.json` (files 29 to 33 in `evaluation/cases/`) focus on email structure and local technical checks:

| Case | Category | Input | What it tests |
|---|---|---|---|
| **29** | successful_case | `29-legit-hamk.eml` | Clean university email passing SPF, DKIM, and DMARC with authentic links |
| **30** | successful_case | `30-auth-spoof-moodle.eml` | Display name spoofing ('helpdesk' on free webmail), Reply-To mismatch, and failed SPF/DMARC |
| **31** | successful_case | `31-attachment-double-ext.eml` | Dangerous double extension `.pdf.exe` masquerading as a delivery receipt |
| **32** | difficult_case | `32-deceptive-links-punycode.eml` | Deceptive anchor text (Nordea text vs raw IP href) and Punycode homograph domain |
| **33** | failure_case | `33-headerless-raw-body.eml` | Raw pasted body text without RFC headers, missing headers warning, and URL shortener |

The evaluation results comparing the offline fallback against the `qwen3.5:4b` model are documented in `evaluation/evaluation_results.md`.