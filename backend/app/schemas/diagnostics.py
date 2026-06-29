"""AI diagnosis schemas."""
from pydantic import BaseModel


class DiagnoseRequest(BaseModel):
    question: str


class DiagnoseResponse(BaseModel):
    system_id: int
    answer: str
