"""AI diagnosis schemas."""
from pydantic import BaseModel, Field


class DiagnoseRequest(BaseModel):
    question: str


class DiagnoseResponse(BaseModel):
    system_id: int
    answer: str
    template_name: str = ""
    template_description: str = ""
    duration_ms: int = 0
    evidence_sources: list[str] = Field(default_factory=list)


class DiagnosticTemplateOut(BaseModel):
    name: str
    description: str
    triggers: list[str] = Field(default_factory=list)
    steps: str
    enabled: bool = True


class DiagnosticTemplateSettingsUpdate(BaseModel):
    disabled_names: list[str] = Field(default_factory=list)


class DataAnalysisRequest(BaseModel):
    question: str


class DataAnalysisResponse(BaseModel):
    system_id: int
    answer: str
    evidence: dict


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
