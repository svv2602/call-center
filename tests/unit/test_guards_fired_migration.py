"""Migration 069: ``call_turns.guards_fired``.

The SQL is collected by importing the revision with a stand-in ``alembic``
module (alembic is not a test dependency) and executed on a throw-away
PostgreSQL cluster when the server binaries are installed; those tests are
skipped otherwise. The base schema is ``call_turns`` as it stands before the
revision: partitioned by month, with one partition already there.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import types
from glob import glob
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "versions" / "069_add_guards_fired_to_call_turns.py"

# The column list the call logger writes today (src/logging/call_logger.py):
# it does not name the new column, and must keep working once it exists.
_INSERT_AS_THE_LOGGER_DOES = """
INSERT INTO call_turns (id, call_id, turn_number, speaker, content,
                        stt_confidence, stt_latency_ms, llm_latency_ms,
                        tts_latency_ms, language, created_at)
VALUES (gen_random_uuid(), gen_random_uuid(), 1, 'bot', 'Добрий день',
        NULL, NULL, 900, NULL, 'uk-UA', '2026-11-20 10:00:00+00')
"""

_BASE_SCHEMA = """
CREATE TABLE call_turns (
    id UUID NOT NULL,
    call_id UUID NOT NULL,
    turn_number INTEGER,
    speaker VARCHAR(10),
    content TEXT,
    stt_confidence FLOAT,
    stt_latency_ms INTEGER,
    llm_latency_ms INTEGER,
    tts_latency_ms INTEGER,
    language VARCHAR(10),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE TABLE call_turns_2026_11 PARTITION OF call_turns
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
"""


def _render(fn_name: str) -> list[str]:
    statements: list[str] = []
    fake_alembic = types.ModuleType("alembic")
    fake_alembic.op = types.SimpleNamespace(execute=statements.append)  # type: ignore[attr-defined]
    saved = sys.modules.get("alembic")
    sys.modules["alembic"] = fake_alembic
    try:
        spec = importlib.util.spec_from_file_location("_mig_069", MIGRATION)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        getattr(mod, fn_name)()
    finally:
        if saved is None:
            del sys.modules["alembic"]
        else:
            sys.modules["alembic"] = saved
    return statements


def test_revision_chain() -> None:
    src = MIGRATION.read_text()
    assert 'revision: str = "069"' in src
    assert 'down_revision: str | None = "068"' in src


def test_no_other_revision_follows_068() -> None:
    """One head: 069 is the only revision that names 068 as its parent."""
    children = [
        p.name
        for p in (ROOT / "migrations" / "versions").glob("*.py")
        if 'down_revision: str | None = "068"' in p.read_text()
    ]
    assert children == [MIGRATION.name]


def test_upgrade_and_downgrade_render_one_statement_each() -> None:
    up = _render("upgrade")
    down = _render("downgrade")
    assert len(up) == 1 and len(down) == 1
    assert "call_turns" in up[0] and "guards_fired" in up[0]
    assert "call_turns" in down[0] and "guards_fired" in down[0]


def _pg_bin() -> Path | None:
    for d in sorted(glob("/usr/lib/postgresql/*/bin"), reverse=True):
        if Path(d, "initdb").exists() and Path(d, "postgres").exists():
            return Path(d)
    return None


class _Pg:
    def __init__(self, bindir: Path, sockdir: str) -> None:
        self.bindir = bindir
        self.sockdir = sockdir

    def sql(self, statement: str) -> str:
        res = subprocess.run(
            [
                str(self.bindir / "psql"),
                "-h",
                self.sockdir,
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-v",
                "ON_ERROR_STOP=1",
                "-X",
                "-Atq",
                "-c",
                statement,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr)
        return res.stdout.strip()

    def migrate(self, fn_name: str) -> None:
        for stmt in _render(fn_name):
            self.sql(stmt)

    def column(self, table: str) -> str:
        """``data_type|is_nullable|column_default`` of guards_fired, '' when absent."""
        return self.sql(
            "SELECT data_type || '|' || is_nullable || '|' || coalesce(column_default, '') "
            "FROM information_schema.columns "
            f"WHERE table_name = '{table}' AND column_name = 'guards_fired'"
        )


@pytest.fixture(scope="module")
def pg():
    bindir = _pg_bin()
    if bindir is None:
        pytest.skip("PostgreSQL server binaries not installed")
    base = tempfile.mkdtemp(prefix="pg069")  # short: unix socket path ≤ 107 bytes
    data = f"{base}/d"
    subprocess.run(
        [str(bindir / "initdb"), "-D", data, "-U", "postgres", "-A", "trust", "--no-sync"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            str(bindir / "pg_ctl"),
            "-D",
            data,
            "-w",
            "-l",
            f"{base}/log",
            "-o",
            f"-c listen_addresses='' -k {base} -c fsync=off",
            "start",
        ],
        check=True,
        capture_output=True,
    )
    try:
        yield _Pg(bindir, base)
    finally:
        subprocess.run(
            [str(bindir / "pg_ctl"), "-D", data, "-m", "immediate", "stop"],
            check=False,
            capture_output=True,
        )
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def db(pg: _Pg):
    """``call_turns`` as it is before 069, for every test."""
    pg.sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    pg.sql(_BASE_SCHEMA)
    return pg


def test_column_is_nullable_jsonb_without_default(db: _Pg) -> None:
    assert db.column("call_turns") == ""
    db.migrate("upgrade")
    assert db.column("call_turns") == "jsonb|YES|"


def test_existing_partition_gets_the_column(db: _Pg) -> None:
    db.migrate("upgrade")
    assert db.column("call_turns_2026_11") == "jsonb|YES|"


def test_partition_created_after_the_upgrade_inherits_it(db: _Pg) -> None:
    db.migrate("upgrade")
    db.sql(
        "CREATE TABLE call_turns_2026_12 PARTITION OF call_turns "
        "FOR VALUES FROM ('2026-12-01') TO ('2027-01-01')"
    )
    assert db.column("call_turns_2026_12") == "jsonb|YES|"


def test_todays_insert_still_works_and_leaves_null(db: _Pg) -> None:
    """NULL — «no trace»: a writer that does not name the column must not get []."""
    db.migrate("upgrade")
    db.sql(_INSERT_AS_THE_LOGGER_DOES)
    assert db.sql("SELECT guards_fired IS NULL FROM call_turns") == "t"


def test_empty_trace_is_not_null(db: _Pg) -> None:
    """[] — «recorded, nothing fired» — stays apart from NULL."""
    db.migrate("upgrade")
    db.sql(_INSERT_AS_THE_LOGGER_DOES)
    db.sql("UPDATE call_turns SET guards_fired = '[]'::jsonb")
    assert (
        db.sql("SELECT guards_fired IS NULL, jsonb_array_length(guards_fired) FROM call_turns")
        == "f|0"
    )


def test_upgrade_twice_is_harmless(db: _Pg) -> None:
    db.migrate("upgrade")
    db.sql(_INSERT_AS_THE_LOGGER_DOES)
    db.sql("""UPDATE call_turns SET guards_fired = '[{"guard": "tire_claim_refused"}]'::jsonb""")
    db.migrate("upgrade")
    assert db.sql("SELECT guards_fired->0->>'guard' FROM call_turns") == "tire_claim_refused"


def test_up_down_up(db: _Pg) -> None:
    db.migrate("upgrade")
    db.sql(_INSERT_AS_THE_LOGGER_DOES)
    db.migrate("downgrade")
    assert db.column("call_turns") == ""
    assert db.column("call_turns_2026_11") == ""
    assert db.sql("SELECT count(*) FROM call_turns") == "1"
    db.migrate("downgrade")  # a second downgrade is harmless too
    db.migrate("upgrade")
    assert db.column("call_turns") == "jsonb|YES|"
