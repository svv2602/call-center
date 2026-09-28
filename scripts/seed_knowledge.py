"""Import knowledge_seed/*.md articles directly into the database.

Usage (inside docker container or with proper DATABASE_URL):
    python -m scripts.seed_knowledge
"""

import asyncio
import glob
import os
import re
import sys

from sqlalchemy.exc import IntegrityError

from src.knowledge.categories import CATEGORY_VALUES

CATEGORY_PATTERNS = CATEGORY_VALUES

# Service files in knowledge_seed/ that are not KB articles (tenant manifest).
SKIP_FILES = frozenset({"TENANTS.md"})


def list_seed_files(root: str = "knowledge_seed") -> list[str]:
    """All seed articles under *root*, service files excluded."""
    files = glob.glob(os.path.join(root, "**", "*.md"), recursive=True)
    return sorted(f for f in files if os.path.basename(f) not in SKIP_FILES)


def detect_category(filepath: str) -> str:
    """Detect category from file path (directory name)."""
    parts = filepath.replace("\\", "/").split("/")
    # knowledge_seed/<category>/filename.md
    if len(parts) >= 2:
        parent = parts[-2].lower()
        if parent in CATEGORY_PATTERNS:
            return parent
    # Fallback: detect from filename
    fname = os.path.basename(filepath).lower()
    for cat in CATEGORY_PATTERNS:
        if re.search(rf"(?:^|[\W_]){cat}(?:[\W_]|$)", fname):
            return cat
    return "general"


def parse_md(filepath: str) -> tuple[str, str]:
    """Parse .md file: extract title from first # heading, rest is body."""
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    lines = content.strip().split("\n")
    title = os.path.splitext(os.path.basename(filepath))[0].replace("_", " ").strip()

    for line in lines:
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            break

    return title, content


async def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url)

    files = list_seed_files()
    print(f"Found {len(files)} seed files")

    ok = 0
    skipped = 0
    errors = 0

    async with engine.begin() as conn:
        # Check existing articles to avoid duplicates
        result = await conn.execute(text("SELECT title FROM knowledge_articles"))
        existing = {row[0] for row in result}
        print(f"Existing articles in DB: {len(existing)}")

        for filepath in files:
            title, content = parse_md(filepath)
            category = detect_category(filepath)

            if title in existing:
                skipped += 1
                continue

            try:
                nested = await conn.begin_nested()
                await conn.execute(
                    text("""
                        INSERT INTO knowledge_articles (title, category, content, embedding_status)
                        VALUES (:title, :category, :content, 'pending')
                    """),
                    {"title": title, "category": category, "content": content},
                )
                await nested.commit()
                ok += 1
                existing.add(title)
            except IntegrityError:
                await nested.rollback()
                skipped += 1
            except Exception as exc:
                errors += 1
                print(f"  ERR {filepath}: {exc}")

    await engine.dispose()
    print(f"Done: {ok} imported, {skipped} skipped (already exist), {errors} errors")


if __name__ == "__main__":
    asyncio.run(main())
