"""Admin API for the tshina sync: status, manual run, run status + the manual task.

The DB is a SQL-recording fake that answers only the statements these
endpoints send (unknown SQL fails the test); Redis is an in-memory fake with
real ``SET NX`` semantics; the Celery call site is patched with an autospec of
the real ``apply_async``, so a call with a wrong signature fails here too.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import create_autospec, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import create_jwt
from src.api.vehicles import router
from src.config import TshinaApiSettings
from src.integrations.tshina_api import RESOURCES
from src.tasks import tshina_sync_tasks as tasks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

SECRET = "test-secret"


# ── fakes ────────────────────────────────────────────────────────────────────


class Row(SimpleNamespace):
    pass


class Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self._rows = [Row(**r) for r in rows or []]
        self._scalar = scalar

    def __iter__(self) -> Any:
        return iter(self._rows)

    def scalar(self) -> Any:
        return self._scalar


class FakeEngine:
    def __init__(self, *, lock_held: bool = False, state: list[dict[str, Any]] | None = None):
        self.lock_held = lock_held
        self.state = state or []
        self.log: list[tuple[str, dict[str, Any]]] = []

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeEngine]:
        yield self

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Result:
        sql = " ".join(str(stmt).split())
        self.log.append((sql, params or {}))
        if "FROM pg_locks" in sql:
            assert params == {"classid": 0, "objid": 72_409_291}
            return Result(scalar=self.lock_held)
        if sql.startswith("SELECT resource, watermark") and "FROM data_sync_state" in sql:
            return Result(self.state)
        if sql.startswith("INSERT INTO admin_audit_log"):
            return Result()
        raise AssertionError(f"unmodelled SQL: {sql}")

    def audit_rows(self) -> list[dict[str, Any]]:
        return [p for s, p in self.log if s.startswith("INSERT INTO admin_audit_log")]


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ex: dict[str, int | None] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> Any:
        if nx and key in self.store:
            return None
        self.store[key] = value
        self.ex[key] = ex
        return True

    async def delete(self, *keys: str) -> int:
        return sum(self.store.pop(k, None) is not None for k in keys)

    async def aclose(self) -> None:
        return None


def _token(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_jwt({'sub': role + '1', 'role': role}, SECRET)}"}


ADMIN, ANALYST, OPERATOR = _token("admin"), _token("analyst"), _token("operator")
ON = {"base_url": "https://t.test", "token": "tok"}


@pytest.fixture()
def env() -> Iterator[SimpleNamespace]:
    """App + patched settings/engine/redis/Celery; ``env.enabled`` flips the switch."""
    ns = SimpleNamespace(engine=FakeEngine(), redis=FakeRedis(), cfg=dict(ON), states={})

    def settings() -> Any:
        return SimpleNamespace(
            admin=SimpleNamespace(jwt_secret=SECRET),
            tshina_api=TshinaApiSettings(**ns.cfg),
        )

    async def get_engine() -> Any:
        return ns.engine

    async def get_redis() -> Any:
        return ns.redis

    def task_state(task_id: str) -> tuple[str, Any]:
        return ns.states.get(task_id, ("PENDING", None))

    def no_client(*a: Any, **k: Any) -> None:
        raise AssertionError("the API must never talk to tshina itself")

    enqueue = create_autospec(tasks.tshina_sync_manual.apply_async)
    app = FastAPI()
    app.include_router(router)
    with (
        patch("src.api.auth.get_settings", settings),
        patch("src.api.vehicles.get_settings", settings),
        patch("src.api.vehicles._get_engine", get_engine),
        patch("src.api.vehicles._get_redis", get_redis),
        patch("src.api.vehicles._task_state", task_state),
        patch("src.integrations.tshina_api.TshinaApiClient", no_client),
        patch.object(tasks.tshina_sync_manual, "apply_async", enqueue),
    ):
        ns.client = TestClient(app)
        ns.enqueue = enqueue
        yield ns


def _run(env: SimpleNamespace, headers: dict[str, str] = ADMIN, **body: Any) -> Any:
    return env.client.post("/admin/vehicles/tshina-sync/run", json=body, headers=headers)


def _nothing_started(env: SimpleNamespace) -> None:
    assert env.enqueue.call_count == 0
    assert env.redis.store == {}
    assert env.engine.audit_rows() == []


# ── status ───────────────────────────────────────────────────────────────────


def test_status_lists_every_resource_in_contract_order(env: SimpleNamespace) -> None:
    ts = datetime(2026, 9, 29, 1, 40, tzinfo=UTC)
    env.engine.state = [
        {
            "resource": "eu-labels",
            "watermark": ts,
            "last_success_at": ts,
            "last_attempt_at": ts,
            "last_error": None,
            "upserted": 12,
            "deleted": 1,
            "full_sync_at": None,
        }
    ]
    r = env.client.get("/admin/vehicles/tshina-sync/status", headers=ANALYST)
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is True and data["lock_held"] is False and data["active"] is None
    assert [x["resource"] for x in data["resources"]] == list(RESOURCES)
    labels = data["resources"][0]
    assert labels["watermark"] == ts.isoformat() and labels["upserted"] == 12
    never = data["resources"][1]
    assert never["last_success_at"] is None and never["watermark"] is None


def test_status_reports_disabled_lock_and_active_run(env: SimpleNamespace) -> None:
    env.cfg = {"base_url": "https://t.test"}  # no token
    env.engine.lock_held = True
    env.redis.store[tasks.ACTIVE_KEY] = "t-1"
    env.redis.store[tasks.RUN_KEY.format(task_id="t-1")] = json.dumps({"mode": "full"})
    data = env.client.get("/admin/vehicles/tshina-sync/status", headers=ADMIN).json()
    assert data["enabled"] is False and data["lock_held"] is True
    assert data["active"] == {"task_id": "t-1", "mode": "full"}


def test_status_needs_vehicles_read(env: SimpleNamespace) -> None:
    assert env.client.get("/admin/vehicles/tshina-sync/status", headers=OPERATOR).status_code == 403
    assert env.client.get("/admin/vehicles/tshina-sync/status").status_code == 401


# ── run: guards ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("headers", [ANALYST, OPERATOR])
def test_run_needs_vehicles_write(env: SimpleNamespace, headers: dict[str, str]) -> None:
    r = _run(env, headers, mode="incremental")
    assert r.status_code == 403
    _nothing_started(env)


def test_full_without_confirmation_is_refused(env: SimpleNamespace) -> None:
    r = _run(env, mode="full")
    assert r.status_code == 400 and "confirm_full" in r.json()["detail"]
    r = _run(env, mode="full", confirm_full=False)
    assert r.status_code == 400
    _nothing_started(env)


@pytest.mark.parametrize("mode", ["dry_run", "incremental", "full"])
@pytest.mark.parametrize("cfg", [{}, {"base_url": "https://t.test"}, {"token": "tok"}])
def test_disabled_queues_nothing(env: SimpleNamespace, cfg: dict[str, str], mode: str) -> None:
    env.cfg = cfg
    r = _run(env, mode=mode, confirm_full=True)
    assert r.status_code == 409 and "disabled" in r.json()["detail"]
    _nothing_started(env)


def test_lock_held_by_the_nightly_run_is_409(env: SimpleNamespace) -> None:
    env.engine.lock_held = True
    r = _run(env, mode="incremental")
    assert r.status_code == 409 and "already running" in r.json()["detail"]
    _nothing_started(env)


def test_second_click_while_the_first_is_queued_is_409(env: SimpleNamespace) -> None:
    first = _run(env, mode="dry_run")
    assert first.status_code == 202
    second = _run(env, mode="incremental")
    assert second.status_code == 409 and "already running" in second.json()["detail"]
    assert env.enqueue.call_count == 1
    assert env.redis.store[tasks.ACTIVE_KEY] == first.json()["task_id"]


def test_marker_of_a_finished_task_is_taken_over(env: SimpleNamespace) -> None:
    env.redis.store[tasks.ACTIVE_KEY] = "dead"
    env.states["dead"] = ("FAILURE", RuntimeError("TimeLimitExceeded"))
    r = _run(env, mode="incremental")
    assert r.status_code == 202
    assert env.redis.store[tasks.ACTIVE_KEY] == r.json()["task_id"] != "dead"


def test_unknown_mode_is_422(env: SimpleNamespace) -> None:
    assert _run(env, mode="everything").status_code == 422
    _nothing_started(env)


# ── run: the happy path ──────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["dry_run", "incremental", "full"])
def test_run_queues_the_celery_task_and_audits(env: SimpleNamespace, mode: str) -> None:
    r = _run(env, mode=mode, confirm_full=mode == "full")
    assert r.status_code == 202
    data = r.json()
    task_id = data["task_id"]
    assert data["status"] == "queued" and data["mode"] == mode and data["user"] == "admin1"

    env.enqueue.assert_called_once_with(kwargs={"mode": mode}, task_id=task_id)
    assert env.redis.store[tasks.ACTIVE_KEY] == task_id
    assert env.redis.ex[tasks.ACTIVE_KEY] == tasks.ACTIVE_TTL_S
    run_key = tasks.RUN_KEY.format(task_id=task_id)
    assert json.loads(env.redis.store[run_key])["mode"] == mode
    assert env.redis.ex[run_key] == tasks.RUN_TTL_S

    [audit] = env.engine.audit_rows()
    assert audit["action"] == "tshina_sync_run" and audit["resource_id"] == task_id
    assert audit["username"] == "admin1" and f"'mode': '{mode}'" in audit["details"]


def test_enqueue_failure_frees_the_marker(env: SimpleNamespace) -> None:
    env.enqueue.side_effect = ConnectionError("broker down")
    r = _run(env, mode="incremental")
    assert r.status_code == 503
    assert tasks.ACTIVE_KEY not in env.redis.store
    assert env.engine.audit_rows() == []
    assert _run(env, mode="incremental").status_code == 503  # not 409: nothing stuck


# ── run status ───────────────────────────────────────────────────────────────


def test_run_status_only_for_runs_this_page_started(env: SimpleNamespace) -> None:
    r = env.client.get("/admin/vehicles/tshina-sync/run/some-other-task", headers=ADMIN)
    assert r.status_code == 404


def test_run_status_pending_then_success_then_failure(env: SimpleNamespace) -> None:
    task_id = _run(env, mode="dry_run").json()["task_id"]
    url = f"/admin/vehicles/tshina-sync/run/{task_id}"

    pending = env.client.get(url, headers=ANALYST).json()
    assert pending["state"] == "PENDING" and pending["done"] is False
    assert pending["mode"] == "dry_run" and pending["result"] is None

    report = {"mode": "dry_run", "status": "dry_run", "resources": [{"resource": "eu-labels"}]}
    env.states[task_id] = ("SUCCESS", report)
    done = env.client.get(url, headers=ANALYST).json()
    assert done["done"] is True and done["result"] == report and done["error"] is None

    env.states[task_id] = ("FAILURE", RuntimeError("boom"))
    failed = env.client.get(url, headers=ANALYST).json()
    assert failed["done"] is True and failed["result"] is None and "boom" in failed["error"]


def test_run_status_needs_vehicles_read(env: SimpleNamespace) -> None:
    r = env.client.get("/admin/vehicles/tshina-sync/run/x", headers=OPERATOR)
    assert r.status_code == 403


# ── the manual task ──────────────────────────────────────────────────────────


async def test_manual_dry_run_writes_nothing_to_the_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end through the real ``run_tshina_sync``/``run_sync``: no begin(), no lock."""
    from tests.unit.test_tshina_sync import FakeClient, FakeDb, label

    db = FakeDb()
    db.dispose = _async_noop  # type: ignore[attr-defined]
    client = FakeClient({"eu-labels": [[label("1"), {"sku": "2", "deleted": True}]]})
    released: list[str] = []

    async def release(task_id: str) -> None:
        released.append(task_id)

    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(
            tshina_api=TshinaApiSettings(**ON),
            database=SimpleNamespace(url="postgresql+asyncpg://x/y"),
        ),
    )
    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", lambda *a, **k: db)
    monkeypatch.setattr("src.integrations.tshina_api.TshinaApiClient", lambda *a, **k: client)
    monkeypatch.setattr(tasks, "release_active", release)

    out = await tasks.run_manual_sync("dry_run", "t-9")
    assert out["mode"] == "dry_run" and out["status"] == "dry_run"
    labels = next(r for r in out["resources"] if r["resource"] == "eu-labels")
    assert labels["upserted"] == 1 and labels["deleted"] == 1
    assert {r for r, _ in client.calls} == set(RESOURCES)
    assert all(rows == {} for rows in db.t.values())
    assert db.commits == 0  # not even data_sync_state.last_attempt_at
    assert not any("advisory" in s for s in db.log)
    assert released == ["t-9"]


