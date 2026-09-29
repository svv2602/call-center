"""Run the tshina Data API sync by hand.

    python -m scripts.tshina_sync                          # incremental, all resources
    python -m scripts.tshina_sync --resource eu-labels     # one resource (repeatable)
    python -m scripts.tshina_sync --full                   # full snapshot + guarded sweep
    python -m scripts.tshina_sync --dry-run                # read the API, print counts only

Needs ``TSHINA_API_BASE_URL`` and ``TSHINA_API_TOKEN`` (plus
``TSHINA_API_BASIC_USER``/``TSHINA_API_BASIC_PASSWORD`` on the stand);
without them it prints «disabled» and sends nothing. ``--dry-run`` writes
nothing: no rows, no watermark, no lock (it only reads the stored watermark).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from src.integrations.tshina_api import RESOURCES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--resource", action="append", choices=RESOURCES, help="repeatable")
    parser.add_argument("--full", action="store_true", help="full snapshot with the sweep")
    parser.add_argument("--dry-run", action="store_true", help="read the API, write nothing")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from src.tasks.tshina_sync_tasks import run_tshina_sync

    result = asyncio.run(
        run_tshina_sync(full=args.full, resources=args.resource, dry_run=args.dry_run)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") in ("ok", "dry_run", "disabled") else 1


if __name__ == "__main__":
    sys.exit(main())
