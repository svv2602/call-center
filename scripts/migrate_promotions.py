"""Copy live promotion articles from the knowledge base into ``promotions``.

Source: ``knowledge_articles`` with ``category = 'promotions'`` and
``active`` (8 on prod 2026-09-28: ProKoleso 6, Tvoya Shina 2). Every article
becomes one ``promotions`` row valid from today to 2026-12-31 (owner decision
2026-09-28), with ``source_article_id`` pointing back at it. The articles
themselves are never modified.

``overrides``, ``mention_brands`` and ``bot_text`` come from ``KNOWN_PROMOTIONS``,
matched by the start of the article title. The hand-written ``bot_text`` is
used because the articles' own ``promo_summary`` still names the original
periods («з 1 по 28 лютого 2026») that the new window replaces. A title not in
the table still gets a row — ``overrides = {}``, no brands, ``bot_text`` from
``promo_summary`` or the cleaned ``content`` — and a warning to fill it in.

Dry-run by default: prints the plan, writes nothing. ``--apply`` inserts.
Re-running is safe: rows already present by ``(tenant_id, title)`` are
skipped, and the INSERT also carries ``ON CONFLICT DO NOTHING``.

Usage:
    python -m scripts.migrate_promotions            # plan only
    python -m scripts.migrate_promotions --apply    # insert
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

logger = logging.getLogger(__name__)

VALID_TO = date(2026, 12, 31)
BOT_TEXT_MAX = 500
_KYIV = ZoneInfo("Europe/Kyiv")

_FREE_DELIVERY_BRANDS_12 = [
    "goodyear",
    "nexen",
    "roadstone",
    "starmaxx",
    "matador",
    "pirelli",
    "vredestein",
    "barum",
    "kleber",
    "fulda",
    "grenlander",
    "sailun",
]
_FREE_DELIVERY_12_TEXT = (
    "Безкоштовна доставка «Новою Поштою» по Україні від однієї шини на шини з позначкою "
    "«Безкоштовна доставка» брендів Goodyear, Nexen, Roadstone, Starmaxx, Matador, Pirelli, "
    "Vredestein, Barum, Kleber, Fulda, Grenlander та Sailun. Діє на літні, зимові та "
    "всесезонні шини."
)


@dataclass(frozen=True)
class PromotionSpec:
    """How one known article maps onto a ``promotions`` row."""

    title_prefix: str
    bot_text: str
    overrides: dict[str, Any] = field(default_factory=dict)
    mention_brands: list[str] = field(default_factory=list)


KNOWN_PROMOTIONS: tuple[PromotionSpec, ...] = (
    # --- ProKoleso ---
    PromotionSpec(
        title_prefix="Безкоштовна доставка шин Doublestar та Rydanz",
        bot_text=(
            "Безкоштовна доставка «Новою Поштою» по Україні на всі шини Doublestar та Rydanz "
            "— навіть при замовленні однієї шини. Діє на літні, зимові та всесезонні шини."
        ),
        overrides={"free_delivery": True},
        mention_brands=["doublestar", "rydanz"],
    ),
    PromotionSpec(
        title_prefix="Безкоштовна доставка шин від Prokoleso.ua",
        bot_text=_FREE_DELIVERY_12_TEXT,
        overrides={"free_delivery": True},
        mention_brands=list(_FREE_DELIVERY_BRANDS_12),
    ),
    PromotionSpec(
        # The article spells the brand «Grenadier»; the brand is Grenlander.
        title_prefix="Безкоштовна доставка шин від провідних брендів",
        bot_text=_FREE_DELIVERY_12_TEXT,
        overrides={"free_delivery": True},
        mention_brands=list(_FREE_DELIVERY_BRANDS_12),
    ),
    PromotionSpec(
        title_prefix="Гарантія на шини Matador",
        bot_text=(
            "Гарантія 12 місяців на легкові шини Matador, куплені на сайті Prokoleso.ua, від "
            "проколів, порізів та інших пошкоджень. Якщо шину можна відремонтувати — "
            "компенсуємо вартість ремонту, якщо ні — суму пропорційно залишку протектора; "
            "виплата на картку протягом 5 робочих днів."
        ),
        overrides={"extended_warranty_brands": ["matador"]},
        mention_brands=["matador"],
    ),
    PromotionSpec(
        title_prefix="Ексклюзивне сервісне обслуговування на 3 роки",
        bot_text=(
            "При покупці комплекту з 4 шин будь-якого бренду — знижка 25% на шиномонтаж і "
            "безкоштовний ремонт проколів у зоні протектора протягом 3 років у сервісних "
            "центрах «Твоя Шина». Активувати треба протягом 30 днів від покупки, назвавши "
            "номер замовлення."
        ),
        overrides={
            "discount": True,
            "partner_service": {"service": "fitting", "network_label": "Твоя Шина"},
        },
    ),
    PromotionSpec(
        title_prefix="Знижка 5% на всесезонні шини Bridgestone",
        bot_text=(
            "Знижка 5% на всесезонні шини Bridgestone — моделі Turanza All Season 6 ENLITEN "
            "та WeatherControl A005 EVO, розміри від 185/65 R15 до 255/45 R19."
        ),
        overrides={"discount": True},
        mention_brands=["bridgestone"],
    ),
    # --- Tvoya Shina ---
    PromotionSpec(
        title_prefix="Акція «Доставимо безплатно»",
        bot_text=(
            "Безплатна доставка «Новою Поштою» при замовленні будь-яких шин і дисків, "
            "зокрема й при покупці в кредит."
        ),
        overrides={"free_delivery": True},
    ),
    PromotionSpec(
        title_prefix="Сервісна програма MICHELIN",
        bot_text=(
            "Сервісна програма MICHELIN: при покупці в торговому центрі «Твоя Шина» і "
            "встановленні там комплекту від 4 шин MICHELIN, вироблених після 01.01.2022, "
            "пошкоджену шину, яку не можна відремонтувати (прокол, розрив чи здуття "
            "боковини), безкоштовно замінять на таку саму. Не діє на шини, куплені в кредит "
            "або частинами."
        ),
        overrides={"extended_warranty_brands": ["michelin"]},
        mention_brands=["michelin"],
    ),
)

SELECT_SOURCE_SQL = """
    SELECT id::text AS id, tenant_id::text AS tenant_id, title, promo_summary, content
    FROM knowledge_articles
    WHERE category = 'promotions' AND active = true
    ORDER BY tenant_id NULLS LAST, title
