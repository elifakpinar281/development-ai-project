import app.ui as ui
from src.config import config
from src.services import follow_up, model_choice
from src.services.service_factory import build_analysis_service
from tests.fakes import ScriptedModelClient, decide, final

EMAIL = (
    "From: IT Support <it-support@hamk-helpdesk.example>\n"
    "Subject: Verify your password\n\n"
    "Your mailbox will be closed today. Verify your password now:\n"
    "http://hamk-helpdesk.example/verify\n"
)


def test_analyze_and_follow_up_go_through_the_service_layer(monkeypatch):
    monkeypatch.setattr(config, "case_history_write", False)
    model = ScriptedModelClient([
        decide(None),
        final(evidence=[{"source": "tool", "observation_id": 1, "quote": None, "claim": "Scan ran."}]),
    ])
    service = build_analysis_service(model_client=model, start_domain_server=False)
    monkeypatch.setattr(ui, "get_service_for_model", lambda name: service)

    view = ui.analyze({"text": EMAIL})
    assert "rejected" not in view

    answers = ScriptedModelClient(["Do not click the link and report the email to IT support."])
    monkeypatch.setattr(model_choice, "get_model_client", lambda name: answers)
    entry_id = ui.session_history().entries()[-1].entry_id
    ui.ask({"id": entry_id, "question": "What should I do?"})

    messages = ui.session_history().get(entry_id).messages
    assert messages[-1].content.startswith("Do not click the link")


def test_empty_input_is_rejected_by_the_service(monkeypatch):
    service = build_analysis_service(model_client=ScriptedModelClient([]), start_domain_server=False)
    monkeypatch.setattr(ui, "get_service_for_model", lambda name: service)
    view = ui.analyze({"text": "   "})
    assert view["rejected"] is True