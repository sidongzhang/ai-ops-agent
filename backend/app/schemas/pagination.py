"""Shared paginated list response models."""
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageOut(BaseModel, Generic[T]):
    items: list[T] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 50
