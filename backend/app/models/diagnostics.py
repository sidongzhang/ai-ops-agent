"""Persisted AI diagnosis and data-analysis reports."""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column, Index, text
from sqlmodel import Field, SQLModel

from .common import utcnow


class DiagnosisReport(SQLModel, table=True):
    __tablename__ = "diagnosis_reports"
    __table_args__ = (Index("uq_diagnosis_resumed_from", "resumed_from_report_id", unique=True,
                           postgresql_where=text("resumed_from_report_id IS NOT NULL")),)

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="orgs.id", index=True)
    system_id: int = Field(foreign_key="systems.id", index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
    external_request_id: str = Field(default="", index=True, max_length=255)
    report_type: str = Field(default="diagnose", index=True)
    status: str = Field(default="success", index=True)
    question: str
    answer: str = ""
    template_name: str = ""
    template_description: str = ""
    model: str = ""
    duration_ms: int = 0
    total_tokens: int = 0
    evidence_sources: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    evidence_steps: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    tool_calls: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    evidence: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    knowledge_refs: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    answer_draft: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    business_context: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error_message: str = ""
    # 中断（stopping 是**派生**状态：cancel_requested_at 非空且 status == "running"，
    # 不写进 status 字段，保证旧前端/旧外部链路"status 仍是 running 就继续轮询"的语义不变）
    cancel_requested_at: Optional[datetime] = Field(default=None)
    cancelled_at: Optional[datetime] = Field(default=None)
    # 取消时的执行相位：model_request / tool_exec / idle（用于判定用量完整性与 gap 原因）
    cancel_phase: str = Field(default="", max_length=16)
    # 运行心跳：reaper 据此判断"是否还有活着的 worker 在推进"，**不再只看创建时间**
    runner_id: str = Field(default="", max_length=64)
    runner_heartbeat_at: Optional[datetime] = Field(default=None)
    # 继续（resume）：指向被继续的源报告；**唯一**约束保证"重复点击只启动一次"
    resumed_from_report_id: Optional[int] = Field(default=None, index=True)
    # 用量完整性：有 usage ≠ 用量完整（取消落在模型请求在途时该请求用量不可得）
    usage_complete: bool = Field(default=True)
    usage_gap_reason: str = Field(default="", max_length=64)
    cost_usd_estimated: Optional[float] = Field(default=None)
    price_table_version: str = Field(default="", max_length=64)
    # 用户反馈：""（未评）/ "up" / "down"。差评会生成自改进候选。
    feedback: str = Field(default="", index=True, max_length=16)
    feedback_note: str = Field(default="", max_length=2000)
    feedback_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, index=True)

    @property
    def unverified_reason(self) -> str:
        """派生（不落库）：`success` 但**没有实际取证**时给出原因码。

        为什么需要：模型（尤其本地小模型）会给出"没干活却像干完了"的结论，例如只回
        「我没有获取数据的能力」或凭常识编一段，而报告状态仍是 `success`。
        实测：本地 3B 对"TCAT 切割任务有几条没完成"未调用任何工具就作答（#268）。

        判据（用 206 条历史报告校准过，命中 2 条、误报 0 条）：
          status=success + 结论非空 + **无工具调用 + 无证据步 + 无 evidence_sources**
          + 答案里**没有引用**本次检索到的任何知识库文档（按 `knowledge_refs[].name` 判断）。
        最后一条很关键：纯知识库回答会明确写出文档名（如"依据 多星联合分析流程_v3.md"），
        那是有依据的，不能标；而"我拿不到数据"式的回答不会引用任何文档。
        采用**读取时派生**而非加列：历史报告立刻生效，无需迁移。
        """
        if self.status != "success" or not (self.answer or "").strip():
            return ""
        if (self.business_context or {}).get('interaction',{}).get('resolved')=='chat':
            return ''
        if self.tool_calls or self.evidence or self.evidence_sources:
            return ""
        if _answer_cites_knowledge(self.answer, self.knowledge_refs):
            return ""
        return "no_tool_evidence"


def _answer_cites_knowledge(answer: str, refs: list | None) -> bool:
    """答案里是否引用了本次检索到的知识库文档（`name` 或去掉扩展名的文件名）。"""
    text = answer or ""
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        name = str(ref.get("name") or ref.get("title") or "").strip()
        if not name:
            continue
        if name in text:
            return True
        stem = name.rsplit(".", 1)[0].strip()
        if len(stem) >= 6 and stem in text:
            return True
    return False
