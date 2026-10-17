"""Schemas for external system OpenAPI."""
from typing import Any, Literal

from pydantic import BaseModel, Field

from .health import HealthItem
from .messages import SystemMessageOut
from .pagination import PageOut


class OpenAlertIn(BaseModel):
    request_id: str
    title: str
    summary: str = ""
    content: str = ""
    severity: str = "warning"
    need_llm_process: bool = False
    context: dict = Field(default_factory=dict)


class OpenHealthIn(BaseModel):
    request_id: str = ""
    services: list[HealthItem]


class LogAnalysisResponse(BaseModel):
    request_id: str
    system_id: int
    filename: str
    log_format: str
    report: str
    model: str = ""
    duration_ms: int = 0
    truncated: bool = False


class LogAnalysisAcceptedResponse(BaseModel):
    request_id: str
    system_id: int
    message_id: int
    filename: str
    log_format: str
    status: str = "queued"
    truncated: bool = False
    detail: str = "日志已接收，平台将在后台分析，结果会回写到消息中心。"


class OpenMessageIn(BaseModel):
    request_id: str
    title: str
    summary: str = ""
    content: str = ""
    severity: str = "info"
    need_llm_process: bool = False
    context: dict = Field(default_factory=dict)


class OpenMessageStatusIn(BaseModel):
    status: str = Field(description="read / acknowledged / resolved")


class OpenProductionReportIn(BaseModel):
    request_id: str
    title: str = ""
    period: str = ""
    summary: str = ""
    data: dict | list = Field(default_factory=dict)
    context: dict = Field(default_factory=dict)


class OpenMessagePageOut(PageOut[SystemMessageOut]):
    pass


class OpenResumeIn(BaseModel):
    """外部继续诊断的可选项。"""

    force_refresh: bool = False
    model_mode: Literal["auto", "default", "local", "api", "advanced"] = "auto"


class OpenDiagnosisIn(BaseModel):
    idempotency_key: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9._:-]*$")
    chat_id: str = Field(default="", max_length=64)
    question: str = Field(min_length=1, max_length=1000)
    request_id: str = Field(default="", max_length=255)
    actor_id: str = Field(default="", max_length=128)
    model_mode: Literal["auto", "default", "local", "api", "advanced"] = "auto"
    # Generated clients may serialize an omitted optional snapshot as JSON null.
    business_context: dict[str, Any] | None = None
    # 会话式追问：带上上一轮诊断报告 id，平台会把「上一轮问题+结论」注入本轮上下文。
    follow_up_report_id: int | None = None
    # 多模态：随问题上传的截图（data URL，最多 3 张）。
    images: list[str] = Field(default_factory=list)
