"""Network promotions in the prompt (``src/agent/promotions.py``).

With ``sales_enabled`` the promotions block is built from the network's
``promotions`` rows live today (Kyiv); without it the old
``knowledge_articles`` path is used unchanged. The database is a fake engine
that records SQL and returns canned rows — including rows the query should
never return, to prove the reader does not trust it.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import re
from datetime import date, timedelta
from typing import Any

import pytest

from src.agent import promotions, prompt_manager
from src.agent.promotions import (
    ActivePromotion,
    PromoOverrides,
    format_promotions_block,
    load_active_promotions,
    promo_overrides,
)
from src.agent.prompt_manager import format_promotions_context

_REPO = pathlib.Path(__file__).resolve().parents[2]
_TENANT = "11111111-1111-1111-1111-111111111111"
_OTHER = "22222222-2222-2222-2222-222222222222"
_TODAY = date(2026, 10, 15)


# ---------------------------------------------------------------- fake engine


class _Row:
    def __init__(self, data: dict[str, Any]) -> None:
        self._mapping = data


class _Conn:
    def __init__(self, engine: _Engine) -> None:
        self._engine = engine

    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, query: Any, params: dict[str, Any] | None = None) -> list[_Row]:
        sql = str(query)
        self._engine.calls.append((sql, dict(params or {})))
        if "FROM promotions" in sql:
            return [_Row(r) for r in self._engine.promo_rows]
        if "FROM knowledge_articles" in sql:
            return [_Row(r) for r in self._engine.article_rows]
        raise AssertionError(f"unexpected SQL: {sql}")


class _Engine:
    def __init__(
        self,
        promo_rows: list[dict[str, Any]] | None = None,
        article_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.promo_rows = promo_rows or []
        self.article_rows = article_rows or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def begin(self) -> _Conn:
        return _Conn(self)

    def tables_queried(self) -> list[str]:
        return [
            "promotions" if "FROM promotions" in sql else "knowledge_articles"
            for sql, _ in self.calls
        ]


class _Redis:
    def __init__(self, value: Any = None) -> None:
        self.value = value

    async def get(self, key: str) -> Any:
        assert key == prompt_manager.PROMOS_CACHE_REDIS_KEY
        return self.value


def _promo(
    title: str,
    *,
    tenant: str = _TENANT,
    start: date = _TODAY - timedelta(days=10),
    end: date = _TODAY + timedelta(days=10),
    active: bool = True,
    bot_text: str | None = None,
    overrides: dict[str, Any] | None = None,
    brands: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant,
        "title": title,
        "bot_text": bot_text or f"Умови акції {title}.",
        "valid_from": start,
        "valid_to": end,
        "overrides": overrides if overrides is not None else {},
        "mention_brands": brands if brands is not None else [],
        "active": active,
    }


_ARTICLES = [
    {"title": "Стара акція", "content": "Текст статті", "promo_summary": "Коротко"},
]


@pytest.fixture(autouse=True)
def _clear_caches() -> Any:
    promotions._cache.clear()
    promotions._cache_ts = 0.0
    prompt_manager._promos_cache.clear()
    prompt_manager._promos_cache_ts = 0.0
    yield
    promotions._cache.clear()
    prompt_manager._promos_cache.clear()


def _load(engine: _Engine, **kw: Any) -> list[ActivePromotion]:
    kw.setdefault("today", _TODAY)
    return asyncio.run(load_active_promotions(engine, _TENANT, **kw))  # type: ignore[arg-type]


# ------------------------------------------------------------- default-deny


class TestLoadActivePromotions:
    def test_only_live_rows_of_the_network(self) -> None:
        engine = _Engine(
            [
                _promo("Жива"),
                _promo("Остання дата", end=_TODAY),
                _promo("Перша дата", start=_TODAY),
                _promo("Закінчилась", end=_TODAY - timedelta(days=1)),
                _promo("Ще не почалась", start=_TODAY + timedelta(days=1)),
                _promo("Вимкнена", active=False),
                _promo("Чужа мережа", tenant=_OTHER),
            ]
        )
        titles = {p.title for p in _load(engine)}
        assert titles == {"Жива", "Остання дата", "Перша дата"}

    def test_expired_promotion_not_in_block(self) -> None:
        engine = _Engine([_promo("Жива"), _promo("Закінчилась", end=_TODAY - timedelta(days=1))])
        block = format_promotions_block(_load(engine))
        assert block is not None
        assert "Жива" in block
        assert "Закінчилась" not in block

    def test_other_network_not_in_block(self) -> None:
        engine = _Engine([_promo("Наша"), _promo("Чужа", tenant=_OTHER)])
        block = format_promotions_block(_load(engine))
        assert block is not None
        assert "Чужа" not in block

    def test_sql_filters_network_activity_and_dates(self) -> None:
        engine = _Engine([])
        _load(engine)
        ((sql, params),) = engine.calls
        flat = re.sub(r"\s+", " ", sql)
        assert "FROM promotions" in flat
        assert "tenant_id = CAST(:tid AS uuid)" in flat
        assert "active = true" in flat
        assert "valid_from <= :today" in flat
        assert "valid_to >= :today" in flat
        assert params == {"tid": _TENANT, "today": _TODAY}

    def test_default_today_is_kyiv_date(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(promotions, "kyiv_today", lambda: _TODAY)
        engine = _Engine([])
        asyncio.run(load_active_promotions(engine, _TENANT))  # type: ignore[arg-type]
        assert engine.calls[0][1]["today"] == _TODAY

    def test_order_is_stable(self) -> None:
        rows = [
            _promo("Б", end=_TODAY + timedelta(days=5)),
            _promo("А", end=_TODAY + timedelta(days=5)),
            _promo("В", end=_TODAY + timedelta(days=1)),
        ]
        assert [p.title for p in _load(_Engine(rows))] == ["В", "А", "Б"]
        promotions._cache.clear()
        assert [p.title for p in _load(_Engine(list(reversed(rows))))] == ["В", "А", "Б"]

    def test_no_tenant_no_query(self) -> None:
        engine = _Engine([_promo("Жива")])
        assert asyncio.run(load_active_promotions(engine, "", today=_TODAY)) == []  # type: ignore[arg-type]
        assert engine.calls == []


class TestCache:
    def test_cached_within_the_day(self) -> None:
        engine = _Engine([_promo("Жива")])
        _load(engine)
        _load(engine)
        assert len(engine.calls) == 1

    def test_next_day_reloads_and_drops_expired(self) -> None:
        engine = _Engine([_promo("До сьогодні", end=_TODAY)])
        assert [p.title for p in _load(engine)] == ["До сьогодні"]
        assert _load(engine, today=_TODAY + timedelta(days=1)) == []
        assert len(engine.calls) == 2
        assert all(k[1] == (_TODAY + timedelta(days=1)).isoformat() for k in promotions._cache)

    def test_redis_signal_invalidates(self) -> None:
        engine = _Engine([_promo("Жива")])
        _load(engine, redis=_Redis(None))
        _load(engine, redis=_Redis(str(promotions._cache_ts + 100)))
        assert len(engine.calls) == 2


# ------------------------------------------------------------------- block


class TestFormatBlock:
    def test_empty_is_none(self) -> None:
        assert format_promotions_block([]) is None

    def test_block_carries_text_date_and_rules(self) -> None:
        promo = ActivePromotion(
            title="Назва",
            bot_text="Текст для бота.",
            valid_to=date(2026, 12, 31),
            mention_brands=("Matador",),
        )
        block = format_promotions_block([promo])
        assert block is not None
        assert "## Актуальні акції мережі" in block
        assert "### Назва" in block
        assert "Текст для бота." in block
        assert "Діє до 31.12.2026" in block
        assert "Matador" in block
        assert "не більше однієї акції у відповіді" in block
        assert "Умови акції не вигадуй" in block
        assert "главніша за стандартні умови" in block

    def test_no_numbers_beyond_the_data(self) -> None:
        """Rules carry no literal examples: every digit comes from the row."""
        promo = ActivePromotion(title="Назва", bot_text="Текст.", valid_to=date(2026, 12, 31))
        block = format_promotions_block([promo])
        assert block is not None
        assert re.findall(r"\d+", block.replace("31.12.2026", "")) == []

    def test_old_hardcoded_service_promise_absent(self) -> None:
        promo = ActivePromotion(title="Назва", bot_text="Текст.", valid_to=date(2026, 12, 31))
        block = format_promotions_block([promo])
        assert block is not None
        assert "сервісне обслуговування" not in block


# --------------------------------------------------------------- overrides


class TestPromoOverrides:
    def test_empty(self) -> None:
        assert promo_overrides([]) == PromoOverrides()

    def test_union_and_default_deny(self) -> None:
        promos = [
            ActivePromotion(
                "А",
                "т",
                _TODAY,
                overrides={
                    "free_delivery": True,
                    "extended_warranty_brands": ["Matador", " ", 5],
                },
            ),
            ActivePromotion(
                "Б",
                "т",
                _TODAY,
                overrides={
                    "free_delivery": "yes",
                    "discount": 1,
                    "partner_service": {"service": "шиномонтаж", "network_label": "Партнер"},
                },
            ),
        ]
        o = promo_overrides(promos)
        assert o.free_delivery is True
        assert o.discount is False  # only a literal True counts
        assert o.extended_warranty_brands == frozenset({"Matador"})
        assert o.partner_services == ({"service": "шиномонтаж", "network_label": "Партнер"},)

    def test_truthy_non_bool_does_not_grant(self) -> None:
        o = promo_overrides(
            [ActivePromotion("А", "т", _TODAY, overrides={"free_delivery": "true"})]
        )
        assert o.free_delivery is False

    def test_overrides_survive_the_loader(self) -> None:
        engine = _Engine([_promo("Жива", overrides={"free_delivery": True}, brands=["Matador"])])
        (promo,) = _load(engine)
        assert promo.mention_brands == ("Matador",)
        assert promo_overrides([promo]).free_delivery is True


# ------------------------------------------------------ sandbox, behaviour


def _run_sandbox(monkeypatch: pytest.MonkeyPatch, engine: _Engine, sales: bool) -> dict[str, Any]:
    from src.sandbox import agent_runner

    captured: dict[str, Any] = {}

    class _PM:
        def __init__(self, _engine: Any) -> None:
            pass

        async def get_active_prompt(self) -> dict[str, Any]:
            return {"id": None}

    async def _no_tools(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
        return []

    async def _no_rows(*_a: Any, **_k: Any) -> list[Any]:
        return []

    def _agent(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return captured

    monkeypatch.setattr(agent_runner, "PromptManager", _PM)
    monkeypatch.setattr(agent_runner, "get_tools_with_overrides", _no_tools)
    monkeypatch.setattr(agent_runner, "get_few_shot_examples", _no_rows)
    monkeypatch.setattr(agent_runner, "get_safety_rules_for_prompt", _no_rows)
    monkeypatch.setattr(agent_runner, "LLMAgent", _agent)
    monkeypatch.setattr(promotions, "kyiv_today", lambda: _TODAY)

    tenant = {"config": {"sales_enabled": sales}}
    asyncio.run(
        agent_runner.create_sandbox_agent(
            engine,  # type: ignore[arg-type]
            tenant=tenant,
            tenant_id=_TENANT,
        )
    )
    return captured


class TestSandboxSourceChoice:
    def test_sales_off_uses_old_path_byte_for_byte(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine = _Engine([_promo("Нова")], _ARTICLES)
        kwargs = _run_sandbox(monkeypatch, engine, sales=False)
        assert kwargs["promotions_context"] == format_promotions_context(_ARTICLES)
        assert kwargs["promotions"] is None
        assert engine.tables_queried() == ["knowledge_articles"]

    def test_sales_on_uses_promotions_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine = _Engine(
            [_promo("Нова"), _promo("Стара", end=_TODAY - timedelta(days=1))], _ARTICLES
        )
        kwargs = _run_sandbox(monkeypatch, engine, sales=True)
        # Sales on: the live list goes to the agent, which builds the block
        # per turn from the relevant ones only (wave 3-F); no static text.
        assert kwargs["promotions_context"] is None
        assert [p.title for p in kwargs["promotions"]] == ["Нова"]
        assert engine.tables_queried() == ["promotions"]


# --------------------------------------------------- main.py wiring (AST)


def _main_tree() -> ast.Module:
    return ast.parse((_REPO / "src/main.py").read_text(encoding="utf-8"))


def _is_sales_flag(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "sales_enabled"
        and isinstance(node.value, ast.Name)
        and node.value.id == "network_policy"
    )


def _called_names(node: ast.AST) -> set[str]:
    return {
        n.func.id
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


class TestMainWiring:
    """handle_call is not unit-runnable; pin the source choice by AST."""

    def test_loader_picks_source_by_sales_flag(self) -> None:
        funcs = [
            n
            for n in ast.walk(_main_tree())
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_load_promotions"
        ]
        assert len(funcs) == 1
        branches = [
            n for n in ast.walk(funcs[0]) if isinstance(n, ast.If) and _is_sales_flag(n.test)
        ]
        assert len(branches) == 1
        branch = branches[0]
        on = set().union(*(_called_names(s) for s in branch.body))
        assert "load_active_promotions" in on
        assert "fetch_tenant_promotions" not in on
        # sales off: every call outside the branch body — the old path only
        inside = {id(n) for s in branch.body for n in ast.walk(s)}
        off = {
            n.func.id
            for n in ast.walk(funcs[0])
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and id(n) not in inside
        }
        assert "fetch_tenant_promotions" in off
        assert "load_active_promotions" not in off

    def test_context_formatted_by_sales_flag(self) -> None:
        assigns = [
            n
            for n in ast.walk(_main_tree())
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "promotions_context" for t in n.targets)
            and not (isinstance(n.value, ast.Constant) and n.value.value is None)
        ]
        assert len(assigns) == 1
        value = assigns[0].value
        assert isinstance(value, ast.IfExp)
        assert _is_sales_flag(value.test)
        # Sales on: no static block — the agents build one per turn (wave 3-F).
        assert isinstance(value.body, ast.Constant) and value.body.value is None
        assert _called_names(value.orelse) == {"format_promotions_context"}
        (arg,) = [c for c in ast.walk(value.orelse) if isinstance(c, ast.Call)]
        assert isinstance(arg.args[0], ast.Name) and arg.args[0].id == "promos"
