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
3. **Memory / persistent state:** a local SQLite case history of validated verdicts, queried through the `lookup_case_history` tool.

**Why it is needed:**
- **Tools:** An LLM alone cannot know whether a domain was registered yesterday, whether DKIM failed or whether a URL is on a blocklist. It would guess, and a guessed security verdict is dangerous. Tools provide verifiable facts, and the model interprets and explains them.
- **Agent:** Phishing emails attack in different ways (malicious link, fake sender, dangerous attachment), so the useful checks depend on the email and on each previous result. A fixed workflow would run every check on every email, which is slower, sends more data to external services and fills the explanation with irrelevant findings. The agent picks only relevant checks and stops when the evidence is clear.
- **Memory:** Phishing arrives in campaigns. If the same sender domain, link domain or subject pattern was already judged phishing, the agent can finish with fewer checks and tell the user it is a known campaign. Only matching fingerprints are stored (no full email bodies), and a previous verdict counts as evidence, not as an automatic answer, so a wrong earlier result cannot silently repeat.

**Alternatives considered:**
- **RAG:** Rejected. The needed knowledge is not in a document collection. It comes from the email itself and from live checks, so tools fit better.
- **MCP:** Not needed. Our tools are a small fixed set inside the application, so a separate protocol would add complexity without benefit.
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
