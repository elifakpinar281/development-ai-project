from src.capabilities.tool_boundary import error_to_result, tool_boundary
from src.exceptions import (
    DatabaseUnavailableError,
    EvalCacheMissError,
    ExternalServiceError,
    InvalidDomainError,
    InvalidToolInputError,
    MissingApiKeyError,
    RateLimitError,
    SpotThePhishError,
    ToolError,
    ToolUnavailableError,
    UnknownToolError,
)


def test_hierarchy():
    assert issubclass(InvalidDomainError, InvalidToolInputError)
    assert issubclass(UnknownToolError, InvalidToolInputError)
    for exc in (RateLimitError, MissingApiKeyError):
        assert issubclass(exc, ExternalServiceError) and issubclass(exc, ToolUnavailableError)
    for exc in (DatabaseUnavailableError, EvalCacheMissError, InvalidDomainError):
        assert issubclass(exc, ToolError) and issubclass(exc, SpotThePhishError)


def test_status_mapping():
    assert InvalidDomainError().tool_status == "error"
    assert RateLimitError().tool_status == "unavailable"
    assert "could not be run" in RateLimitError().finding()
    assert "rate limit" in RateLimitError().finding()


def test_custom_reason_and_default():
    assert str(DatabaseUnavailableError()) == DatabaseUnavailableError.default_reason
    assert ExternalServiceError("boom").reason == "boom"


def test_error_to_result():
    r = error_to_result("x", RateLimitError(), offline=False)
    assert (r.tool_name, r.status, r.is_offline) == ("x", "unavailable", False)


def test_boundary_converts_and_never_raises():
    @tool_boundary("demo", offline=True)
    def bad_input():
        raise InvalidDomainError()

    @tool_boundary("demo", offline=True)
    def bug():
        raise RuntimeError("secret-token-123")

    assert bad_input().status == "error"
    r = bug()
    assert r.status == "unavailable" and "secret-token-123" not in r.finding