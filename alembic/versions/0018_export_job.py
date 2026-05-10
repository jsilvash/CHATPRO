"""Fase 23B: tabla export_jobs para exportación GDPR de datos del tenant.

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "export_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("storage_uri", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_export_jobs_tenant", "export_jobs", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_export_jobs_tenant", table_name="export_jobs")
    op.drop_table("export_jobs")
