from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.responses import ToolResult, Verdict, VerdictType


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=1,
        max_length=500,
        description="One sentence: why this is the right next step.",
    )
    action: Literal["call_tool", "finish"]
    tool_name: Optional[str] = Field(
        default=None,
        description="The tool to run. Required when action is call_tool.",
    )
    arguments: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments for the tool. Use {} for tools without arguments.",
    )

    @field_validator("arguments", mode="before")
    @classmethod
    def missing_arguments_mean_none(cls, value):
        if value is None:
            return {}
        return value

    @model_validator(mode="after")
    def tool_name_required_for_tool_calls(self) -> "AgentDecision":
        if self.action == "call_tool" and not self.tool_name:
            raise ValueError("tool_name is required when action is call_tool.")
        return self


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["tool", "email_text"]
    observation_id: Optional[int] = Field(
        default=None,
        description="Observation number. Required when source is tool.",
    )
    quote: Optional[str] = Field(
        default=None,
        max_length=300,
        description="Exact text from the email. Required when source is email_text.",
    )
    claim: str = Field(
        min_length=1,
        max_length=300,
        description="What this evidence shows, in plain language.",
    )


class ModelVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: VerdictType
    risk_score: float = Field(ge=0, le=100)
    explanation: str = Field(
        min_length=1,
        max_length=1500,
        description="Plain-language explanation for a non-technical user.",
    )
    recommended_action: str = Field(
        min_length=1,
        max_length=400,
        description="What the user should do now.",
    )
    evidence: List[EvidenceCitation] = Field(default_factory=list, max_length=8)


class Observation(BaseModel):
    observation_id: int
    result: ToolResult


StepKind = Literal[
    "automatic_check",
    "tool_call",
    "rejected_call",
    "repeated_call",
    "invalid_output",
    "step_limit",
    "final_answer",
]


class AgentStep(BaseModel):
    step: int
    kind: StepKind
    reason: str = ""
    tool_name: Optional[str] = None
    arguments: Dict[str, Any] = Field(default_factory=dict)
    observation_id: Optional[int] = None
    status: Optional[str] = None
    summary: str = ""
    duration_ms: int = 0


class AnalyzerOutput(BaseModel):
    analyzer_name: str
    verdict: Verdict
    trace: List[AgentStep] = Field(default_factory=list)
    observations: List[Observation] = Field(default_factory=list)
    model_calls: int = 0


class AnalysisStatus(str, Enum):
    COMPLETED = "completed"
    FALLBACK = "fallback"
    REJECTED = "rejected"
    FAILED = "failed"


class AnalysisResult(BaseModel):
    status: AnalysisStatus
    verdict: Optional[Verdict] = None
    trace: List[AgentStep] = Field(default_factory=list)
    observations: List[Observation] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    message: str = ""
    analyzer_name: str = ""
    model_calls: int = 0
    duration_ms: int = 0

    @classmethod
    def rejected(cls, message: str, duration_ms: int = 0) -> "AnalysisResult":
        return cls(status=AnalysisStatus.REJECTED, message=message, duration_ms=duration_ms)

    @classmethod
    def failed(cls, message: str, duration_ms: int = 0) -> "AnalysisResult":
        return cls(status=AnalysisStatus.FAILED, message=message, duration_ms=duration_ms)
