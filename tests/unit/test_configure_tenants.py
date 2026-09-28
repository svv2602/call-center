"""``scripts/configure_tenants.py --config-only`` / ``--dry-run``.

``--config-only`` merges only ``network_policy`` into ``tenants.config``: the
prod tenants' ``enabled_tools``, ``prompt_suffix``, ``name``, ``sales_enabled``,
``sales_preview_callers`` and every other config key (``excluded_station_ids``,
``agent_provider_override``) survive. ``--dry-run`` writes nothing.

The DB is a small in-memory table: an UPDATE applies ``config || patch`` and
every other column its SET clause names — so a statement that also writes
``enabled_tools`` changes the stored row and the test sees it.
"""

from __future__ import annotations

import copy
import json
import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts import configure_tenants as ct

_TSH_TOOLS = ["search_tires", "book_fitting"]
_PROD_ROWS: dict[str, dict[str, Any]] = {
    "tvoya-shina": {
        "name": "Твоя Шина",
        "enabled_tools": _TSH_TOOLS,
        "prompt_suffix": "suffix-tsh",
        "config": {
            "excluded_station_ids": ["st-1"],
            "agent_provider_override": "provider-x",
            "sales_enabled": True,
            "sales_preview_callers": ["7770", "7771"],
        },
    },
    "prokoleso": {
        "name": "Про Колесо",
        "enabled_tools": ["search_tires"],
        "prompt_suffix": "suffix-pk",
        "config": {},
    },
}


class _FakeDb:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = copy.deepcopy(rows)
        self.updates: list[str] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(stmt)
        params = params or {}
        result = MagicMock(spec=["first"])
        if sql.lstrip().upper().startswith("SELECT"):
            row = self.rows.get(params["slug"])
            result.first.return_value = (
                SimpleNamespace(config=copy.deepcopy(row["config"])) if row else None
            )
            return result
        assert sql.lstrip().upper().startswith("UPDATE")
        self.updates.append(sql)
        slug = params.get("slug") or re.search(r"slug = '([^']+)'", sql).group(1)
        row = self.rows[slug]
        row["config"] = {**(row["config"] or {}), **json.loads(params["config_patch"])}
        for column in ("name", "enabled_tools", "prompt_suffix"):
            if re.search(rf"\b{column}\s*=", sql):
                row[column] = params[column]
        result.first.return_value = SimpleNamespace(id=1, slug=slug, name=row["name"])
        return result


@pytest.mark.asyncio
async def test_config_only_touches_only_network_policy() -> None:
    db = _FakeDb(_PROD_ROWS)
    await ct.apply_config_only(db, dry_run=False)

    assert len(db.updates) == 2
    for slug, before in _PROD_ROWS.items():
        after = db.rows[slug]
        for column in ("name", "enabled_tools", "prompt_suffix"):
            assert after[column] == before[column], (slug, column)
        want = {
            **before["config"],
            "network_policy": ct.TENANT_CONFIG_PATCHES[slug]["network_policy"],
        }
        assert after["config"] == want, slug
    # the flags survive as they were — sales_enabled is not reset to false
    tsh = db.rows["tvoya-shina"]["config"]
    assert tsh["sales_enabled"] is True
    assert tsh["sales_preview_callers"] == ["7770", "7771"]
    assert "sales_enabled" not in db.rows["prokoleso"]["config"]


def test_config_only_patch_has_no_sales_keys() -> None:
    for full in ct.TENANT_CONFIG_PATCHES.values():
        assert set(ct.config_only_patch(full)) == {"network_policy"}
    assert not re.search(r"enabled_tools|prompt_suffix|\bname\s*=", ct.CONFIG_ONLY_UPDATE_SQL)


@pytest.mark.asyncio
async def test_dry_run_writes_nothing_and_prints_before_after(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = _FakeDb(_PROD_ROWS)
    plan = await ct.apply_config_only(db, dry_run=True)

    assert db.updates == []
    assert db.rows == _PROD_ROWS
    out = capsys.readouterr().out
    assert "tvoya-shina" in out and "prokoleso" in out and "before:" in out and "after:" in out
    before, after = plan["tvoya-shina"]
    assert before == _PROD_ROWS["tvoya-shina"]["config"]
    assert after["network_policy"]["delivery_mode"] == "free"
    assert after["sales_preview_callers"] == ["7770", "7771"]


@pytest.mark.asyncio
async def test_missing_tenant_is_skipped() -> None:
    db = _FakeDb({"prokoleso": _PROD_ROWS["prokoleso"]})
    plan = await ct.apply_config_only(db, dry_run=False)
    assert set(plan) == {"prokoleso"}
    assert len(db.updates) == 1


def test_merged_config_handles_null_and_text_json() -> None:
    patch_ = {"network_policy": {"a": 1}}
    assert ct.merged_config(None, patch_) == patch_
    assert ct.merged_config('{"x": 1}', patch_) == {"x": 1, **patch_}


def test_args_default_mode_is_unchanged() -> None:
    args = ct.parse_args([])
    assert (args.config_only, args.dry_run) == (False, False)
    args = ct.parse_args(["--config-only", "--dry-run"])
    assert (args.config_only, args.dry_run) == (True, True)


def test_default_mode_patches_are_unchanged() -> None:
    """Many tests import the full patches; the default mode still writes sales_enabled."""
    assert ct.TENANT_CONFIG_PATCHES == {
        "prokoleso": ct.PROKOLESO_CONFIG_PATCH,
        "tvoya-shina": ct.TVOYA_SHINA_CONFIG_PATCH,
    }
    assert ct.PROKOLESO_CONFIG_PATCH["sales_enabled"] is False
    assert ct.TVOYA_SHINA_CONFIG_PATCH["sales_enabled"] is False
