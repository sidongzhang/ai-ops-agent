"""自改进候选：把线上「答错 / 被差评 / 执行失败」的案例沉淀成**待人工采纳**的经验。

闭环：
  反馈（差评）/ 诊断失败
      → 生成候选（从当次回答里抽取症状 / 结论 / 处置，可人工编辑）
      → 人工审核：采纳 / 拒绝
      → 采纳：写入结构化记忆（source=human、高置信度）→ 下次同类问题检索命中
      → 拒绝：丢弃并留痕

与旧做法的区别：旧版是诊断一成功就把模型结论当"经验"回写（易污染）。
这里**必须人工过一道**才入库，且明确标注来源为人工确认。
"""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .common import utcnow

# 候选来源
SOURCE_FEEDBACK_DOWN = "feedback_down"  # 用户点了"没用"
SOURCE_FAILED = "failed"                # 诊断执行失败
SOURCE_MANUAL = "manual"                # 人工手动登记

# 审核状态
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"


class ImprovementCandidate(SQLModel, table=True):
    __tablename__ = "improvement_candidates"

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(index=True)
    system_id: int = Field(index=True)
    report_id: Optional[int] = Field(default=None, index=True)

    source: str = Field(default=SOURCE_FEEDBACK_DOWN, index=True)
    status: str = Field(default=STATUS_PENDING, index=True)

    # 原始问题与当次回答（留证：审核时可对照）
    question: str = Field(default="", max_length=1000)
    answer_excerpt: str = Field(default="", max_length=2000)

    # 拟写入记忆的结构化字段（人工可编辑后再采纳）
    symptom: str = Field(default="", max_length=500)
    root_cause: str = Field(default="", max_length=2000)
    remedy: str = Field(default="", max_length=2000)

    # 用户差评备注 / 失败原因
    note: str = Field(default="", max_length=2000)
    # 审核意见
    review_note: str = Field(default="", max_length=2000)
    reviewed_by: Optional[int] = Field(default=None, index=True)

    # 采纳后落库的记忆条目 id
    memory_id: Optional[int] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)
    reviewed_at: Optional[datetime] = Field(default=None)
