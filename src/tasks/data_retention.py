"""Data retention task — automatic cleanup per data-policy.md.

Runs weekly via Celery Beat:
- Deletes call_turns older than 90 days
- Deletes call_tool_calls older than 90 days
- Anonymizes caller_id in calls older than 1 year
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import get_settings
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)

_RETENTION_TRANSCRIPTS_DAYS = 90
_RETENTION_METADATA_DAYS = 365


@app.task(name="src.tasks.data_retention.cleanup_expired_data")  # type: ignore[untyped-decorator]
def cleanup_expired_data() -> dict[str, Any]:
    """Delete expired transcriptions and anonymize old caller data.

    Per data-policy.md:
    - Transcriptions (call_turns, call_tool_calls): 90 days
    - Caller metadata (caller_id): 1 year → anonymize
    """
    return asyncio.get_event_loop().run_until_complete(_cleanup_async())


async def _cleanup_async() -> dict[str, Any]:
    settings = get_settings()
    engine = create_async_engine(settings.database.url)

    results: dict[str, Any] = {}

    try:
        async with engine.begin() as conn:
            # Delete transcriptions older than 90 days
            r1 = await conn.execute(
                text(
                    f"DELETE FROM call_turns "
                    f"WHERE created_at < NOW() - INTERVAL '{_RETENTION_TRANSCRIPTS_DAYS} days'"
                )
            )
            results["call_turns_deleted"] = r1.rowcount

            r2 = await conn.execute(
                text(
                    f"DELETE FROM call_tool_calls "
                    f"WHERE created_at < NOW() - INTERVAL '{_RETENTION_TRANSCRIPTS_DAYS} days'"
                )
            )
            results["call_tool_calls_deleted"] = r2.rowcount

            # Anonymize caller_id older than 1 year
            r3 = await conn.execute(
                text(
                    f"UPDATE calls SET caller_id = 'DELETED' "
                    f"WHERE started_at < NOW() - INTERVAL '{_RETENTION_METADATA_DAYS} days' "
                    f"AND caller_id IS NOT NULL AND caller_id != 'DELETED'"
                )
            )
            results["calls_anonymized"] = r3.rowcount

        logger.info(
            "Data retention cleanup: turns=%d, tool_calls=%d, anonymized=%d",
            results["call_turns_deleted"],
            results["call_tool_calls_deleted"],
            results["calls_anonymized"],
        )

    except Exception:
        logger.exception("Data retention cleanup failed")
        raise
    finally:
        await engine.dispose()

    return results
