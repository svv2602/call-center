"""One-off converter: tshina_new TireConsultant topics → knowledge_seed/*.md.

Reads the ``translations.ua`` of selected topics from the TireConsultant
seeder JSON files and writes voice-ready Markdown articles (``NN_category_topic.md``).
Deterministic, no network, no DB.  Re-running overwrites the same files with
the same bytes.

Only topics that are *not* already covered by ``knowledge_seed/`` and carry no
network-specific (site) conditions are converted; the full topic → decision
table lives in the wave checklist
``development-checklists/orders-consult-networks-2026-09-28/wave-3-H-kb-tshina-faq-and-wheels``.

The one article built from ``Config/answer_safety_rules.php`` has no body in
the source (the rules are prompt instructions), so its text is written here
in customer-facing Ukrainian.

Usage:
    python -m scripts.import_tshina_faq [--source DIR] [--dest knowledge_seed]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DEFAULT_SOURCE = Path(
    "/home/snisar/RubyProjects/tshina_new/Modules/TireConsultant/Database/Seeders/data"
)
DEFAULT_DEST = Path(__file__).resolve().parent.parent / "knowledge_seed"

# slug → (source json, destination path relative to knowledge_seed, appendix)
TOPICS: dict[str, tuple[str, str, str]] = {
    "tire-low-profile": (
        "tire_basics_knowledge_topics.json",
        "faq/34_faq_nyzkoprofilni_shyny.md",
        "",
    ),
    "tire-break-in": (
        "tire_basics_knowledge_topics.json",
        "faq/35_faq_obkatka_novykh_shyn.md",
        "",
    ),
    "tire-ece-approval-mark": (
        "tire_extra_knowledge_topics.json",
        "faq/36_faq_znak_e_na_shyni.md",
        "",
    ),
    "tire-sidewall-symbols": (
        "tire_extra_knowledge_topics.json",
        "faq/37_faq_poznachennya_na_bokovyni.md",
        "",
    ),
    "tire-lt-load-range": (
        "tire_extra_knowledge_topics.json",
        "faq/38_faq_lt_load_range_pr.md",
        "",
    ),
    "tire-new-stock-age": (
        "tire_extra_knowledge_topics.json",
        "faq/39_faq_nova_shyna_mynuloho_roku.md",
        "",
    ),
    "tire-studs-rules": (
        "tire_extra_knowledge_topics.json",
        "faq/40_faq_shypovani_shyny_pravyla.md",
        "## Шиповані шини в нашому асортименті\n\n"
        "Шипованих шин у нас немає. Для зими пропонуємо фрикційні шини (липучку). "
        "Важливо чесно сказати: на чистому льоду й укоченому снігу липучка "
        "поступається шипованим шинам, тому на таких дорогах потрібна обережніша їзда.",
    ),
    "tire-segments": (
        "tire_basics_knowledge_topics.json",
        "guides/24_guides_premium_seredniy_ekonom.md",
        "",
    ),
    "disk-replica": (
        "disk_knowledge_topics.json",
        "wheels/08_wheels_replika_ta_oryhinalni_dysky.md",
        "",
    ),
    "wheel-fasteners-tpms": (
        "disk_knowledge_topics.json",
        "wheels/09_wheels_kriplennya_ta_datchyky_tysku.md",
        "",
    ),
}

SAFETY_ARTICLE_PATH = "faq/41_faq_zasterezhennya_pry_vybori_shyn.md"
# Source: Config/answer_safety_rules.php (global_rules + conditional_rules).
SAFETY_ARTICLE = """# Що важливо знати при виборі шин: чесні обмеження

## RunFlat — не безмежний запас

Після проколу на шині RunFlat можна доїхати до шиномонтажу, але з обмеженнями виробника: зазвичай швидкість не більше 80 км/год і відстань не більше 80 км. Чи вдасться їхати далі, залежить від пошкодження. Обіцяти, що на RunFlat можна їхати після будь-якого проколу, не можна.

## Індекс навантаження — вищий не означає кращий

Індекс навантаження має відповідати вимозі автовиробника — з таблички на стійці дверей або з інструкції. Нижчий ставити не можна. Вищий допустимий, але сам по собі не робить шину надійнішою чи безпечнішою.

## На одній осі — однакові шини

На одну вісь не ставлять шини різних моделей, розмірів, конструкцій або з помітно різним зносом.

## Всесезонні шини — залежить від регіону

Чи підійдуть всесезонні шини, залежить від клімату й доріг, якими ви їздите. Для снігу шукайте маркування 3PMSF (сніжинка на тлі гори). Сказати, що всесезонні підходять усім і цілий рік, не можна.

## Економія пального і гальмівний шлях

Скільки літрів пального збереже шина або на скільки метрів коротший гальмівний шлях, заздалегідь пообіцяти не можна. Порівнювати моделі можна за класами EU-етикетки: опір коченню і зчеплення на мокрій дорозі — від A до E.

## Бренд і модель

Репутація бренду не переноситься автоматично на кожну модель. Порівнюють конкретну модель у потрібному розмірі.

## Нештатний розмір

Ми пропонуємо шини в розмірі, який передбачив автовиробник, або в розмірі, який назвав клієнт. Інший розмір по телефону не пропонуємо — його підбирає спеціаліст.
"""

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+")
_BOLD_HEADING = re.compile(r"^\*\*([^*]+)\*\*$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# A sentence matching any of these is site/chat specific and is dropped:
# links to other chat topics, the web consultant, cards/pages of the site,
# "no operator / leave your number" (the call center HAS operators).
DROP_SENTENCE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"окрем(?:ій|их|а) тем",
        r"\bу темі\b",
        r"\bтемах довідки\b",
        r"див\. тем",
        r"консультант",
        r"на сайті",
        r"сторінц",
        r"картц[іи]|картк[аиу]\b",
        r"оператора немає",
        r"залиште (?:номер|заявку)",
        r"tshina",
        r"шинному калькулятор",
    )
)


def clean_body(body: str) -> str:
    """Strip links/URLs and site-specific sentences; bold-only lines → ``##``."""
    body = _MD_LINK.sub(r"\1", body)
    body = _URL.sub("", body)
    out: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        heading = _BOLD_HEADING.match(stripped)
        if heading:
            out.append(f"## {heading.group(1).strip()}")
            continue
        sentences = _SENTENCE_SPLIT.split(line)
        kept = [s for s in sentences if not any(p.search(s) for p in DROP_SENTENCE_PATTERNS)]
        if sentences and not kept:
            continue  # whole line was site-specific
        out.append(" ".join(kept).rstrip())
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def render(title: str, body: str, appendix: str) -> str:
    parts = [f"# {title.strip()}", clean_body(body)]
    if appendix:
        parts.append(appendix.strip())
    return "\n\n".join(parts) + "\n"


def load_topics(source: Path) -> dict[str, dict]:
    topics: dict[str, dict] = {}
    for name in sorted({src for src, _, _ in TOPICS.values()}):
        for topic in json.loads((source / name).read_text(encoding="utf-8")):
            topics[topic["slug"]] = topic
    return topics


def build(source: Path) -> dict[str, str]:
    """Return {relative destination path: file content}."""
    topics = load_topics(source)
    files: dict[str, str] = {}
    for slug, (_src, dest, appendix) in TOPICS.items():
        ua = topics[slug]["translations"]["ua"]
        files[dest] = render(ua["title"], ua["body_md"], appendix)
    files[SAFETY_ARTICLE_PATH] = SAFETY_ARTICLE
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    args = parser.parse_args()
    for rel, content in build(args.source).items():
        path = args.dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
