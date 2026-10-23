from src.schemas.agent import AnalysisResult, AnalysisStatus
from src.schemas.responses import EmailAddress, LinkInfo, ParsedEmail, Verdict
from src.services.history import SessionHistory, grouping_rows, grouping_text, subject_pattern


def _email(sender: str, subject: str, link: str) -> ParsedEmail:
    domain = sender.rsplit("@", 1)[1]
    return ParsedEmail(
        subject=subject,
        sender=EmailAddress(address=sender, domain=domain),
        links=[LinkInfo(url=f"http://{link}/x", domain=link)],
    )


def _result(verdict: str, risk: float) -> AnalysisResult:
    return AnalysisResult(
        status=AnalysisStatus.COMPLETED,
        verdict=Verdict(verdict=verdict, risk_score=risk, explanation="Fake parcel notice. More text."),
    )


def test_subject_pattern_replaces_numbers_and_reply_prefix():
    assert subject_pattern("RE: Parcel 8841 could not be delivered") == "parcel # could not be delivered"
    assert subject_pattern("Hi there") == ""


def test_emails_with_same_subject_pattern_form_a_campaign():
    history = SessionHistory()
    history.add(_email("a@parcel-one.example", "Parcel 1207 could not be delivered", "pay-one.example"), _result("phishing", 92), "m")
    history.add(_email("b@other.example", "Weekly newsletter for you", "news.example"), _result("phishing", 80), "m")
    history.add(_email("c@parcel-two.example", "Parcel 5530 could not be delivered", "pay-two.example"), _result("phishing", 90), "m")

    campaigns = history.campaigns()

    assert len(campaigns) == 1
    assert [entry.entry_id for entry in campaigns[0].entries] == [1, 3]
    assert history.campaign_of(2) is None
    assert "share the same subject" in grouping_text(campaigns[0])
    assert grouping_rows(campaigns[0])[0]["label"] == "Subject pattern"


def test_safe_emails_never_join_a_campaign():
    history = SessionHistory()
    history.add(_email("a@shop.example", "Your order 1 has shipped today", "shop.example"), _result("likely safe", 5), "m")
    history.add(_email("a@shop.example", "Your order 2 has shipped today", "shop.example"), _result("likely safe", 5), "m")

    assert history.campaigns() == []