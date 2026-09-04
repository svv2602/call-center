"""Add vehicle_aliases table and audit columns for Wave 8 (fitting flow model recognition).

Adds:
- vehicle_brands / vehicle_models audit columns (source, created_at, updated_at, updated_by)
  so upsert re-imports can preserve manually added or edited records.
- Sequences for manual-entry IDs (negative, decreasing) to avoid collision with
  source CSV integer PKs.
- vehicle_aliases table — reverse-lookup dictionary that lets the LLM/backend map
  raw customer utterances ("Дастер", "Camry") to (brand, model?) pairs before falling
  back to pg_trgm fuzzy match or the Wave 7 vehicle-type-fallback prompt rule.
- vehicle_import_history — audit trail for annual CSV re-imports (dry-run + apply
  results, diff counts, error messages) so the admin UI can show past imports.

Revision ID: 059
Revises: 058
Create Date: 2026-09-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "059"
down_revision: str | None = "058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Audit columns on vehicle_brands ---
    op.execute("""
        ALTER TABLE vehicle_brands
            ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'auto_import',
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            ADD COLUMN IF NOT EXISTS updated_by UUID REFERENCES admin_users(id) ON DELETE SET NULL
    """)

    # --- Audit columns on vehicle_models ---
    op.execute("""
        ALTER TABLE vehicle_models
            ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'auto_import',
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            ADD COLUMN IF NOT EXISTS updated_by UUID REFERENCES admin_users(id) ON DELETE SET NULL
    """)

    # --- Sequences for manual IDs (negative range: -1, -2, -3, ...) ---
    # Manual entries via admin UI use nextval() from these sequences.
    # CSV import uses positive IDs from source, so no collision possible.
    op.execute("""
        CREATE SEQUENCE IF NOT EXISTS vehicle_brands_manual_id_seq
            AS INTEGER
            MINVALUE -2147483647 MAXVALUE -1
            START WITH -1 INCREMENT BY -1
            NO CYCLE
    """)
    op.execute("""
        CREATE SEQUENCE IF NOT EXISTS vehicle_models_manual_id_seq
            AS INTEGER
            MINVALUE -2147483647 MAXVALUE -1
            START WITH -1 INCREMENT BY -1
            NO CYCLE
    """)

    # --- vehicle_aliases ---
    # alias           — human-readable form (may contain original case + accents)
    # alias_normalized — lower(), ё→е, diacritics stripped, spaces collapsed
    #                    Used for exact-match lookup and gin_trgm fuzzy fallback.
    # model_id NULL  — brand-only alias ("вольво" → Volvo, no specific model)
    # source values: 'auto_import' (from CSV name)         — regenerated on re-import
    #                'auto_translit' (Lat→Cyr rules)        — regenerated on re-import
    #                'auto_model_name' (model name = alias) — regenerated on re-import
    #                'manual' (admin added via UI)          — PRESERVED across re-imports
    op.execute("""
        CREATE TABLE vehicle_aliases (
            id SERIAL PRIMARY KEY,
            alias VARCHAR(200) NOT NULL,
            alias_normalized VARCHAR(200) NOT NULL,
            brand_id INTEGER NOT NULL REFERENCES vehicle_brands(id) ON DELETE CASCADE,
            model_id INTEGER REFERENCES vehicle_models(id) ON DELETE CASCADE,
            source VARCHAR(20) NOT NULL DEFAULT 'manual',
            confidence NUMERIC(3,2),
            created_by UUID REFERENCES admin_users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_vehicle_aliases_source CHECK (
                source IN ('auto_import', 'auto_translit', 'auto_model_name', 'manual')
            )
        )
    """)

    # Two partial UNIQUE indexes (NULL model_id vs NOT NULL) — PG can't put NULL
    # into a regular UNIQUE constraint reliably.
    op.execute("""
        CREATE UNIQUE INDEX ux_vehicle_aliases_brand_only
            ON vehicle_aliases(alias_normalized, brand_id)
            WHERE model_id IS NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX ux_vehicle_aliases_brand_model
            ON vehicle_aliases(alias_normalized, brand_id, model_id)
            WHERE model_id IS NOT NULL
    """)

    # Btree for exact-match lookup (`WHERE alias_normalized = ?`)
    op.execute("""
        CREATE INDEX idx_vehicle_aliases_normalized
            ON vehicle_aliases(alias_normalized)
    """)
    # Trigram for fuzzy fallback (`WHERE alias_normalized % ?`)
    op.execute("""
        CREATE INDEX idx_vehicle_aliases_alias_trgm
            ON vehicle_aliases USING gin(alias_normalized gin_trgm_ops)
    """)
    op.execute("""
        CREATE INDEX idx_vehicle_aliases_brand ON vehicle_aliases(brand_id)
    """)
    op.execute("""
        CREATE INDEX idx_vehicle_aliases_model
            ON vehicle_aliases(model_id) WHERE model_id IS NOT NULL
    """)

    # --- vehicle_import_history ---
    # Audit trail for CSV re-imports. Written by scripts/import_vehicle_db.py
    # (dryrun + apply modes) and read by the admin UI history tab.
    op.execute("""
        CREATE TABLE vehicle_import_history (
            id SERIAL PRIMARY KEY,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ,
            status VARCHAR(20) NOT NULL DEFAULT 'running',
            mode VARCHAR(10) NOT NULL DEFAULT 'apply',
            source_path TEXT,
            archive_name VARCHAR(255),
            triggered_by UUID REFERENCES admin_users(id) ON DELETE SET NULL,
            brand_added INTEGER NOT NULL DEFAULT 0,
            brand_updated INTEGER NOT NULL DEFAULT 0,
            brand_skipped_manual INTEGER NOT NULL DEFAULT 0,
            brand_missing_in_source INTEGER NOT NULL DEFAULT 0,
            model_added INTEGER NOT NULL DEFAULT 0,
            model_updated INTEGER NOT NULL DEFAULT 0,
            model_skipped_manual INTEGER NOT NULL DEFAULT 0,
            model_missing_in_source INTEGER NOT NULL DEFAULT 0,
            kit_added INTEGER NOT NULL DEFAULT 0,
            kit_updated INTEGER NOT NULL DEFAULT 0,
            kit_deleted INTEGER NOT NULL DEFAULT 0,
            tire_size_added INTEGER NOT NULL DEFAULT 0,
            tire_size_updated INTEGER NOT NULL DEFAULT 0,
            tire_size_deleted INTEGER NOT NULL DEFAULT 0,
            aliases_regenerated INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            diff_report_json JSONB,
            CONSTRAINT ck_vehicle_import_status CHECK (
                status IN ('running', 'completed', 'failed', 'dryrun')
            ),
            CONSTRAINT ck_vehicle_import_mode CHECK (
                mode IN ('apply', 'dryrun')
            )
        )
    """)
    op.execute("""
        CREATE INDEX idx_vehicle_import_history_started
            ON vehicle_import_history(started_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS vehicle_import_history")
    op.execute("DROP TABLE IF EXISTS vehicle_aliases")
    op.execute("DROP SEQUENCE IF EXISTS vehicle_models_manual_id_seq")
    op.execute("DROP SEQUENCE IF EXISTS vehicle_brands_manual_id_seq")

    op.execute("""
        ALTER TABLE vehicle_models
            DROP COLUMN IF EXISTS updated_by,
            DROP COLUMN IF EXISTS updated_at,
            DROP COLUMN IF EXISTS created_at,
            DROP COLUMN IF EXISTS source
    """)
    op.execute("""
        ALTER TABLE vehicle_brands
            DROP COLUMN IF EXISTS updated_by,
            DROP COLUMN IF EXISTS updated_at,
            DROP COLUMN IF EXISTS created_at,
            DROP COLUMN IF EXISTS source
    """)
