"""Per-system knowledge base entries for RAG."""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .common import utcnow


class SystemKnowledgeDoc(SQLModel, table=True):
    __tablename__ = "system_knowledge_docs"
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="orgs.id", index=True)
    system_id: int = Field(foreign_key="systems.id", index=True)
    title: str
    content: str
    source_type: str = "manual"
    tags: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
