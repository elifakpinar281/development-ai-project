# Project decisions

Use this decision log to document significant technical and architectural choices made by your team during development.

## Decision 1 — Model selection

**Decision:**
We use `qwen3:8b`, running locally through Ollama. `qwen3:4b` is the fallback for computers with less memory. The model is set through the `MODEL_NAME` environment variable, so switching requires no code changes.

**Alternatives considered:**
- Cloud-hosted LLM APIs (e.g. OpenAI, Anthropic, Gemini)
- Smaller local model (`qwen3:4b`) as the main model
- No LLM at all, only rule-based checks

**Why we chose this:**
- **Privacy:** Emails can contain personal and confidential data. Running the model locally keeps the email and its analysis on the user's machine instead of sending it to a third-party API.
- **Tool calling and structured output:** Our agent loop needs reliable JSON actions and a schema-conformant final report. Qwen3 supports both in Ollama.
- **Reasoning capacity:** The agent has to combine several pieces of evidence instead of classifying in one step. The 8B model has more capacity for this than smaller models and still runs on typical laptops with 16 GB RAM.
- **Configurability:** Because the model is set via environment variables, we can compare `qwen3:8b` and `qwen3:4b` in the evaluation without changing the application.

Trade-off: a local 8B model can produce invalid actions or miss subtle manipulation. We limit this with schema validation, a step limit and a fallback pipeline (see Decision 3).

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
- The offline fallback must work even if MCP is down. It therefore calls `check_lookalike_domain` directly as a Python function.

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
- **Campaign matching that works:** The subject is normalised before hashing (lower case, reply prefixes removed, digits replaced). "Parcel 8841 could not be delivered" and "RE: Parcel 5530 could not be delivered" produce the same fingerprint, so a campaign with changing tracking numbers is recognised. Link domains of well-known brands are ignored for matching, because they appear in countless legitimate emails.
- **A previous verdict is evidence, not an answer:** The tool returns a finding and a risk contribution, and the agent still decides. A wrong earlier verdict therefore cannot silently repeat itself. A match on sender domain or subject pattern with an earlier "phishing" verdict counts more (risk 70) than a link-domain match alone (risk 55).
- **Fallback verdicts are not stored:** They are rule-based and less reliable, and storing them would spread their errors.
- **Why PostgreSQL:** The history and the evaluation cache are shared state used by several processes and several team members. PostgreSQL gives concurrent access, array columns with a GIN index for link-domain matching, and JSONB for cached results. Docker Compose makes the setup reproducible. Separate databases (`spotthephish`, `spotthephish_eval`, `spotthephish_test`) keep development data, evaluation runs and tests apart.

**Trade-offs:**
- Docker (or a local PostgreSQL) is now required to run the full application.
- If the database is unavailable, `lookup_case_history` returns `unavailable` and saving a verdict raises a controlled `DatabaseUnavailableError`. The analysis itself continues without the history.
- The history can only be as good as past verdicts. Campaign matching by subject can also produce false matches for generic subjects. In the evaluation, repeated-campaign cases required a median of 2 tool calls, compared with 3 for the other cases. This suggests that case history can reduce the number of checks when a matching previous verdict is available. However, fallback verdicts are not stored, so the benefit depends on the model successfully completing an analysis and saving a validated verdict.
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
- **No secrets in messages:** The reason texts are fixed and safe to display. The Safe Browsing key is sent as a URL parameter, and HTTP library errors can contain that URL. We therefore never copy the original error text into a message, and only the exception type is logged. A test checks that the key does not appear in the result.
- **Failures are never cached:** Because failures raise, the evaluation cache cannot freeze a temporary outage as a result.
- **Unexpected bugs are contained:** Any other exception is caught at the same boundary and reported as `unavailable` with only its type name.
- **Input validation protects the services:** A domain is validated and normalised before it is placed into an RDAP request URL, and a URL must be a plain http/https URL. Invalid input never leads to a network request.

**Trade-offs:**
- We do not retry automatically. Each call is bounded by `HTTP_TIMEOUT` (default 5 seconds), and the agent has a budget of 6 tool calls. Retries would multiply the waiting time for the user. The course recommends bounded retries mainly for model output, and for our tools "unavailable" is the safer outcome.
- A missing optional service (for example no Safe Browsing key) lowers detection quality. The UI shows this, but it cannot be repaired by the application.

