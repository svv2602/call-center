"""Add tshina tyre models and ommology: the site is the source of tyre properties.

Owner's decision (2026-10-29): tyre properties — brand, model, season, studs,
XL, ommology and the rest — come from tshina.ua; 1C keeps the product list,
stock and prices. ``tire_attributes`` (066) already holds the per-SKU
attributes; these tables add what the Data API gives beside them:

- ``tshina_tire_models`` — ``GET /api/v1/tire-models``: brand and model name
  as the site shows them, keyed by the 1C model code (``tire_products.model_id``).
  Tyres and disks both come; disk properties stay with 1C (tshina has not
  synced disk properties from 1C since 2025-09), the rows are kept for the
  name only.
- ``product_ommology`` — ``GET /api/v1/product-ommology`` (snapshot): ommology
  codes per tyre SKU («*», «MO», «PE»…).
- ``ommology_codes`` — ``GET /api/v1/ommology-codes`` (snapshot): which car
  brands a code stands for.

No foreign keys to the 1C catalogue, as in 064/066: the catalogue sync
replaces rows and these must survive a SKU briefly missing.

Revision ID: 067
Revises: 066
Create Date: 2026-10-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "067"
down_revision: str | None = "066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE tshina_tire_models (
            model_1c_id VARCHAR(50) PRIMARY KEY,
            brand_slug VARCHAR(100),
            brand_title VARCHAR(100),
            name VARCHAR(200) NOT NULL,
            type VARCHAR(10) NOT NULL CHECK (type IN ('tyres', 'disks')),
            season VARCHAR(20),
            is_published BOOLEAN,
            updated_at TIMESTAMPTZ,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE product_ommology (
            sku VARCHAR(50) PRIMARY KEY,
            codes TEXT[] NOT NULL,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE ommology_codes (
            code VARCHAR(50) PRIMARY KEY,
            car_brands JSONB NOT NULL DEFAULT '[]'::jsonb,
            description VARCHAR(500),
            updated_at TIMESTAMPTZ,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ommology_codes")
    op.execute("DROP TABLE IF EXISTS product_ommology")
    op.execute("DROP TABLE IF EXISTS tshina_tire_models")
