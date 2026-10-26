"""Add the tire_attributes table: structural tyre attributes per catalogue SKU.

From ``GET /api/v1/tire-attributes`` of the tshina Data API (since
2026-10-26), keyed by the same 1C product code as ``tire_products.sku``:
studs (``studded`` — ``ship`` studded, ``pod_ship`` stud-ready, ``no_ship``
non-studded; 1C attribute 26, canonical per tshina), XL, RunFlat, the 3PMSF
snow marker, vehicle type, load/speed index, country, production year and
``model_1c_id`` (the link to tyre tests). Today studs and XL are guessed by
regexes over ``description``, and «під шип» is not told apart at all; this
table is the source and the regexes become the fallback.

``studded`` NULL means «not applicable» (summer and all-season tyres), NOT
«non-studded»: tshina itself sets ``no_ship`` on a winter tyre without 1C
data. ``load_index`` is a string — light-truck tyres come as ``"109/107"``.
``updated_at`` is tshina's change time as sent, ``synced_at`` is ours.

No foreign key to ``tire_products``: the catalogue sync replaces products
and an attribute row must survive a SKU briefly missing from a sync; the
reader joins (as ``tire_eu_labels``, 064).

Revision ID: 066
Revises: 065
Create Date: 2026-10-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "066"
down_revision: str | None = "065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE tire_attributes (
            sku VARCHAR(50) PRIMARY KEY,
            model_1c_id VARCHAR(50),
            season VARCHAR(20),
            studded VARCHAR(10) CHECK (studded IN ('ship','pod_ship','no_ship')),
            is_xl BOOLEAN,
            is_runflat BOOLEAN,
            is_snow_marker BOOLEAN,
            vehicle_type VARCHAR(32),
            reinforcement VARCHAR(32),
            load_index VARCHAR(16),
            speed_index VARCHAR(8),
            country JSONB,
            year SMALLINT CHECK (year BETWEEN 2000 AND 2100),
            source VARCHAR(32) NOT NULL DEFAULT 'tshina',
            updated_at TIMESTAMPTZ,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_tire_attributes_model_1c ON tire_attributes (model_1c_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tire_attributes")
