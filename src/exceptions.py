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


class InvalidEmailInputError(SpotThePhishError):
    default_reason = "The input could not be read as an email."
    user_message = "Please paste the full email (ideally with headers) or upload an .eml file."

    def __init__(self, reason: str | None = None, user_message: str | None = None):
        super().__init__(reason)
        if user_message:
            self.user_message = user_message



class InvalidToolCallError(SpotThePhishError):
    default_reason = "The tool call was rejected."


class ModelError(SpotThePhishError):
    default_reason = "The AI model could not produce a usable answer."
    user_message = (
        "The AI analysis could not be completed, so SpotThePhish used its offline checks instead. "
        "This result is less thorough."
    )


class ModelUnavailableError(ModelError):
    default_reason = "The Ollama server is not reachable."
    user_message = (
        "The AI model is not available (is Ollama running?), so SpotThePhish "
        "used its offline checks instead. This result is less thorough."
    )


class ModelNotFoundError(ModelUnavailableError):
    default_reason = "The configured model is not installed in Ollama."
    user_message = (
        "The configured AI model is not installed in Ollama (run 'ollama pull <model>'), "
        "so SpotThePhish used its offline checks instead."
    )


class ModelTimeoutError(ModelUnavailableError):
    default_reason = "The model did not answer in time."
    user_message = (
        "The AI model took too long to answer, so SpotThePhish used its "
        "offline checks instead. This result is less thorough."
    )


class InvalidModelOutputError(ModelError):
    default_reason = "The model returned invalid output."

    def __init__(self, reason: str | None = None, raw_output: str = ""):
        super().__init__(reason)
        self.raw_output = raw_output


class VerdictValidationError(ModelError):
    default_reason = "The model's verdict was not supported by the evidence."

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems) or self.default_reason)
        self.problems = problems
