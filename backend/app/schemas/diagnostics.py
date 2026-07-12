"""AI diagnosis schemas."""
from datetime import datetime

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


class DiagnoseResponse(BaseModel):
    id: int | None = None
    system_id: int
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


class DiagnosisReportOut(BaseModel):
    id: int
    system_id: int
    user_id: int | None = None
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
    error_message: str = ""
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
