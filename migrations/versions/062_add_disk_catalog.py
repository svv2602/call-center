"""Add wheel catalog columns and vehicle wheel sizes.

Wheels come from the same 1C ``get_wares`` feed as tyres and stay in
``tire_models`` / ``tire_products`` / ``tire_stock`` under ``type_id='566'``
(``src.store_client.catalog_types.WHEEL``): price and stock are shared, so
the wheel row keeps its ``tire_products`` parent. PCD, ET and DIA arrive only
as text in ``size`` (``16 4/100x6.5 ET50 DIA54.1``); ``disk_products`` holds
them parsed, one row per wheel SKU, written by ``_upsert_wares``.
A ``size`` the parser could not read is stored with ``parse_ok = false`` and
NULL dimensions rather than dropped.

``vehicle_disk_sizes`` mirrors ``vehicle_tire_sizes`` (migration 014) and is
filled from ``test_table_car2_kit_disk_size.csv`` by
``scripts/import_vehicle_db.py``. ``et`` is NULL-able: 11 828 of 1 144 266
CSV rows carry ``NULL``.

``tire_products.commercial`` marks C (light-truck) tyres. 1C sends their
diameter as ``"16C"``, which used to be stored as 0 — 5 271 passenger SKU
measured on prod 2026-09-28 could not be found by size.

Revision ID: 062
Revises: 061
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "062"
down_revision: str | None = "061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tire_products ADD COLUMN IF NOT EXISTS commercial BOOLEAN NOT NULL DEFAULT false"
    )

    # pcd / pcd_alt / dia use the same NUMERIC(6,2) as vehicle_kits.pcd / .dia,
    # so the fitment check compares them without casts.
    op.execute("""
        CREATE TABLE disk_products (
            sku VARCHAR(50) PRIMARY KEY REFERENCES tire_products(sku) ON DELETE CASCADE,
            diameter SMALLINT,
            width_j NUMERIC(4,1),
            bolt_count SMALLINT,
            pcd NUMERIC(6,2),
            pcd_alt NUMERIC(6,2),
            et NUMERIC(5,1),
            dia NUMERIC(6,2),
            color VARCHAR(200),
            parse_ok BOOLEAN NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT disk_products_parsed_has_dimensions CHECK (
                NOT parse_ok OR (
                    diameter IS NOT NULL AND width_j IS NOT NULL
                    AND bolt_count IS NOT NULL AND pcd IS NOT NULL
                    AND et IS NOT NULL AND dia IS NOT NULL
                )
            )
        )
    """)
    op.execute("""
        CREATE INDEX idx_disk_products_fit
            ON disk_products(diameter, bolt_count, pcd) WHERE parse_ok
    """)
    op.execute("""
        CREATE INDEX idx_disk_products_pcd_alt
            ON disk_products(bolt_count, pcd_alt) WHERE parse_ok AND pcd_alt IS NOT NULL
    """)

    op.execute("""
        CREATE TABLE vehicle_disk_sizes (
            id INTEGER PRIMARY KEY,
            kit_id INTEGER NOT NULL REFERENCES vehicle_kits(id) ON DELETE CASCADE,
            width NUMERIC(4,2) NOT NULL,
            diameter NUMERIC(4,1) NOT NULL,
            et NUMERIC(5,1),
            type SMALLINT NOT NULL DEFAULT 1,
            axle SMALLINT NOT NULL DEFAULT 0,
            axle_group SMALLINT
        )
    """)
    op.execute("CREATE INDEX idx_vds_disk_kit_type ON vehicle_disk_sizes(kit_id, type)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS vehicle_disk_sizes")
    op.execute("DROP TABLE IF EXISTS disk_products")
    op.execute("ALTER TABLE tire_products DROP COLUMN IF EXISTS commercial")