"""

SELECT_EXISTING_SQL = "SELECT tenant_id::text AS tenant_id, title FROM promotions"

INSERT_SQL = """
    INSERT INTO promotions
        (tenant_id, title, bot_text, valid_from, valid_to,
         overrides, mention_brands, active, source_article_id)
    VALUES
        (CAST(:tenant_id AS uuid), :title, :bot_text, :valid_from, :valid_to,
         CAST(:overrides AS jsonb), :mention_brands, true, CAST(:source_article_id AS uuid))
    ON CONFLICT (tenant_id, title) DO NOTHING
"""


@dataclass
class PlannedPromotion:
    action: str  # "insert" | "exists" | "skip_no_tenant"
    article_id: str
    tenant_id: str | None
    title: str
    bot_text: str = ""
    overrides: dict[str, Any] = field(default_factory=dict)
    mention_brands: list[str] = field(default_factory=list)
    valid_from: date | None = None
    valid_to: date | None = None
    warnings: list[str] = field(default_factory=list)

    def insert_params(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "title": self.title,
            "bot_text": self.bot_text,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "overrides": json.dumps(self.overrides, ensure_ascii=False),
            "mention_brands": list(self.mention_brands),
            "source_article_id": self.article_id,
        }


def match_spec(title: str) -> PromotionSpec | None:
    """Return the table entry whose prefix starts ``title`` (case-insensitive)."""
    norm = " ".join(title.split()).casefold()
    matches = [s for s in KNOWN_PROMOTIONS if norm.startswith(s.title_prefix.casefold())]
    if len(matches) != 1:
        return None
    return matches[0]


_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_MD_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_MD_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)")
_SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")


def clean_text(raw: str, limit: int = BOT_TEXT_MAX) -> str:
    """Plain text without links or markdown, cut at a sentence end within ``limit``."""
    s = _MD_LINK.sub(r"\1", raw)
    s = _URL.sub("", s)
    s = _MD_HEADING.sub("", s)
    s = _MD_BULLET.sub("", s)
    s = _MD_EMPHASIS.sub("", s)
    s = " ".join(s.split())
    if len(s) <= limit:
        return s
    head = s[:limit]
    ends = [m.end() for m in _SENTENCE_END.finditer(head)]
    if ends:
        return head[: ends[-1]].strip()
    cut = head.rsplit(" ", 1)[0].rstrip(" ,;:—-")
    return cut[: limit - 1] + "…"


def build_plan(
    articles: list[dict[str, Any]],
    existing: set[tuple[str, str]],
    today: date,
) -> list[PlannedPromotion]:
    """Decide, per source article, what ``--apply`` would do. Pure: no I/O."""
    if today > VALID_TO:
        raise ValueError(f"today {today} is past valid_to {VALID_TO}")
    plan: list[PlannedPromotion] = []
    for art in articles:
        title = " ".join((art.get("title") or "").split())
        item = PlannedPromotion(
            action="insert",
            article_id=str(art["id"]),
            tenant_id=art.get("tenant_id"),
            title=title,
            valid_from=today,
            valid_to=VALID_TO,
        )
        if not item.tenant_id:
            item.action = "skip_no_tenant"
            item.warnings.append("стаття без мережі (tenant_id NULL) — акція потребує мережу")
            plan.append(item)
            continue
        if (item.tenant_id, title) in existing:
            item.action = "exists"
            plan.append(item)
            continue
        spec = match_spec(title)
        if spec is not None:
            item.bot_text = spec.bot_text
            item.overrides = json.loads(json.dumps(spec.overrides))
            item.mention_brands = list(spec.mention_brands)
        else:
            item.bot_text = clean_text(art.get("promo_summary") or art.get("content") or "")
            item.warnings.append(
                "заголовок не в таблиці KNOWN_PROMOTIONS — overrides порожні, "
                "перевірте текст і умови в адмінці"
            )
        if not item.bot_text:
            item.action = "skip_empty_text"
            item.warnings.append("порожній текст для бота")
        plan.append(item)
    return plan


def format_plan(plan: list[PlannedPromotion], applied: bool) -> str:
    lines = [
        ("ЗАСТОСОВАНО" if applied else "DRY-RUN (нічого не записано)") + f": {len(plan)} статей"
    ]
    for p in plan:
        lines.append(f"- [{p.action}] {p.tenant_id} «{p.title}»")
        if p.action == "insert":
            lines.append(f"    {p.valid_from} … {p.valid_to}")
            lines.append(f"    overrides={json.dumps(p.overrides, ensure_ascii=False)}")
            lines.append(f"    mention_brands={p.mention_brands}")
            lines.append(f"    bot_text ({len(p.bot_text)}): {p.bot_text}")
        for w in p.warnings:
            lines.append(f"    ⚠ {w}")
    return "\n".join(lines)


async def run(conn: Any, apply: bool, today: date) -> list[PlannedPromotion]:
    """Read source + existing rows, build the plan, insert only when ``apply``."""
    src = await conn.execute(text(SELECT_SOURCE_SQL))
    articles = [dict(r) for r in src.mappings().all()]
    ex = await conn.execute(text(SELECT_EXISTING_SQL))
    existing = {(r["tenant_id"], r["title"]) for r in ex.mappings().all()}
    plan = build_plan(articles, existing, today)
    if apply:
        for p in plan:
            if p.action == "insert":
                await conn.execute(text(INSERT_SQL), p.insert_params())
    for p in plan:
        for w in p.warnings:
            logger.warning("%s «%s»: %s", p.article_id, p.title, w)
    return plan


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--apply", action="store_true", help="insert (default: dry-run)")
    args = parser.parse_args(argv)

    from sqlalchemy.ext.asyncio import create_async_engine

    from src.config import get_settings

    engine = create_async_engine(get_settings().database.url)
    try:
        async with engine.begin() as conn:
            plan = await run(conn, apply=args.apply, today=datetime.now(_KYIV).date())
    finally:
        await engine.dispose()
    print(format_plan(plan, applied=args.apply))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(asyncio.run(main()))
