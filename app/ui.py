import gradio as gr

from src.services.ai_service import analyze_email

VERDICT_STYLE = {
    "phishing": "🔴 PHISHING",
    "suspicious": "🟠 SUSPICIOUS",
    "likely safe": "🟢 LIKELY SAFE",
}


def _read_upload(uploaded) -> str:
    """Read an uploaded .eml file as text."""
    if not uploaded:
        return ""
    path = getattr(uploaded, "name", uploaded)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def run_analysis(pasted_text: str, uploaded_file):
    """Called by the button. Talks only to the service layer."""
    text = _read_upload(uploaded_file) or (pasted_text or "")
    result = analyze_email(text)

    # Rejected or failed input: no verdict, just a message
    if result.verdict is None:
        return (result.message, "", "", "", "", "", "")

    v = result.verdict
    headline = f"## {VERDICT_STYLE.get(v.verdict, v.verdict.upper())}"
    score = f"**Risk score: {v.risk_score:.0f} / 100**  (confidence {v.confidence:.0%})"

    if v.is_fallback:
        headline += "\n\n⚠️ **Offline fallback result: the AI model was not used.**"

    explanation = f"{v.explanation}\n\n**What to do:** {v.recommended_action}"

    evidence = "\n".join(f"- {e}" for e in v.evidence) or "_No evidence items._"

    notes = list(result.warnings)
    if v.unavailable_tools:
        notes.append("Checks that were unavailable: " + ", ".join(v.unavailable_tools))
    warnings = "\n".join(f"- {w}" for w in notes) or "_None._"

    trace_lines = []
    for step in result.trace:
        tool = f" `{step.tool_name}`" if step.tool_name else ""
        line = f"{step.step}. **{step.kind}**{tool}: {step.summary}"
        if step.reason:
            line += f"  _(why: {step.reason})_"
        trace_lines.append(line)
    trace = "\n".join(trace_lines) or "_No steps recorded._"

    footer = (
        f"Analyzer: {result.analyzer_name} · model calls: {result.model_calls} "
        f"· time: {result.duration_ms} ms · checks run: {', '.join(v.tools_run) or 'none'}"
    )
    return (headline + "\n\n" + score, explanation, evidence, warnings, trace, footer, "")


def build_ui() -> gr.Blocks:
    """Gradio interface. Communicates only with the AI service layer."""
    with gr.Blocks(title="SpotThePhish") as demo:
        gr.Markdown(
            "# 🎣 SpotThePhish\n"
            "Paste a suspicious email or upload an `.eml` file. "
            "The assistant investigates it and tells you whether it looks like phishing."
        )

        with gr.Row():
            with gr.Column():
                email_box = gr.Textbox(
                    lines=12,
                    label="Paste the email (full source gives the best result)",
                    placeholder="From: ...\nSubject: ...\n\nBody...",
                )
                eml_file = gr.File(label="...or upload an .eml file", file_types=[".eml", ".txt"])
                analyze_btn = gr.Button("Analyze email", variant="primary")
                clear_btn = gr.Button("Clear")

            with gr.Column():
                verdict_md = gr.Markdown(label="Verdict")
                explanation_md = gr.Markdown()
                with gr.Accordion("Evidence", open=True):
                    evidence_md = gr.Markdown()
                with gr.Accordion("Warnings and unavailable checks", open=True):
                    warnings_md = gr.Markdown()
                with gr.Accordion("Investigation trace", open=False):
                    trace_md = gr.Markdown()
                footer_md = gr.Markdown()
                spare = gr.Markdown(visible=False)

        outputs = [verdict_md, explanation_md, evidence_md, warnings_md, trace_md, footer_md, spare]
        analyze_btn.click(fn=run_analysis, inputs=[email_box, eml_file], outputs=outputs)
        clear_btn.click(
            fn=lambda: ("", None, "", "", "", "", "", "", ""),
            outputs=[email_box, eml_file] + outputs,
        )

    return demo