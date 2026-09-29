"""Add the tire_eu_labels table: the EU tyre label per catalogue SKU.

Fuel efficiency (``energy_class``), wet grip (``wet_grip_class``), external
rolling noise in dB and its class, and the EPREL registration number — what
the bot compares two tyres by («чим Turanza 6 краща за T005?»). The data
comes from the neighbour tshina catalogue (``catalog_product_euro_labels``,
filled there from EPREL), keyed by the same 1C product code as
``tire_products.sku``; ``scripts/import_tire_labels.py`` upserts it.

No foreign key to ``tire_products``: the catalogue sync replaces products
and a label must survive a SKU briefly missing from a sync; the reader joins.

Revision ID: 064
Revises: 063
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "064"
down_revision: str | None = "063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE tire_eu_labels (
            sku VARCHAR(50) PRIMARY KEY,
            energy_class CHAR(1) CHECK (energy_class IN ('A','B','C','D','E','F','G')),
            wet_grip_class CHAR(1) CHECK (wet_grip_class IN ('A','B','C','D','E','F','G')),
            noise_db SMALLINT CHECK (noise_db BETWEEN 50 AND 90),
            noise_class CHAR(1) CHECK (noise_class IN ('A','B','C')),
            eprel_number VARCHAR(32),
            source VARCHAR(32) NOT NULL DEFAULT 'tshina',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tire_eu_labels")
