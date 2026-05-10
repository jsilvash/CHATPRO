"""Fase 6: agregar columna embedding VECTOR(1024) a tabla products.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-10
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Asegurar extensión pgvector (idempotente)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Agregar columna embedding (nullable: productos pre-Fase-6 no tienen embedding aún)
    op.execute(
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS embedding vector(1024)"
    )

    # Índice HNSW para búsqueda de similaridad coseno.
    # No se usa CONCURRENTLY porque Alembic opera dentro de una transacción;
    # en producción se puede recrear CONCURRENTLY fuera de la migración si la tabla es grande.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_embedding_hnsw "
        "ON products USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_products_embedding_hnsw")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS embedding")
