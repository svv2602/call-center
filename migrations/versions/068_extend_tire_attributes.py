"""Extend tire_attributes with every tyre property tshina now sends.

tshina added to ``GET /api/v1/tire-attributes`` (2026-10-29, contract v1,
fields only added): brand and model of the SKU, the size (width, profile,
diameter, the site's size string), ``commercial`` («C» by the ``R..C``
diameter, «LT» by the LT reinforcement), SUV and electric-car marks, noise
insulation, rim protection, ply and the technology list. With these the
site is the source of every tyre property (owner's decision 2026-10-29);
the 1C columns stay the fallback for SKUs the site does not carry.

``profile`` is NULL on full-profile tyres, ``diameter`` is fractional for
17.5-style sizes. Bools keep tshina's NULL = «no value», FALSE = «no».
``size`` set marks a row of the extended contract: before tshina deploys
and before our first full walk the new columns are NULL on every row, and a
reader must not take that NULL for «not commercial».

Revision ID: 068
Revises: 067
Create Date: 2026-10-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "068"
down_revision: str | None = "067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("brand_slug", "VARCHAR(100)"),
    ("brand_title", "VARCHAR(100)"),
    ("model_name", "VARCHAR(200)"),
    ("width", "SMALLINT"),
    ("profile", "SMALLINT"),
    ("diameter", "NUMERIC(4,1)"),
    ("size", "VARCHAR(50)"),
    ("commercial", "VARCHAR(4)"),
    ("suv", "BOOLEAN"),
    ("electric_car", "BOOLEAN"),
    ("tire_insulation", "VARCHAR(50)"),
    ("tire_rim_peeling", "VARCHAR(50)"),
    ("tire_ply", "VARCHAR(50)"),
    ("tire_technology", "TEXT[]"),
)


def upgrade() -> None:
    for name, kind in _COLUMNS:
        op.execute(f"ALTER TABLE tire_attributes ADD COLUMN IF NOT EXISTS {name} {kind}")


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.execute(f"ALTER TABLE tire_attributes DROP COLUMN IF EXISTS {name}")
