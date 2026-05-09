import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin

# ────────────────────────────────────────────────────────────
# Tablas multi-tenant raíz
# ────────────────────────────────────────────────────────────


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    plan: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="free")
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default="true")
    settings_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )

    users: Mapped[list["User"]] = relationship(
        "User", back_populates="tenant", cascade="all, delete-orphan"
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(sa.Text, nullable=False)
    hashed_password: Mapped[str] = mapped_column(sa.Text, nullable=False)
    full_name: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="")
    # owner | admin | agent
    role: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="agent")
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default="true")

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="users")

    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        sa.Index("ix_users_email", "email"),
    )
