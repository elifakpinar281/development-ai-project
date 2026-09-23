# Architecture Documentation

This document describes the baseline architectural layer of the application and provides guidance on extending it for your team project.

## Baseline Architecture

The starter application follows a clean layered separation of concerns:

```text
User
  ↓
Gradio UI (app/ui.py)
  ↓
Application / AI Service (src/services/ai_service.py)
  ↓
Model Client (src/models/model_client.py)
  ↓
Ollama (Local Inference Server)
  ↓
Local Model (e.g., llama3.2)
```

### Core Design Rules

1. **Strict UI Isolation:** The Gradio user interface (`app/ui.py`) MUST NOT directly instantiate `OllamaModelClient` or make direct API calls to Ollama. It delegates all operations to `generate_response()` in `src/services/ai_service.py`.
2. **Controlled Failure Handling:** The model client handles low-level runtime exceptions (e.g., connection errors or missing models). The service layer converts technical exceptions into structured, user-understandable responses.
3. **Data Schemas:** Request inputs and response structures are validated using Pydantic models in `src/schemas/responses.py`.

---

## Extension Points for Course Projects

As your project team designs and implements your additional AI capability, extend this baseline architecture. Potential capability extensions include:

- **RAG (Retrieval-Augmented Generation):** Insert document parsing, chunking, vector embeddings, and vector database retrieval into the service layer to ground model responses in domain documents.
- **Tools & External APIs:** Integrate tool execution functions into the service layer allowing the model to query web services or local python utilities.
- **Model Context Protocol (MCP):** Connect your service layer to standard MCP servers to access external tools and context providers.
- **Agent Workflows:** Implement observation-action loops controlled by application code to handle dynamic multi-step tasks.
- **Memory & Conversation State:** Store session history or persistent state across user interactions.
- **Multimodal Models:** Update the model client and UI payload to pass image data to vision-enabled local models.

> **Instruction:** Replace or update the architecture diagram in this file as your team implements new components.
