"""Normalize fleet instance target result keys.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-26

This is intentionally separate from the corrected 0004 backfill so environments
that applied 0004 during a rolling deployment are repaired as well.
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE experiments
        SET instances = COALESCE((
            SELECT json_agg(
                jsonb_set(
                    instance::jsonb,
                    '{targets}',
                    COALESCE((
                        SELECT jsonb_agg(jsonb_build_object(
                            'target_ref', target->>'target_ref',
                            'launched', COALESCE(
                                (target->>'launched')::integer,
                                (target->>'launched_pulls')::integer,
                                0
                            ),
                            'successful', COALESCE(
                                (target->>'successful')::integer,
                                (target->>'successful_pulls')::integer,
                                0
                            ),
                            'failed', COALESCE(
                                (target->>'failed')::integer,
                                (target->>'failed_pulls')::integer,
                                0
                            ),
                            'active', COALESCE((target->>'active')::integer, 0)
                        ))
                        FROM jsonb_array_elements(
                            COALESCE(instance::jsonb->'targets', '[]'::jsonb)
                        ) AS target
                    ), '[]'::jsonb)
                )
            )
            FROM json_array_elements(instances) AS instance
        ), '[]'::json)
        WHERE json_array_length(instances) > 0
        """
    )
    op.execute(
        """
        DELETE FROM experiment_events
        WHERE id IN (
            SELECT id
            FROM (
                SELECT
                    id,
                    event_type,
                    row_number() OVER (
                        PARTITION BY experiment_id, event_type
                        ORDER BY created_at DESC, id DESC
                    ) AS event_rank
                FROM experiment_events
                WHERE event_type IN ('progress', 'counter_snapshot')
            ) AS ranked
            WHERE
                (event_type = 'progress' AND event_rank > 1000)
                OR (event_type = 'counter_snapshot' AND event_rank > 500)
        )
        """
    )


def downgrade() -> None:
    # The concise keys are understood by both fleet code and the compatibility
    # normalizer; restoring the malformed shape would lose information.
    pass