---

## Decision 7 — Domain checks: heuristics, services and thresholds

**Decision:**
- **Lookalike domains** are detected offline by comparing the domain name with a list of well-known brands (including `hamk.fi`). The rules are: character substitution (for example `paypa1`, `amaz0n`, Cyrillic lookalike letters, also inside punycode domains), the same name under a different domain ending, a brand name of at least 4 characters embedded in a longer name (`hamk-student-login`), and an edit distance of 1 or 2 for names with at least 5 characters.
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

---

## Testing approach for the domain tools and memory (Person 3)

- **Deterministic tests (no model needed):**
  - Each tool: detected cases, clean cases and invalid input (empty, malformed, too long, path-like input, non-http URLs).
  - External failures simulated for each service: timeout, rate limit, HTTP error, unreadable response, missing API key, database unreachable.
  - Custom exceptions: hierarchy, status mapping, the boundary converting exceptions and containing unexpected bugs, no secret in any result.
  - Privacy: the case history has no body column and stores no body text.
  - Memory: campaign match through a subject variant, ignored brand links, fallback verdicts not stored, writing can be disabled, controlled error if the database is down.
  - Evaluation cache: record, then replay, cache miss, seeding.
  - MCP: the server lists exactly the four tools, the write function is not exposed, the synchronous client works and unknown tools are rejected.
- **Hand-crafted evaluation cases:** Five emails with six cases (`evaluation/martina_test_cases.json`): two lookalike domains, one newly registered domain, a repeated campaign (first report and repeat) and one failure case with unavailable online checks. [TODO: record actual results and status per case.]


---

## Decision 9 - Model interaction: structured output, temperature 0, thinking off

**Decision:**
The agent talks to the model through Ollama's structured output. Every request includes a JSON schema (`AgentDecision` for each step, `ModelVerdict` for the final answer), and Ollama constrains the reply to that schema. 
We do not use Ollama's native tool-calling format. 

Settings: temperature 0, Qwen3 thinking mode off, context window 8192 tokens, 120-second timeout per call (all configurable in `.env`). All Ollama access goes through one class, `OllamaModelClient`, behind a small `ModelClient` interface.

**Alternatives considered:**
- **Native tool calling:** The model returns `tool_calls`, and the final verdict is free text. Small local models sometimes write tool calls into the text instead, and the final verdict would still need a separate format, so we would need two parsing paths.
- **Free-text JSON (prompt only, no schema):** Simpler, but an 8B model produces invalid JSON noticeably more often.
- **Thinking mode on:** Better reasoning on hard cases, but several times slower per step, and with up to 8 model calls per email the waiting time is not acceptable. The agent already writes a short reason for every step, which serves as a visible trace.

**Why we chose this:**
- **Fewer invalid outputs:** The schema limits tool_name to the real tool names and verdict to the three allowed values, so most format errors cannot happen at all. The application still validates everything with Pydantic (defence in depth), because a schema cannot check meaning.
- **One parsing path:** Each step and the final answer are plain JSON validated by Pydantic models.
- **Repeatable evaluation:** Temperature 0 makes the same email give (nearly) the same result, so the three configurations can be compared fairly.
- **Replaceable runtime:** The rest of the application depends on `ModelClient`, not on Ollama. Tests use a scripted fake model.

---

## Decision 10 - Agent loop design

**Decision:**
`AgentAnalyzer` runs a ReAct-style loop. The model decides and the application acts:

