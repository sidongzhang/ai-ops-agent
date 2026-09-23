"""结构化长期记忆（带来源、置信度与有效性反馈的故障经验条目）。

设计要点（解决"记忆污染"问题——旧版把模型生成结论全文回写知识库当权威依据）：
  * 每条记忆是结构化字段（症状/根因/处置），不是全文追加
  * source 标注来源：model（模型生成）/ human（人工确认）/ imported
  * confidence 置信度：模型生成 0.5，人工确认 1.0
  * validity 有效性：工作流执行后回查结果自动回填——
      confirmed_success（处置有效，检索加权提高）/ unconfirmed / failed（处置无效，检索时直接排除）
  * 检索权重 = 向量相似度 × confidence × validity 惩罚
"""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .common import utcnow

MEMORY_SOURCE_MODEL = "model"
MEMORY_SOURCE_HUMAN = "human"

VALIDITY_UNCONFIRMED = "unconfirmed"
VALIDITY_CONFIRMED = "confirmed_success"
VALIDITY_FAILED = "failed"

CONFIDENCE_MODEL = 0.5
CONFIDENCE_HUMAN = 1.0
CONFIDENCE_VERIFIED = 0.9


class KnowledgeMemory(SQLModel, table=True):
    __tablename__ = "knowledge_memories"

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(index=True)
    system_id: int = Field(index=True)

    symptom: str = Field(default="", description="症状/触发条件（检索主字段）")
    context: str = Field(default="", max_length=2000)
    root_cause: str = Field(default="", max_length=2000)
    remedy: str = Field(default="", max_length=2000)

    source: str = Field(default=MEMORY_SOURCE_MODEL)          # model / human / imported
    confidence: float = Field(default=CONFIDENCE_MODEL)       # 0~1
    validity: str = Field(default=VALIDITY_UNCONFIRMED)       # unconfirmed / confirmed_success / failed
    hit_count: int = Field(default=0)

    # embedding 向量列由 Alembic 迁移管理（pgvector, 维度=EMBEDDING_DIM）；
    # 模型层不声明该列（SQLite 兜底建表不含向量列，记忆功能在 SQLite 场景降级为不可用）。
    diagnosis_report_id: Optional[int] = Field(default=None, index=True)
    workflow_id: Optional[int] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
