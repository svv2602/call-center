"""Add vehicle tire size database tables.

Revision ID: 014

Tables for imported vehicle → tire size lookup:
vehicle_brands, vehicle_models, vehicle_kits, vehicle_tire_sizes,
vehicle_db_metadata.
"""

from alembic import op  # type: ignore[import-untyped]

revision = "014"
down_revision = "013"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute("""
        CREATE TABLE vehicle_brands (
            id INTEGER PRIMARY KEY,
            name VARCHAR(100) NOT NULL
        )
    """)
    op.execute("CREATE INDEX idx_vehicle_brands_name ON vehicle_brands(LOWER(name))")

    op.execute("""
        CREATE TABLE vehicle_models (
            id INTEGER PRIMARY KEY,
            brand_id INTEGER NOT NULL REFERENCES vehicle_brands(id) ON DELETE CASCADE,
            name VARCHAR(200) NOT NULL
        )
    """)
    op.execute("CREATE INDEX idx_vehicle_models_brand ON vehicle_models(brand_id)")
    op.execute("CREATE INDEX idx_vehicle_models_name ON vehicle_models(LOWER(name))")

    op.execute("""
        CREATE TABLE vehicle_kits (
            id INTEGER PRIMARY KEY,
            model_id INTEGER NOT NULL REFERENCES vehicle_models(id) ON DELETE CASCADE,
            year SMALLINT NOT NULL,
            name VARCHAR(200),
            pcd DECIMAL(6,2),
            bolt_count SMALLINT,
            dia DECIMAL(6,2),
            bolt_size VARCHAR(50)
        )
    """)
    op.execute("CREATE INDEX idx_vehicle_kits_model_year ON vehicle_kits(model_id, year)")

    op.execute("""
        CREATE TABLE vehicle_tire_sizes (
            id INTEGER PRIMARY KEY,
            kit_id INTEGER NOT NULL REFERENCES vehicle_kits(id) ON DELETE CASCADE,
            width SMALLINT NOT NULL,
            height SMALLINT NOT NULL,
            diameter DECIMAL(4,1) NOT NULL,
            type SMALLINT NOT NULL DEFAULT 1,
            axle SMALLINT NOT NULL DEFAULT 0,
            axle_group SMALLINT
        )
    """)
    op.execute("CREATE INDEX idx_vts_kit_type ON vehicle_tire_sizes(kit_id, type)")

    op.execute("""
        CREATE TABLE vehicle_db_metadata (
            id SERIAL PRIMARY KEY,
            imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            brand_count INTEGER,
            model_count INTEGER,
            kit_count INTEGER,
            tire_size_count INTEGER,
            source_path TEXT
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS vehicle_db_metadata")
    op.execute("DROP TABLE IF EXISTS vehicle_tire_sizes")
    op.execute("DROP TABLE IF EXISTS vehicle_kits")
    op.execute("DROP TABLE IF EXISTS vehicle_models")
    op.execute("DROP TABLE IF EXISTS vehicle_brands")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