1. The application runs the prompt-injection scan automatically (observation #1).
2. Each step, the model returns `call_tool` (one tool and its arguments, with a one-sentence reason) or `finish`.
3. The application checks the call (Decision 11), runs it and sends the result back as a numbered observation.
4. After `finish`, or when the step budget is used up, the model writes the final verdict, which is validated (Decision 12).

**Rules the application enforces:**
- **Step budget of 6:** Every decision that is not `finish` uses a step, including refused and repeated calls. The loop therefore always ends, even if the model keeps asking for invalid tools.
- **No repeated checks:** The same tool with the same arguments is not run twice. The model is pointed to the earlier observation instead.
- **Bounded repair:** If an answer is not valid JSON or breaks the evidence rules, the model gets the exact problems back and one chance to correct it (max_repairs = 1). A second failure ends the run with a `ModelError`, and the service switches to the fallback.
- **App-filled arguments:** `lookup_case_history` always searches for the email's own sender, link domains and subject. The model only decides whether to run it, not what to search for.
- **No state between emails:** Every analysis creates a fresh run object, so one email can never influence the next.

**Why:**
- The agent is the capability we argue for in the README, so the application, not the model, must stay in control of what runs and when it stops.
- Counting refused calls prevents an endless loop and keeps the worst-case latency bounded (at most 6 tool calls and a fixed maximum number of model calls).
- The trace records every step with its kind (`tool_call`, `rejected_call`, `repeated_call`, `invalid_output`, `step_limit`, `final_answer`), the model's reason and the duration. The UI shows it, and the evaluation can count tool calls and failures from it.

---

## Decision 11 - Tool calls may only use domains and links from the email

**Decision:**
`ToolRegistry` (`src/capabilities/tool_registry.py`) checks every proposed tool call before it runs. 
A call is refused (`InvalidToolCallError`) when the tool is unknown, an argument is missing, unknown or has the wrong type, or when a `domain` or `url` argument does not appear in the email. 
A parent domain of a host in the email is allowed (`paypa1.com` for `login.paypa1.com`). 
The refusal reason is sent back to the model so it can choose again.

**Alternatives considered:**
- **Trust the model's arguments:** Simpler, but text inside a phishing email could steer the agent to look up an attacker's server or send unrelated data to an external service.
- **Let the application choose all arguments:** Safe, but then the model could no longer decide which of several domains or links is worth checking, which is the point of the agent.

**Why:**
- **Prompt-injection containment:** Even if the model were manipulated, it can only check what is already in the email.
- **Privacy:** Only domains and links from the email are ever sent to external services.
- **Clear errors:** Argument problems are found in one place, before any network request, and the model receives an exact explanation.
- **One registry for all tools:** in-process email checks (`LocalToolSource`) and MCP tools (`McpToolSource`) are merged into one list, so the agent does not know or care where a tool runs.

**Testing findings:**

The tool registry tests cover valid and invalid tool calls, including unknown tools, missing or incorrectly typed arguments, domains that do not appear in the email, and URLs that are not part of the email. They also verify that a parent domain of a host present in the email is allowed.

The agent tests verify that refused calls consume the step budget and that the rejection reason is returned to the model as an observation. Prompt-injection tests also check that instructions embedded in email content cannot directly execute tools or bypass the application's validation rules.

These checks reduce the risk of prompt injection leading to unauthorised tool calls. However, they do not guarantee that the model will always choose the most useful check or interpret every email correctly. The application therefore retains control over tool execution, and the final verdict is independently validated against the evidence collected during the run.

---

## Decision 12 - Verdict validation: grounded evidence, score bands and safety rules

**Decision:**
The model's final answer must match `ModelVerdict`, and `VerdictValidator` then checks every evidence item:
- `source: "tool"`: the cited observation must exist in this run and must have actually run (status success or warning). An unavailable check cannot be cited.
- `source: "email_text"`: the quote must appear word for word in the email's subject or body (ignoring case, spacing and curly quotes).

After that, fixed rules in `src/capabilities/verdict_policy.py` are applied:
- **Score bands:** likely safe 0–39, suspicious 40–69, phishing 70–100. If the score does not fit the verdict, the score is moved into the band (the verdict wins, because it is what the user acts on).
- **Safety rules** (can only raise, never lower):

| Rule | Condition | Minimum |
|---|---|---|
| `url_listed_as_malicious` | the reputation service lists a link | phishing, 90 |
| `known_phishing_campaign` | the same sender or subject was judged phishing before | suspicious, 60 |
| `prompt_injection` | the email contains instructions aimed at AI tools | suspicious, 50 |
| `high_risk_finding` | any check returned a warning with risk >= 80 | suspicious, 50 |

The rules look at all tool results of the run, not only the cited ones, so the model cannot avoid a rule by leaving a finding out. 
When a rule raises the verdict, the explanation says so and the recommended action is replaced with the standard action for the new verdict.

**Alternatives considered:**
- **Evidence only from tools:** Grounding would be stricter, but the language analysis (urgency, threats, requests for passwords) is exactly what the LLM adds. Exact quotes make that analysis checkable too.
- **Drop invalid evidence silently:** Friendlier, but it hides a model failure. We ask for one correction and fall back if it fails again.
- **Let the model decide everything:** A security verdict that can be talked down by text inside the email is not acceptable.

**Why:**
- The user sees only evidence the application has verified, which is our main difference from chatbot-style phishing checkers.
- The evaluation can measure grounding automatically, because every evidence item has a checkable source.

---

## Decision 13 - Model failures: own exceptions and an offline fallback

**Decision:**
`OllamaModelClient` translates every Ollama or network error into our own exceptions: `ModelUnavailableError` (not reachable or server error), `ModelNotFoundError` (model not pulled) and `ModelTimeoutError`. The agent raises `InvalidModelOutputError` or `VerdictValidationError` when a repaired answer is still invalid. All five are subclasses of `ModelError`. `AnalysisService` catches `ModelError` in one place and runs `OfflineAnalyzer` instead: every offline check (prompt-injection scan, lookalike domains, Person 1's email checks), a simple score (highest warning + 10 per extra warning) and the same safety rules. The result is labelled as a fallback, and the warning tells the user what went wrong.

**Rules:**
- An exception class exists only if some code catches it specifically. `InvalidToolCallError` is caught by the agent, `ModelError` and `InvalidEmailInputError` by the service, `ToolError` by `tool_boundary` (Decision 6).
- Each model exception carries a `reason` for the log and a `user_message` for the UI. Raw library errors never reach the user.
- Original errors are kept with `raise ... from error`, so the log shows the real cause.
- `AnalysisService.analyze()` never raises: an unexpected bug becomes status `FAILED` with a friendly message and a full traceback in the log. Tests call the internal method, so bugs still fail the tests.
- Every analyzer (agent, fallback, the two baselines) implements the same `Analyzer` interface, so switching to the fallback needs no special code.

**Why:**
- The user always gets a usable answer, even without the model, and is told clearly that it is less thorough.
- The fallback never sends data over the network and never depends on the model, so it works in exactly the situation it is needed for.
- Fallback verdicts are not saved to the case history, so a weaker result cannot influence later analyses.

---

## Testing approach for the agent, model client and service 

All tests run without Ollama, network or database (`tests/fakes.py` provides a scripted model and fake tools). One test uses the real `ollama` library against a port where nothing is listening, so the "Ollama not running" path is tested for real.

- **Model client** (`test_model_client.py`): reply extraction, settings sent to Ollama, each error type translated (404, server error, connection refused, timeout, broken connection), bugs not disguised as "Ollama down".
- **Agent** (`test_agent.py`): normal run, finishing without tools, unknown tool, wrong or missing arguments, domain not in the email, repeated call, unavailable and crashing tools, step limit, refused calls using up steps, malformed JSON repaired and failing twice, invented evidence repaired and failing twice, prompt injection that cannot make an email safe, an email that tries to close its data block, no state between emails.
- **Tool registry** (`test_tool_registry.py`): argument types, anyOf, allowed and refused domains and URLs, app-filled arguments, MCP and local tool sources.
- **Validator and rules** (`test_verdict_validator.py`): every grounding rule, score bands, every safety rule, rules never lowering a verdict.
- **Prompts and injection scan** (`test_prompts.py`): data blocks, truncation, missing headers, JSON extraction, injection patterns found (including in HTML comments) and normal emails not flagged.
- **Service** (`test_ai_service.py`): empty, too large and non-email input rejected without a model call. Each model error switching to the fallback; database failure only adding a warning. Unexpected bug becoming `FAILED`.
- **End-to-end** (`test_integration.py`): the real factory with Person 1's parser and email checks and Person 3's lookalike check; only the model is scripted. A real `.eml` case through the agent, all email checks offered to the model, Ollama down leading to the offline fallback with every offline check, a "safe" verdict overruled by a high-risk check, a pasted body without headers, and the parser's placeholder subject never used for campaign matching.
- **Manual check with the real model:** `python -m scripts.try_agent evaluation/cases/*.eml` runs the full application on the evaluation emails and prints the verdict, evidence, warnings and trace.
