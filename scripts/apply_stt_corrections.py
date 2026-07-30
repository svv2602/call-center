"""Import a curated STT-corrections TSV/CSV into the running backend.

Reads a file produced (and edited) from ``scripts/mine_stt_candidates.py``
and POSTs the rows to ``/admin/stt/corrections/bulk``. Rows whose
``replacement`` column is empty are considered *not yet curated* and are
skipped — the mining script pre-fills everything except the replacement.

Usage::

    # Dry-run — parse the file and print what would be sent
    python -m scripts.apply_stt_corrections \\
        --file /tmp/stt_candidates.tsv --url http://localhost:8080 --dry-run

    # Real import (JWT token from Admin UI localStorage or /auth/login)
    python -m scripts.apply_stt_corrections \\
        --file /tmp/stt_candidates.tsv \\
        --url https://call-center.example.com \\
        --token eyJ...

The file may be either TSV (default, matches the mining output) or CSV
(``--delimiter=,``). Both must have a header row containing at minimum:
``pattern``, ``replacement``, and optionally ``context_hint``, ``enabled``,
``note``, ``flags``.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _parse_bool(v: str | None) -> bool:
    if v is None:
        return True
    return v.strip().lower() not in ("false", "0", "no", "off", "")


def _load_rows(path: str, delimiter: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        if reader.fieldnames is None:
            raise ValueError("file has no header row")
        missing = {"pattern", "replacement"} - set(reader.fieldnames)
        if missing:
            raise ValueError(f"missing required columns: {sorted(missing)}")

        rules: list[dict[str, Any]] = []
        for i, row in enumerate(reader, start=2):  # +1 header, 1-based
            pattern = (row.get("pattern") or "").strip()
            replacement = row.get("replacement") or ""
            if not pattern:
                logger.debug("row %d: empty pattern — skipped", i)
                continue
            if not replacement.strip():
                logger.debug("row %d: empty replacement — skipped (uncurated)", i)
                continue
            rules.append(
                {
                    "pattern": pattern,
                    "replacement": replacement,
                    "context_hint": (row.get("context_hint") or "").strip(),
                    "flags": (row.get("flags") or "i").strip() or "i",
                    "enabled": _parse_bool(row.get("enabled")),
                    "note": row.get("note") or "",
                }
            )
        return rules


def _post_bulk(
    url: str, token: str | None, rules: list[dict[str, Any]], skip_duplicates: bool
) -> dict[str, Any]:
    payload = json.dumps(
        {"rules": rules, "skip_duplicates": skip_duplicates}, ensure_ascii=False
    ).encode("utf-8")
    req = urlrequest.Request(
        f"{url.rstrip('/')}/admin/stt/corrections/bulk",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {detail}") from exc
    return json.loads(body)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="TSV/CSV of curated rules")
    parser.add_argument(
        "--delimiter",
        default="\t",
        help="column delimiter — '\\t' (default) or ','",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8080",
        help="backend base URL (e.g. http://192.168.11.53:8080)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="JWT bearer token — required unless the backend has auth disabled",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and validate only, do not POST",
    )
    parser.add_argument(
        "--no-skip-duplicates",
        action="store_true",
        help="allow inserting rules whose pattern already exists",
    )
    args = parser.parse_args()

    rules = _load_rows(args.file, args.delimiter)
    logger.info("parsed %d curated rules from %s", len(rules), args.file)

    if not rules:
        logger.warning(
            "no curated rules found — did you fill the `replacement` column?"
        )
        return

    if args.dry_run:
        for i, r in enumerate(rules):
            logger.info(
                "[%d] pattern=%r replacement=%r ctx=%s",
                i,
                r["pattern"],
                r["replacement"],
                r["context_hint"] or "any",
            )
        return

    result = _post_bulk(
        args.url, args.token, rules, skip_duplicates=not args.no_skip_duplicates
    )
    logger.info(
        "server response: created=%d skipped=%d errors=%d",
        result.get("created", 0),
        result.get("skipped", 0),
        result.get("errors", 0),
    )
    for row in result.get("results", []):
        status = row.get("status")
        if status == "error":
            logger.warning(
                "row %s: %s (%s)", row.get("index"), status, row.get("error")
            )
        elif status == "skipped":
            logger.info(
                "row %s: skipped (%s)", row.get("index"), row.get("reason")
            )
        else:
            logger.debug("row %s: created id=%s", row.get("index"), row.get("id"))

    if result.get("errors", 0):
        sys.exit(1)


if __name__ == "__main__":
    main()
