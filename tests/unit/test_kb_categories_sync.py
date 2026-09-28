"""KB categories stay in sync everywhere they are listed.

Single source of truth: ``src.knowledge.categories.CATEGORIES``.  Every other
place that enumerates categories (LLM article processor, scraper API Literal,
seed script, filename detection) must accept exactly that set, and every
``knowledge_seed/<dir>/`` must map to a category of the same name.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

from scripts import seed_knowledge
from src.api import scraper
from src.knowledge import article_processor
from src.knowledge.categories import CATEGORY_VALUES
from src.knowledge.parsers import detect_category_from_filename

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "knowledge_seed"
EXPECTED = set(CATEGORY_VALUES)


def _seed_dirs() -> list[str]:
    return sorted(p.name for p in SEED.iterdir() if p.is_dir())


def test_source_of_truth_has_no_duplicates() -> None:
    assert len(CATEGORY_VALUES) == len(EXPECTED)


def test_article_processor_accepts_exactly_the_categories() -> None:
    assert set(article_processor._VALID_CATEGORIES) == EXPECTED
    for cat in CATEGORY_VALUES:
        assert article_processor._validate_category(cat) == cat


@pytest.mark.parametrize("prompt_name", ["_SYSTEM_PROMPT", "_SHOP_INFO_SYSTEM_PROMPT"])
def test_article_processor_prompts_offer_exactly_the_categories(prompt_name: str) -> None:
    prompt = getattr(article_processor, prompt_name)
    match = re.search(r'"category": "one of: ([^"]+)"', prompt)
    assert match, f"{prompt_name} lost its category list"
    offered = {c.strip() for c in match.group(1).split(",")}
    assert offered == EXPECTED
    assert "<CATEGORIES>" not in prompt


def test_scraper_literal_and_set_match_the_categories() -> None:
    assert set(get_args(scraper.KnowledgeCategory)) == EXPECTED
    assert set(scraper._VALID_CATEGORIES) == EXPECTED


def test_scraper_models_accept_every_category() -> None:
    for cat in CATEGORY_VALUES:
        page = scraper.WatchedPageCreate(url="https://example.com/page", category=cat)
        assert page.category == cat


def test_seed_script_patterns_match_the_categories() -> None:
    assert set(seed_knowledge.CATEGORY_PATTERNS) == EXPECTED


@pytest.mark.parametrize("dirname", _seed_dirs())
def test_every_seed_directory_is_a_category(dirname: str) -> None:
    assert dirname in EXPECTED


@pytest.mark.parametrize("dirname", _seed_dirs())
def test_seed_files_detect_their_directory_category(dirname: str) -> None:
    for path in sorted((SEED / dirname).glob("*.md")):
        assert detect_category_from_filename(path.name) == dirname, path.name
        assert seed_knowledge.detect_category(str(path)) == dirname, path.name


def test_seed_script_skips_the_tenant_manifest() -> None:
    files = seed_knowledge.list_seed_files(str(SEED))
    assert (SEED / "TENANTS.md").is_file()
    assert all(Path(f).name != "TENANTS.md" for f in files)
    assert len(files) == len(list(SEED.glob("*/*.md")))