async def _async_noop() -> None:
    return None


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("dry_run", {"full": False, "dry_run": True}),
        ("incremental", {"full": False, "dry_run": False}),
        ("full", {"full": True, "dry_run": False}),
    ],
)
async def test_manual_mode_maps_to_the_sync_flags(
    monkeypatch: pytest.MonkeyPatch, mode: str, expected: dict[str, bool]
) -> None:
    seen: list[dict[str, Any]] = []

    async def fake_sync(**kw: Any) -> dict[str, Any]:
        seen.append(kw)
        return {"status": "ok"}

    async def release(task_id: str) -> None:
        return None

    monkeypatch.setattr(tasks, "run_tshina_sync", fake_sync)
    monkeypatch.setattr(tasks, "release_active", release)
    assert await tasks.run_manual_sync(mode, "t") == {"mode": mode, "status": "ok"}
    assert seen == [expected]


async def test_manual_run_frees_the_marker_even_when_it_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released: list[str] = []

    async def fake_sync(**kw: Any) -> dict[str, Any]:
        raise RuntimeError("db down")

    async def release(task_id: str) -> None:
        released.append(task_id)

    monkeypatch.setattr(tasks, "run_tshina_sync", fake_sync)
    monkeypatch.setattr(tasks, "release_active", release)
    with pytest.raises(RuntimeError):
        await tasks.run_manual_sync("incremental", "t-1")
    with pytest.raises(ValueError, match="unknown mode"):
        await tasks.run_manual_sync("everything", "t-2")
    assert released == ["t-1", "t-2"]


async def test_release_frees_only_its_own_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    monkeypatch.setattr("redis.asyncio.Redis.from_url", lambda *a, **k: redis)
    redis.store[tasks.ACTIVE_KEY] = "other"
    await tasks.release_active("mine")
    assert redis.store[tasks.ACTIVE_KEY] == "other"
    redis.store[tasks.ACTIVE_KEY] = "mine"
    await tasks.release_active("mine")
    assert tasks.ACTIVE_KEY not in redis.store


def test_manual_task_is_registered_on_the_catalog_queue() -> None:
    from src.tasks.celery_app import app

    name = "src.tasks.tshina_sync_tasks.tshina_sync_manual"
    assert name in app.tasks
    assert app.amqp.router.route({}, name)["queue"].name == "catalog"
