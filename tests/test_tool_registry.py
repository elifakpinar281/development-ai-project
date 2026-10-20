import pytest

from src.capabilities.tool_registry import (
    EmailScope,
    LocalTool,
    LocalToolSource,
    McpToolSource,
    ToolRegistry,
    normalize_host,
)
from src.exceptions import InvalidToolCallError
from tests.fakes import DOMAIN_PARAMETERS, URL_PARAMETERS, FakeToolSource, make_email, result

LINK = "https://login.hamk-helpdesk.com/verify"


def registry_with(tools, results=None, fillers=None):
    source = FakeToolSource(tools, results or {})
    return ToolRegistry([source], argument_fillers=fillers), source


def test_two_sources_with_the_same_tool_name_are_rejected_at_startup():
    first = FakeToolSource({"x": DOMAIN_PARAMETERS}, {})
    second = FakeToolSource({"x": DOMAIN_PARAMETERS}, {})
    with pytest.raises(ValueError):
        ToolRegistry([first, second])


@pytest.mark.parametrize("value", ["hamk-helpdesk.com", "login.hamk-helpdesk.com", "HAMK-HELPDESK.COM.", "www.hamk-helpdesk.com"])
def test_domains_from_the_email_and_their_parents_are_allowed(value):
    registry, _ = registry_with({"check": DOMAIN_PARAMETERS})
    email = make_email()
    call = registry.prepare_call("check", {"domain": value}, email, EmailScope(email))
    assert call.tool_name == "check"


@pytest.mark.parametrize("value", ["evil.com", "com", "helpdesk.com", ""])
def test_other_domains_are_refused(value):
    registry, _ = registry_with({"check": DOMAIN_PARAMETERS})
    email = make_email()
    with pytest.raises(InvalidToolCallError):
        registry.prepare_call("check", {"domain": value}, email, EmailScope(email))


def test_urls_must_be_links_from_the_email():
    registry, _ = registry_with({"rep": URL_PARAMETERS})
    email = make_email()
    scope = EmailScope(email)
    assert registry.prepare_call("rep", {"url": LINK + "/"}, email, scope).arguments == {"url": LINK + "/"}
    with pytest.raises(InvalidToolCallError) as caught:
        registry.prepare_call("rep", {"url": "https://attacker.net/collect?data=1"}, email, scope)
    assert "is not a link in this email" in caught.value.reason


def test_arguments_must_be_an_object():
    registry, _ = registry_with({"check": DOMAIN_PARAMETERS})
    email = make_email()
    with pytest.raises(InvalidToolCallError):
        registry.prepare_call("check", ["hamk-helpdesk.com"], email, EmailScope(email))


def test_boolean_is_not_accepted_as_integer_and_any_of_is_supported():
    schema = {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "names": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]},
        },
    }
    registry, _ = registry_with({"t": schema})
    email = make_email()
    scope = EmailScope(email)
    registry.prepare_call("t", {"count": 2, "names": ["a"]}, email, scope)
    registry.prepare_call("t", {"names": None}, email, scope)
    with pytest.raises(InvalidToolCallError):
        registry.prepare_call("t", {"count": True}, email, scope)
    with pytest.raises(InvalidToolCallError):
        registry.prepare_call("t", {"names": ["a", 3]}, email, scope)


def test_argument_filler_replaces_whatever_the_model_sent():
    registry, _ = registry_with(
        {"history": {"type": "object", "properties": {"sender_domain": {"type": "string"}}}},
        fillers={"history": lambda email: {"sender_domain": email.sender.domain}},
    )
    email = make_email()
    call = registry.prepare_call("history", {"sender_domain": "x.org", "junk": 1}, email, EmailScope(email))
    assert call.arguments == {"sender_domain": "hamk-helpdesk.com"}
    spec = registry.specs_for_model()[0]
    assert spec.parameters["properties"] == {} and "filled in automatically" in spec.description


def test_signature_ignores_argument_order():
    registry, _ = registry_with({})
    from src.capabilities.tool_registry import PreparedCall
    assert PreparedCall("t", {"a": 1, "b": 2}).signature() == PreparedCall("t", {"b": 2, "a": 1}).signature()


def test_execute_turns_a_non_tool_result_into_unavailable():
    registry, _ = registry_with({"t": {"type": "object", "properties": {}}}, {"t": "oops"})
    email = make_email()
    call = registry.prepare_call("t", {}, email, EmailScope(email))
    outcome = registry.execute(call, email)
    assert outcome.status == "unavailable" and outcome.tool_name == "t"


def test_local_tool_source_passes_the_email():
    seen = []

    def check(email):
        seen.append(email.subject)
        return result("local_check", "warning", 40, "found")

    registry = ToolRegistry([LocalToolSource([LocalTool("local_check", "desc", check)])])
    email = make_email(subject="Hello")
    call = registry.prepare_call("local_check", {}, email, EmailScope(email))
    assert registry.execute(call, email).status == "warning"
    assert seen == ["Hello"]


def test_mcp_tool_source_reads_openai_style_definitions():
    class FakeMcpClient:
        def tool_definitions(self):
            return [{"type": "function", "function": {
                "name": "check_domain_age", "description": "Age.", "parameters": DOMAIN_PARAMETERS}}]

        def call(self, name, arguments):
            return result(name, "success", 0, f"checked {arguments['domain']}")

    registry = ToolRegistry([McpToolSource(FakeMcpClient())])
    email = make_email()
    call = registry.prepare_call("check_domain_age", {"domain": "hamk-helpdesk.com"}, email, EmailScope(email))
    assert registry.execute(call, email).finding == "checked hamk-helpdesk.com"


@pytest.mark.parametrize("raw,expected", [
    ("WWW.Example.COM.", "example.com"),
    ("https://user@login.example.com:8443/path", "login.example.com"),
    ("someone@example.org", "example.org"),
    (None, ""),
])
def test_normalize_host(raw, expected):
    assert normalize_host(raw) == expected
