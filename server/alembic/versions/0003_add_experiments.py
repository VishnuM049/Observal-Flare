"""Add isolated GHCR experiments.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

experiment_status = postgresql.ENUM(
    "pending",
    "provisioning",
    "running",
    "destroying",
    "completed",
    "failed",
    "cleanup_failed",
    "cancelled",
    name="experiment_status",
    create_type=False,
)


def upgrade() -> None:
    experiment_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", experiment_status, nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_ref", sa.String(length=500), nullable=False),
        sa.Column("package_url", sa.String(length=500), nullable=False),
        sa.Column("targets", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("rate_per_minute", sa.Integer(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("expected_pulls", sa.Integer(), nullable=False),
        sa.Column("concurrency_limit", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("image_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("layer_count", sa.Integer(), nullable=False),
        sa.Column("estimated_transfer_bytes", sa.BigInteger(), nullable=False),
        sa.Column("instance_type", sa.String(length=32), nullable=False),
        sa.Column("launched_pulls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successful_pulls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_pulls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_pulls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_concurrency", sa.Integer(), nullable=True),
        sa.Column("baseline_count", sa.Integer(), nullable=True),
        sa.Column("immediate_count", sa.Integer(), nullable=True),
        sa.Column("delayed_count", sa.Integer(), nullable=True),
        sa.Column("results", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("instance_id", sa.String(length=64), nullable=True),
        sa.Column("terraform_state_key", sa.String(length=255), nullable=False),
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("destroyed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_counter_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("cleanup_error", sa.String(length=2000), nullable=True),
        sa.Column("run_log", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_experiments_single_active",
        "experiments",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'provisioning', 'running', 'destroying')"),
    )
    op.create_table(
        "experiment_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("payload", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_experiment_events_experiment_created",
        "experiment_events",
        ["experiment_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_experiment_events_experiment_created", table_name="experiment_events")
    op.drop_table("experiment_events")
    op.drop_index("ix_experiments_single_active", table_name="experiments")
    op.drop_table("experiments")
    experiment_status.drop(op.get_bind(), checkfirst=True)
