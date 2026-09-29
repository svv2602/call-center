"""Add data_sync_state and the tyre-test tables for the tshina Data API sync.

``data_sync_state`` — one row per synced resource (``eu-labels``,
``tire-tests``, ``vehicles/brands`` …): the watermark the next incremental
run passes as ``updated_since`` (``server_time`` of the first page of the
last fully applied walk), when the last attempt and the last success were,
the last error and the counts of the last successful run.

``tire_tests`` / ``tire_test_placements`` — tyre tests (ADAC and others)
from ``GET /api/v1/tire-tests``: one test with all its places. Places are
rewritten whole on every upsert of the test. ``source`` keeps the source
object exactly as sent, ``scale_min``/``scale_max``/``higher_is_better``
included: ``higher_is_better`` is null while tshina has not set the scale
direction (all sources on 2026-09-29), and nothing may read a direction
from it until it is set.

Revision ID: 065
Revises: 064
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "065"
down_revision: str | None = "064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE data_sync_state (
            resource VARCHAR(64) PRIMARY KEY,
            watermark TIMESTAMPTZ,
            last_success_at TIMESTAMPTZ,
            last_attempt_at TIMESTAMPTZ,
            last_error TEXT,
            upserted INTEGER NOT NULL DEFAULT 0,
            deleted INTEGER NOT NULL DEFAULT 0,
            full_sync_at TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE TABLE tire_tests (
            id INTEGER PRIMARY KEY,
            source_key VARCHAR(64) NOT NULL,
            source_title VARCHAR(200),
            source JSONB NOT NULL DEFAULT '{}'::jsonb,
            year SMALLINT,
            season VARCHAR(20),
            test_date DATE,
            sizes JSONB NOT NULL DEFAULT '[]'::jsonb,
            notes JSONB,
            updated_at TIMESTAMPTZ,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE tire_test_placements (
            id BIGSERIAL PRIMARY KEY,
            test_id INTEGER NOT NULL REFERENCES tire_tests(id) ON DELETE CASCADE,
            place SMALLINT,
            brand VARCHAR(100),
            model VARCHAR(200),
            model_1c_id VARCHAR(50),
            rating JSONB,
            notes JSONB,
            criteria JSONB NOT NULL DEFAULT '[]'::jsonb
        )
    """)
    op.execute("CREATE INDEX idx_tire_test_placements_test ON tire_test_placements (test_id)")
    op.execute(
        "CREATE INDEX idx_tire_test_placements_model_1c ON tire_test_placements (model_1c_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tire_test_placements")
    op.execute("DROP TABLE IF EXISTS tire_tests")
    op.execute("DROP TABLE IF EXISTS data_sync_state")
