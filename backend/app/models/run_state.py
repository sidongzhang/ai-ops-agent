"""诊断运行状态（框架原生可恢复历史）。

存的是 pydantic-ai 的**完整消息历史**（`all_messages_json()`，含 ModelRequest/ModelResponse/
ToolCallPart/ToolReturnPart，即已完成工具结果），不是"证据摘要"。继续时把它作为
`message_history=` 传回，并由框架自动修复悬空工具调用（合成 outcome='interrupted' 的结果）。

与 `diagnosis_reports` 分行存放：历史 JSON 可能很大，列表查询不需要它。
"""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .common import utcnow


class DiagnosisRunState(SQLModel, table=True):
    __tablename__ = "diagnosis_run_state"

    report_id: int = Field(primary_key=True, foreign_key="diagnosis_reports.id")
    messages_json: str = Field(default="")
    conversation_id: str = Field(default="", max_length=64)
    run_id: str = Field(default="", max_length=64)
    # 框架返回的**累计**用量（resume 时作为 baseline；会话累计由 ledger 的增量推导，不重复累加）
    framework_cumulative_json: str = Field(default="")
    # false = 进程被硬杀/宽限期耗尽，历史没拿到（不得对外宣称"可继续"）
    resumable: bool = Field(default=True)
    updated_at: datetime = Field(default_factory=utcnow)
    created_at: Optional[datetime] = Field(default=None)
