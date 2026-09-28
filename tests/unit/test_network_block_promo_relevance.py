"""«Умови мережі» names only the promotions this turn is about.

Goldset №4/№5 `promo_pk_free_delivery_brand_out_of_scope`: «скільки коштує
доставка шин Michelin?» at Про Колесо — the promotions block was filtered out,
yet the network block said «за акцією безкоштовна на шини Doublestar, Rydanz»
on every turn and the model offered free delivery. The block now gets the
overrides of ``relevant_promotions`` only; the claim guard keeps the call's
full set.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import date

import pytest

from scripts.configure_tenants import PROKOLESO_CONFIG_PATCH
from scripts.migrate_promotions import KNOWN_PROMOTIONS
from src.agent.network_policy import NetworkPolicy, render_network_block
from src.agent.promotions import (
    ActivePromotion,
    PromoOverrides,
    promo_overrides,
    turn_network_overrides,
)

# KNOWN_PROMOTIONS: the first six are Про Колесо's.
PK_PROMOS = [
    ActivePromotion(
        s.title_prefix,
        s.bot_text,
        date(2026, 12, 31),
        overrides=s.overrides,
        mention_brands=tuple(s.mention_brands),
    )
    for s in KNOWN_PROMOTIONS[:6]
]
PK_ON = NetworkPolicy.from_tenant_config({**PROKOLESO_CONFIG_PATCH, "sales_enabled": True})


def _delivery_line(text: str) -> str:
    block = render_network_block(PK_ON, turn_network_overrides(PK_PROMOS, text))
    assert block is not None
    (line,) = [ln for ln in block.splitlines() if ln.startswith("- Доставка")]
    return line


def test_brand_outside_every_promotion_hears_no_promotion() -> None:
    line = _delivery_line("скільки коштує доставка шин Michelin?")
    assert "за акцією" not in line
    assert "Doublestar" not in line


def test_brand_of_a_promotion_hears_it() -> None:
    line = _delivery_line("скільки коштує доставка шин Doublestar?")
    assert "за акцією безкоштовна" in line and "Doublestar" in line


def test_a_question_about_free_delivery_hears_every_one() -> None:
    line = _delivery_line("а доставка у вас безкоштовна?")
    assert "Doublestar" in line and "Goodyear" in line


def test_no_live_list_falls_back_to_the_given_overrides() -> None:
    full = promo_overrides(PK_PROMOS)
    assert turn_network_overrides(None, "будь-що", None, full) is full


def test_the_guard_still_gets_the_full_set() -> None:
    # the filter is for the block only; relevance never narrows the guard
    assert turn_network_overrides(PK_PROMOS, "Michelin") != promo_overrides(PK_PROMOS)
    assert isinstance(turn_network_overrides(PK_PROMOS, "Michelin"), PromoOverrides)


def _render_arg_calls(func: object) -> list[ast.Call]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))  # type: ignore[arg-type]
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "render_network_block"
    ]


@pytest.mark.parametrize("path", ["text", "voice"])
def test_both_loops_render_the_block_with_turn_overrides(path: str) -> None:
    """The loops cannot be unit-run for one prompt line; pin the call sites."""
    if path == "text":
        from src.agent.agent import LLMAgent

        func: object = LLMAgent.process_message
    else:
        from src.agent.streaming_loop import StreamingAgentLoop

        func = StreamingAgentLoop.run_turn
    (call,) = _render_arg_calls(func)
    promos_arg = call.args[1]
    assert isinstance(promos_arg, ast.Call)
    assert isinstance(promos_arg.func, ast.Name)
    assert promos_arg.func.id == "turn_network_overrides"
