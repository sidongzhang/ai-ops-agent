"""自改进候选的请求/响应模型。"""
from datetime import datetime

from pydantic import BaseModel, Field


class FeedbackRequest(BaseModel):
    rating: str = Field(pattern="^(up|down)$", description="up=有帮助 / down=没帮助")
    note: str = Field(default="", max_length=2000)


class FeedbackOut(BaseModel):
    report_id: int
    feedback: str
    candidate_id: int | None = None


class ImprovementCandidateOut(BaseModel):
    id: int
    system_id: int
    report_id: int | None
    source: str
    status: str
    question: str
    answer_excerpt: str
    symptom: str
    root_cause: str
    remedy: str
    note: str
    review_note: str
    memory_id: int | None
    created_at: datetime
    reviewed_at: datetime | None


class ImprovementReviewRequest(BaseModel):
    """审核（采纳）时可编辑候选内容；不传则用候选原值。"""

    symptom: str | None = Field(default=None, max_length=500)
    root_cause: str | None = Field(default=None, max_length=2000)
    remedy: str | None = Field(default=None, max_length=2000)
    review_note: str = Field(default="", max_length=2000)


class ImprovementRejectRequest(BaseModel):
    review_note: str = Field(default="", max_length=2000)


class ImprovementStatsOut(BaseModel):
    pending: int
    approved: int
    rejected: int
