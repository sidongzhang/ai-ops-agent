"""pgvector RAG: knowledge_chunks + knowledge_index_meta

- knowledge_chunks: 分块 + 向量（维度取 EMBEDDING_DIM，默认 1536），HNSW 余弦索引
- knowledge_index_meta: 每系统索引指纹，文档变更时触发增量重建

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-09-22
"""
import os

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def _dim() -> int:
    # 迁移在应用启动时执行，必须与当前 embedding 模型维度一致；换模型需新增迁移并重建索引。
    # 优先读 settings（会加载仓库根目录 .env 的 EMBEDDING_DIM），环境变量兜底。
    from app.core.config import settings

    return int(getattr(settings, "embedding_dim", None) or os.environ.get("EMBEDDING_DIM", "1536"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_index_meta",
        sa.Column("system_id", sa.Integer(), primary_key=True),
        sa.Column("fingerprint", sa.String(32), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(32), nullable=False),
        sa.Column("embedding", Vector(_dim()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("system_id", "source", "chunk_index", name="uq_knowledge_chunk"),
    )
    op.create_index("ix_knowledge_chunks_system", "knowledge_chunks", ["system_id"])
    op.create_index(
        "ix_knowledge_chunks_embedding",
        "knowledge_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_embedding", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_system", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_index_meta")
