"""Authentication and tenant models."""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .common import utcnow


class Org(SQLModel, table=True):
    __tablename__ = "orgs"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=utcnow)


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    hashed_password: str
    org_id: int = Field(foreign_key="orgs.id", index=True)
    role: str = Field(default="owner")
    created_at: datetime = Field(default_factory=utcnow)
