from __future__ import annotations


class SpotThePhishError(Exception):
    default_reason = "An unexpected error occurred."

    def __init__(self, reason: str | None = None):
        self.reason = reason or self.default_reason
        super().__init__(self.reason)


class ToolError(SpotThePhishError):
    tool_status = "error"

    def finding(self) -> str:
        return self.reason


class InvalidToolInputError(ToolError):
    default_reason = "Invalid input; the check was not run."


class InvalidDomainError(InvalidToolInputError):
    default_reason = "Invalid domain name; the check was not run."


class InvalidUrlError(InvalidToolInputError):
    default_reason = "Invalid URL. Only valid HTTP and HTTPS URLs are supported."


class UnknownToolError(InvalidToolInputError):
    default_reason = "Unknown tool."


class ToolUnavailableError(ToolError):
    tool_status = "unavailable"
    default_reason = "The check is currently unavailable."

    def finding(self) -> str:
        return f"Check could not be run. {self.reason}"


class ExternalServiceError(ToolUnavailableError):
    default_reason = "The external service failed."


class MissingApiKeyError(ExternalServiceError):
    default_reason = "No API key is configured."


class ServiceTimeoutError(ExternalServiceError):
    default_reason = "The request timed out or the network is unavailable."


class RateLimitError(ExternalServiceError):
    default_reason = "The external service rate limit was reached."


class ServiceResponseError(ExternalServiceError):
    default_reason = "The external service returned an unexpected response."


class DatabaseUnavailableError(ToolUnavailableError):
    default_reason = "The case history database is not reachable."


class McpServerUnavailableError(ToolUnavailableError):
    default_reason = "The MCP tool server is not reachable."


class EvalCacheMissError(ToolUnavailableError):
    default_reason = "No cached result exists for this input in replay mode."