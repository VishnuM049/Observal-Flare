"""Add fleet support to isolated GHCR experiments.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-26
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column("instance_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "experiments",
        sa.Column(
            "instances",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )

    # Preserve visibility for an in-flight single-instance experiment during a
    # rolling deploy. Historical destroyed runs have no retained instance ID.
    op.execute(
        """
        UPDATE experiments
        SET instances = json_build_array(json_build_object(
            'index', 0,
            'instance_id', instance_id,
            'status', CASE WHEN status = 'running' THEN 'running' ELSE status::text END,
            'cleanup_status', CASE WHEN destroyed_at IS NULL THEN 'not_started' ELSE 'destroyed' END,
            'launched_pulls', launched_pulls,
            'successful_pulls', successful_pulls,
            'failed_pulls', failed_pulls,
            'active_pulls', active_pulls,
            'max_concurrency', COALESCE(max_concurrency, 0),
            'last_progress_at', last_progress_at,
            'error_message', error_message,
            'targets', COALESCE((
                SELECT json_agg(json_build_object(
                    'target_ref', target->>'target_ref',
                    'launched', COALESCE((target->>'launched_pulls')::integer, 0),
                    'successful', COALESCE((target->>'successful_pulls')::integer, 0),
                    'failed', COALESCE((target->>'failed_pulls')::integer, 0),
                    'active', 0
                ))
                FROM json_array_elements(experiments.targets) AS target
            ), '[]'::json)
        ))
        WHERE instance_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("experiments", "instances")
    op.drop_column("experiments", "instance_count")
