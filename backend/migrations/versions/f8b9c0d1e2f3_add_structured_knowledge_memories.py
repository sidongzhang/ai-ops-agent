"""结构化长期记忆 knowledge_memories（来源/置信度/有效性反馈闭环）

向量列维度取 EMBEDDING_DIM（与 knowledge_chunks 一致），换模型需重建索引。

Revision ID: f8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-09-23
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy import func

revision = "f8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def _dim() -> int:
    from app.core.config import settings

    return int(getattr(settings, "embedding_dim", None) or 1536)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "knowledge_memories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("symptom", sa.Text(), nullable=False, server_default=""),
        sa.Column("context", sa.Text(), nullable=False, server_default=""),
        sa.Column("root_cause", sa.Text(), nullable=False, server_default=""),
        sa.Column("remedy", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(16), nullable=False, server_default="model"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("validity", sa.String(24), nullable=False, server_default="unconfirmed"),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding", Vector(_dim()), nullable=True),
        sa.Column("diagnosis_report_id", sa.Integer(), nullable=True),
        sa.Column("workflow_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_knowledge_memories_org", "knowledge_memories", ["org_id"])
    op.create_index("ix_knowledge_memories_system", "knowledge_memories", ["system_id"])
    op.create_index(
        "ix_knowledge_memories_embedding",
        "knowledge_memories",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_memories_embedding", table_name="knowledge_memories")
    op.drop_index("ix_knowledge_memories_system", table_name="knowledge_memories")
    op.drop_index("ix_knowledge_memories_org", table_name="knowledge_memories")
    op.drop_table("knowledge_memories")
