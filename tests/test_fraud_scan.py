import pytest

from src.capabilities.fraud_scan import scan_for_payment_fraud
from tests.fakes import make_email


@pytest.mark.parametrize("body", [
    "Please buy five Apple gift cards today and send me photos of the codes as soon as possible. Keep this between us.",
    "Transfer EUR 4,850 to our new supplier immediately. This is confidential. Do not call me, I am in a meeting.",
    "Urgent: update your bank details within 24 hours or the salary cannot be paid.",
])
def test_payment_requests_under_pressure_are_flagged(body):
    result = scan_for_payment_fraud(make_email(body=body, links=()))
    assert result.status == "warning"
    assert result.details["payment_fraud_detected"] is True
    assert result.evidence


@pytest.mark.parametrize("body", [
    "You can use the following link to reset your password within the next 3 hours.",
    "Registration for the course closes on 6 November. The form is on the HAMK website.",
    "Our invoice 20999 for the workshop room is still open. Could you forward it to accounting?",
    "Thank you for buying a gift card in our shop. Your receipt is attached.",
])
def test_normal_emails_are_not_flagged(body):
    result = scan_for_payment_fraud(make_email(subject="Information", body=body, links=()))
    assert result.status == "success"
    assert result.details["payment_fraud_detected"] is False


def test_request_without_pressure_is_not_enough():
    result = scan_for_payment_fraud(make_email(subject="Info", body="Please transfer EUR 50 for the excursion by next month.", links=()))
    assert result.status == "success"