"""AI diagnosis schemas."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DiagnosisEvidenceItem(BaseModel):
    step: int = 0
    type: str
    label: str
    detail: str = ""
    status: str = "unknown"
    duration_ms: int = 0
    input: dict | str | list | None = None
    output: str = ""


class KnowledgeRefOut(BaseModel):
    name: str
    snippet: str = ""
    score: float | None = None
    content: str = ""
    truncated: bool = False


class DiagnoseRequest(BaseModel):
    question: str
    model_mode: Literal["auto", "default", "local", "api", "advanced"] = "auto"
    model_name: str = Field(default="", max_length=128)
    # 会话式追问：携带上一轮诊断报告 id，Agent 基于上轮结论回答当前问题（不重复取证）
    follow_up_report_id: int | None = None
    # 多模态：随问题上传的截图（data URL，最多 3 张）。Agent 读图后结合探活给结论。
    images: list[str] = Field(default_factory=list)


class ResumeRequest(BaseModel):
    """继续诊断的可选项（幂等：同一源报告只会真正执行一次）。"""

    force_refresh: bool = False
    model_mode: Literal["auto", "default", "local", "api", "advanced"] = "auto"
    model_name: str = Field(default="", max_length=128)


class DiagnoseResponse(BaseModel):
    id: int | None = None
    system_id: int
    status: str = "success"
    answer: str
    template_name: str = ""
    template_description: str = ""
    model: str = ""
    duration_ms: int = 0
    total_tokens: int = 0
    evidence_sources: list[str] = Field(default_factory=list)
    evidence_steps: list[str] = Field(default_factory=list)
    tool_calls: list[dict] = Field(default_factory=list)
    evidence: list[DiagnosisEvidenceItem] = Field(default_factory=list)
    knowledge_refs: list[KnowledgeRefOut] = Field(default_factory=list)
    business_context: dict = Field(default_factory=dict)
    error_message: str = ""
    # 中断（原生取消）与可恢复历史：messages_json/usage_json 属进程内传递，不对外序列化
    cancelled: bool = False
    messages_json: str = Field(default="", exclude=True)
    conversation_id: str = Field(default="", exclude=True)
    run_id: str = Field(default="", exclude=True)
    usage_json: str = Field(default="", exclude=True)


class DiagnosisReportOut(BaseModel):
    id: int
    system_id: int
    user_id: int | None = None
    external_request_id: str = ""
    report_type: str
    status: str
    question: str
    answer: str
    template_name: str = ""
    template_description: str = ""
    model: str = ""
    duration_ms: int = 0
    total_tokens: int = 0
    evidence_sources: list[str] = Field(default_factory=list)
    evidence_steps: list[str] = Field(default_factory=list)
    tool_calls: list[dict] = Field(default_factory=list)
    evidence: list[DiagnosisEvidenceItem] = Field(default_factory=list)
    knowledge_refs: list[KnowledgeRefOut] = Field(default_factory=list)
    business_context: dict = Field(default_factory=dict)
    error_message: str = ""
    # 中断：stopping 是派生态（cancel_requested_at 非空且 status == "running"）
    cancel_requested_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancel_phase: str = ""
    # 是否还有可恢复历史（硬杀/关停宽限期耗尽 → False，不得宣称"可继续"）
    resumable: bool = False
    # 派生态：当前是否**可以**继续（cancelled/failed 且有可恢复历史）
    can_resume: bool = False
    # 续跑链：本报告是从哪条报告继续来的
    resumed_from_report_id: int | None = None
    # 执行方进程标识（排障/证明"取消是否跨进程生效"用）
    runner_id: str = ""
    # 用量完整性（false = 含取不到的用量）+ 估算费用（价格表推算，非账单）
    usage_complete: bool = True
    usage_gap_reason: str = ""
    cost_usd_estimated: float | None = None
    # 会话累计（= 用量账本 SUM(delta)，与"本次 total_tokens"区分开，避免重复计数）
    conversation_tokens: int = 0
    conversation_cost_usd: float | None = None
    created_at: datetime


class DiagnosisHistoryClearOut(BaseModel):
    deleted: int


class DiagnosticTemplateOut(BaseModel):
    name: str
    description: str
    triggers: list[str] = Field(default_factory=list)
    steps: str
    enabled: bool = True


class DiagnosticTemplateSettingsUpdate(BaseModel):
    disabled_names: list[str] = Field(default_factory=list)


class DataAnalysisResponse(BaseModel):
    id: int | None = None
    system_id: int
    answer: str
    evidence: dict
    evidence_sources: list[str] = Field(default_factory=list)
    evidence_steps: list[str] = Field(default_factory=list)
    evidence_items: list[DiagnosisEvidenceItem] = Field(default_factory=list)


class ReadonlyDataset(BaseModel):
    """A named, pre-aggregated view the agent is allowed to query.

    The identifier field is `code` rather than `key` on purpose: field names
    containing "key" are treated as secrets by core.security and would be
    encrypted at rest / masked on read.
    """

    code: str = Field(min_length=1, max_length=64)
    label: str = ""
    view: str = ""
    description: str = ""
    date_column: str = "stat_date"
    filterable: list[str] = Field(default_factory=list)
    default_days: int = 7


class ReadonlyDatabaseConfig(BaseModel):
    enabled: bool = False
    name: str = ""
    database_url: str = ""
    table: str = ""
    timestamp_column: str = ""
    sensitive_fields: list[str] = Field(default_factory=list)
    max_rows: int = 20
    timeout_seconds: float = 5
    task_analysis_enabled: bool = False
    task_id_column: str = ""
    status_column: str = ""
    updated_at_column: str = ""
    worker_column: str = ""
    processing_values: list[str] = Field(default_factory=lambda: ["processing", "处理中"])
    stuck_threshold_minutes: int = 30
    worker_service_names: list[str] = Field(default_factory=list)
    datasets: list[ReadonlyDataset] = Field(default_factory=list)


class KnowledgeDocCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=50000)


class KnowledgeExportRequest(BaseModel):
    doc_name: str = Field(default="", max_length=128)


class KnowledgeDocOut(BaseModel):
    name: str
    size: int


class KnowledgeDocDetail(KnowledgeDocOut):
    content: str
