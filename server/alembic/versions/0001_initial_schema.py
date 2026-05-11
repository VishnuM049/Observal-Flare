"""Initial schema — users, sites, audit_logs

Revision ID: 0001
Revises:
Create Date: 2026-05-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON, UUID

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), unique=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "role",
            sa.Enum("admin", "member", name="userrole"),
            nullable=False,
            server_default="member",
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- sites ---
    site_status_enum = sa.Enum(
        "pending", "provisioning", "deploying", "running", "stopping",
        "stopped", "sleeping", "destroying", "destroyed", "failed",
        name="sitestatus",
    )
    deploy_type_enum = sa.Enum("branch", "commit", "pr", "tag", "release", name="deploytype")
    sleep_mode_enum = sa.Enum("none", "nightly", "idle", name="sleepmode")

    op.create_table(
        "sites",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(63), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("status", site_status_enum, nullable=False, server_default="pending"),
        sa.Column("requestor_email", sa.String(320), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        # Deploy source
        sa.Column("deploy_type", deploy_type_enum, nullable=False),
        sa.Column("deploy_ref", sa.String(255), nullable=False),
        sa.Column("resolved_sha", sa.String(40), nullable=True),
        # Auto-update
        sa.Column("auto_update", sa.Boolean, nullable=False, server_default=sa.text("false")),
        # Redeploy behavior
        sa.Column("auto_wipe_on_failure", sa.Boolean, nullable=False, server_default=sa.text("true")),
        # Sleep/wake
        sa.Column("sleep_mode", sleep_mode_enum, nullable=False, server_default="none"),
        # Auto-teardown
        sa.Column("scheduled_destroy_at", sa.DateTime(timezone=True), nullable=True),
        # Environment configuration
        sa.Column("env_overrides", JSON, nullable=False, server_default="{}"),
        sa.Column("instance_size", sa.String(20), nullable=False, server_default="t3.large"),
        # Idle callback auth
        sa.Column("idle_token", sa.String(64), nullable=True),
        # AWS resources
        sa.Column("instance_id", sa.String(32), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("terraform_state_key", sa.String(255), nullable=True),
        # Stale site reminders
        sa.Column("ttl_days", sa.Integer, nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        # Lifecycle
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("destroyed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_deployed_at", sa.DateTime(timezone=True), nullable=True),
        # Error tracking
        sa.Column("error_message", sa.String(2000), nullable=True),
        sa.Column("provision_log", sa.Text, nullable=True),
    )

    # --- audit_logs ---
    op.create_table(
        "audit_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", UUID(as_uuid=True), sa.ForeignKey("sites.id"), nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("details", JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Indexes for common queries
    op.create_index("ix_sites_name_active", "sites", ["name"], unique=True, postgresql_where=sa.text("status != 'destroyed'"))
    op.create_index("ix_sites_status", "sites", ["status"])
    op.create_index("ix_sites_deploy_type_ref", "sites", ["deploy_type", "deploy_ref"])
    op.create_index("ix_sites_scheduled_destroy", "sites", ["scheduled_destroy_at"], postgresql_where=sa.text("scheduled_destroy_at IS NOT NULL"))
    op.create_index("ix_audit_logs_site_id", "audit_logs", ["site_id"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("sites")
    op.drop_table("users")
    sa.Enum(name="sitestatus").drop(op.get_bind())
    sa.Enum(name="deploytype").drop(op.get_bind())
    sa.Enum(name="sleepmode").drop(op.get_bind())
    sa.Enum(name="userrole").drop(op.get_bind())
