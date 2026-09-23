from pydantic import BaseModel, Field


class UserRequest(BaseModel):
    """Minimal schema for validating incoming user input."""

    message: str = Field(..., description="The user's prompt or message.")


class AIResponse(BaseModel):
    """Minimal schema for structured response output from the AI service layer."""

    content: str = Field(..., description="The generated response text or user-friendly error message.")
    success: bool = Field(True, description="Flag indicating if the operation succeeded.")
    error_message: str | None = Field(None, description="Detailed error description if success is False.")
