from __future__ import annotations
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Literal

#Basic service request/response

class UserRequest(BaseModel):
    """Schema for validating incoming user input."""

    message: str = Field(..., description="Raw email text or the user's prompt.")


class AIResponse(BaseModel):
    """Schema for basic service responses."""

    content: str = Field(..., description="The generated response text or error message.")
    success: bool = Field(True, description="Flag indicating if the operation succeeded.")
    error_message: Optional[str] = Field(None, description="Detailed error description if success is False.")

#email components for email_parser and email tools

class EmailAddress(BaseModel):
    """Structured representation of an email address."""
    display_name : str = ""
    address: str = ""
    domain: str = ""

    def __str__(self) -> str:
        if self.display_name and self.address:
            return f"{self.display_name} <{self.address}>"
        return self.address or ""

class AuthenticationResults(BaseModel):
    """Results extracted from email authentication headers (SPF, DKIM, DMARC)."""
    spf_status : Optional[str] = None  #pass, fail, softfail, neutral, none, temperror, permerror
    spf_details : Optional[str] = None
    dkim_status : Optional[str] = None  #pass, fail, none, permerror
    dkim_details: Optional[str] = None
    dmarc_status : Optional[str] = None  #pass, fail, none, bestguesspass
    dmarc_details : Optional[str] = None 
    arc_status : Optional[str] = None  #pass, fail, none
    raw_headers : Dict[str, List[str]] = Field(default_factory = dict)

class LinkInfo(BaseModel):
    """Extracted hyperlink metadata with security indicators"""
    url: str
    display_text: str = ""
    domain: str = ""
    is_ip_address: bool = False
    is_shortener: bool = False
    is_punycode: bool = False
    text_mismatch: bool = False
    risk_score : float = Field(default= 0.0, ge= 0.0, le= 100.0)
    risk_reasons: List[str] = Field(default_factory = list)

class AttachmentMetadata(BaseModel):
    """Attachment metadata inspected without opening or executing files"""
    filename: str
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    extension: str = ""
    double_extension: bool = False
    is_executable_or_script: bool = False
    is_archive: bool = False
    is_macro_enabled : bool = False
    risk_flag: bool = False
    risk_reasons: List[str] = Field(default_factory = list)

class ParsedEmail(BaseModel):
    """Structured email produced by email_parser.py"""
    raw_text: str = ""
    subject: str = ""
    sender: EmailAddress = Field(default_factory = EmailAddress)
    reply_to: Optional[EmailAddress] = None
    return_path: Optional[EmailAddress] = None
    recipients: List[EmailAddress] = Field(default_factory = list)
    date: Optional[str] = None
    message_id: Optional[str] = None
    body_plain: str = ""
    body_html: str = ""
    links: List[LinkInfo] = Field(default_factory = list)
    attachments: List[AttachmentMetadata] = Field(default_factory = list)
    auth_results: AuthenticationResults = Field(default_factory = AuthenticationResults)
    headers: Dict[str, str] = Field(default_factory = dict)
    has_headers: bool = True

#for all tools

class ToolResult(BaseModel):
    """Standard output for all analytical tools, both offline email and online domain tools"""
    tool_name: str
    status: Literal["success", "warning", "error", "unavailable"] = "success"
    finding: str
    risk_score: float = Field(default = 0.0, ge = 0.0, le = 100.0)
    evidence: List[str] = Field(default_factory = list)
    details: Dict[str, Any] = Field(default_factory = dict)
    is_offline: bool = True


#final verdict

VerdictType = Literal["likely safe", "suspicious", "phishing"]

class Verdict(BaseModel):
    """Final decision displayed in UI and evaluated in benchmarks."""
    verdict: VerdictType
    risk_score: float = Field(ge = 0.0, le = 100.0)
    confidence: float = Field(default = 0.85, ge = 0.0, le = 1.0)
    explanation: str
    evidence: List[str] = Field(default_factory = list)
    tools_run: List[str] = Field(default_factory = list)
    is_fallback: bool = False
    unavailable_tools: List[str] = Field(default_factory = list)
    timestamp: str = Field(default_factory = lambda: datetime.now(timezone.utc).isoformat())
