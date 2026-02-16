"""Add Nova Poshta reference tables.

Revision ID: 011

Tables: novapost_cities, novapost_branches
"""

from alembic import op  # type: ignore[import-untyped]

revision = "011"
down_revision = "010"


def upgrade() -> None:
    # novapost_cities — Nova Poshta cities/settlements
    op.execute("""
        CREATE TABLE novapost_cities (
            ref VARCHAR(50) PRIMARY KEY,
            description VARCHAR(500) NOT NULL,
            description_ru VARCHAR(500),
            city_id VARCHAR(20),
            area_ref VARCHAR(50),
            settlement_type VARCHAR(50),
            is_branch BOOLEAN NOT NULL DEFAULT false,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX idx_novapost_cities_description
        ON novapost_cities(description varchar_pattern_ops)
    """)
    op.execute("""
        CREATE INDEX idx_novapost_cities_city_id
        ON novapost_cities(city_id)
    """)

    # novapost_branches — Nova Poshta branches/warehouses
    op.execute("""
        CREATE TABLE novapost_branches (
            ref VARCHAR(50) PRIMARY KEY,
            description VARCHAR(500) NOT NULL,
            description_ru VARCHAR(500),
            short_address VARCHAR(500),
            city_ref VARCHAR(50) REFERENCES novapost_cities(ref) ON DELETE CASCADE,
            city_description VARCHAR(300),
            number VARCHAR(20),
            phone VARCHAR(50),
            category VARCHAR(50),
            warehouse_status VARCHAR(20),
            latitude VARCHAR(30),
            longitude VARCHAR(30),
            postal_code VARCHAR(10),
            max_weight INTEGER,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX idx_novapost_branches_city_ref
        ON novapost_branches(city_ref)
    """)
    op.execute("""
        CREATE INDEX idx_novapost_branches_status
        ON novapost_branches(warehouse_status)
    """)
    op.execute("""
        CREATE INDEX idx_novapost_branches_city_status
        ON novapost_branches(city_ref, warehouse_status)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS novapost_branches")
    op.execute("DROP TABLE IF EXISTS novapost_cities")
