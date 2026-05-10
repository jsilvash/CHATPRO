"""fase 9: tablas kb_documents y kb_chunks (RAG genérico)

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "kb_documents",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("wa_number_id", UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wa_number_id"], ["wa_numbers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kb_documents_tenant_id", "kb_documents", ["tenant_id"])
    op.create_index(
        "ix_kb_documents_tenant_number",
        "kb_documents",
        ["tenant_id", "wa_number_id"],
    )

    op.create_table(
        "kb_chunks",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", UUID(as_uuid=True), nullable=False),
        sa.Column("wa_number_id", UUID(as_uuid=True), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "embedding",
            sa.Text(),  # placeholder; tipo real asignado via DDL raw
            nullable=False,
        ),
        sa.Column("metadata_", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["document_id"], ["kb_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Reemplaza la columna TEXT por VECTOR(1024)
    op.execute("ALTER TABLE kb_chunks ALTER COLUMN embedding TYPE vector(1024) USING NULL::vector(1024)")
    # Columna tsvector generada para BM25
    op.execute(
        "ALTER TABLE kb_chunks ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('spanish', coalesce(content, ''))) STORED"
    )
    op.create_index("ix_kb_chunks_tenant_doc", "kb_chunks", ["tenant_id", "document_id"])
    op.execute(
        "CREATE INDEX ix_kb_chunks_embedding ON kb_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX ix_kb_chunks_content_tsv ON kb_chunks USING gin(content_tsv)"
    )


def downgrade() -> None:
    op.drop_index("ix_kb_chunks_content_tsv", table_name="kb_chunks")
    op.drop_index("ix_kb_chunks_embedding", table_name="kb_chunks")
    op.drop_index("ix_kb_chunks_tenant_doc", table_name="kb_chunks")
    op.drop_table("kb_chunks")
    op.drop_index("ix_kb_documents_tenant_number", table_name="kb_documents")
    op.drop_index("ix_kb_documents_tenant_id", table_name="kb_documents")
    op.drop_table("kb_documents")
