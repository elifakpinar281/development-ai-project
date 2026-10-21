import sys

from src.capabilities import eval_cache, memory
from src.schemas.responses import ToolResult

DOMAIN_AGES = {
    "paypa1.example": 3,
    "hamk-student-login.example": 5,
    "invoice-portal-ab12.example": 2,
    "parcel-notice-center.example": 1,
    "delivery-update-desk.example": 2,
    "hamk-account-check.example": 4,
    "hamk-support-portal.example": 6,
    "invoice-review.example": 12,
    "shipment-tracking.example": 9,
    "staff-documents.example": 3,
    "company-finance-approval.example": 2,
    "project-updates.example": 1800,
    "lecture-notes.example": 900,
    "lecture-office.example": 900,
    "hamk.fi": 9000,
    "parcel-hub-notice.example": 1,
    "xn--pypal-4ve.example": 2,
    "hamk-test.example": 3000,
    "company-test.example": 3000,
}
MALICIOUS_URLS = {"http://paypa1.example/verify"}
CLEAN_URLS = {
    "http://hamk-student-login.example/reset",
    "http://invoice-portal-ab12.example/pay/20931",
    "http://parcel-notice-center.example/confirm?id=8841",
    "http://delivery-update-desk.example/confirm?id=5530",
    "http://hamk-account-check.example/verify",
    "http://hamk-support-portal.example/reactivate",
    "http://company-finance-approval.example/transfer",
    "http://parcel-hub-notice.example/pay?ref=77310",
    "http://xn--pypal-4ve.example/signin",
    "https://www.hamk.fi/studies/",
}


def _age_result(domain: str, days: int) -> ToolResult:
    # same thresholds as domain_tools.check_domain_age
    risk = 90 if days < 7 else 75 if days < 30 else 45 if days < 90 else 10 if days < 365 else 0
    return ToolResult(
        tool_name="check_domain_age", status="warning" if risk >= 40 else "success",
        risk_score=risk, is_offline=False,
        finding=f"The domain was registered approximately {days} days ago.",
        evidence=["Registration date: seeded for evaluation", f"Domain age: {days} days"],
        details={"domain": domain, "age_days": days, "registered": True})


def _rep_result(url: str, malicious: bool) -> ToolResult:
    if malicious:
        return ToolResult(
            tool_name="check_url_reputation", status="warning", risk_score=95, is_offline=False,
            finding="Google Safe Browsing reported a threat for this URL.",
            evidence=["Threat type: SOCIAL_ENGINEERING"],
            details={"url": url, "listed": True, "threat_types": ["SOCIAL_ENGINEERING"]})
    return ToolResult(
        tool_name="check_url_reputation", status="success", risk_score=0, is_offline=False,
        finding=("The URL was not listed by Google Safe Browsing. "
                 "This does not guarantee that the URL is safe."),
        evidence=["No matching threat was returned by Safe Browsing."],
        details={"url": url, "listed": False})


def main(reset_history: bool = False) -> None:
    memory.init_db()
    for domain, days in DOMAIN_AGES.items():
        eval_cache.seed("check_domain_age", {"domain": domain}, _age_result(domain, days))
    for url in MALICIOUS_URLS | CLEAN_URLS:
        eval_cache.seed("check_url_reputation", {"url": url}, _rep_result(url, url in MALICIOUS_URLS))
    if reset_history:
        memory.reset_history()
    print(f"Seeded {len(DOMAIN_AGES)} domain ages and {len(MALICIOUS_URLS | CLEAN_URLS)} URL results.")


if __name__ == "__main__":
    main(reset_history="--reset-history" in sys.argv)