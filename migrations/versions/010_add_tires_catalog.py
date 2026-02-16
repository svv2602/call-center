"""Add tire catalog tables for 1C integration.

Revision ID: 010

Tables: tire_models, tire_products, tire_stock
"""

from alembic import op  # type: ignore[import-untyped]

revision = "010"
down_revision = "009"


def upgrade() -> None:
    # tire_models — one row per model (from get_wares top-level)
    op.execute("""
        CREATE TABLE tire_models (
            id VARCHAR(100) PRIMARY KEY,
            name VARCHAR(500) NOT NULL,
            manufacturer_id VARCHAR(100) NOT NULL,
            manufacturer VARCHAR(200) NOT NULL,
            seasonality VARCHAR(50),
            tread_pattern_type VARCHAR(50),
            type_id VARCHAR(50),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # tire_products — one row per SKU (from get_wares product[])
    op.execute("""
        CREATE TABLE tire_products (
            sku VARCHAR(50) PRIMARY KEY,
            model_id VARCHAR(100) NOT NULL REFERENCES tire_models(id) ON DELETE CASCADE,
            diameter INTEGER,
            width INTEGER,
            profile INTEGER,
            size VARCHAR(50),
            speed_rating VARCHAR(10),
            load_rating VARCHAR(10),
            studded BOOLEAN NOT NULL DEFAULT false,
            description TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX idx_tire_products_size ON tire_products(width, profile, diameter)
    """)
    op.execute("""
        CREATE INDEX idx_tire_products_model_id ON tire_products(model_id)
    """)
    op.execute("""
        CREATE INDEX idx_tire_products_diameter ON tire_products(diameter)
    """)

    # tire_stock — stock/prices per trading network (from get_stock)
    op.execute("""
        CREATE TABLE tire_stock (
            id SERIAL PRIMARY KEY,
            sku VARCHAR(50) NOT NULL,
            trading_network VARCHAR(50) NOT NULL,
            price INTEGER,
            price_tshina INTEGER,
            stock_quantity INTEGER NOT NULL DEFAULT 0,
            country VARCHAR(100),
            year_issue VARCHAR(20),
            synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(sku, trading_network)
        )
    """)

    op.execute("""
        CREATE INDEX idx_tire_stock_sku ON tire_stock(sku)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tire_stock")
    op.execute("DROP TABLE IF EXISTS tire_products")
    op.execute("DROP TABLE IF EXISTS tire_models")
