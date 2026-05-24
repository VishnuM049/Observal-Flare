"""Add cloud_provider column to sites

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sites", sa.Column("cloud_provider", sa.String(10), nullable=False, server_default="aws"))
    op.alter_column("sites", "instance_id", type_=sa.String(64), existing_type=sa.String(32), existing_nullable=True)


def downgrade() -> None:
    op.alter_column("sites", "instance_id", type_=sa.String(32), existing_type=sa.String(64), existing_nullable=True)
    op.drop_column("sites", "cloud_provider")
