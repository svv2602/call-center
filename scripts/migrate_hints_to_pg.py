"""One-time migration: copy point hints from Redis to PostgreSQL.

Reads fitting:station_hints and pickup:point_hints from Redis,
inserts into point_hints table (ON CONFLICT DO NOTHING — idempotent).

Usage (inside docker container or with proper env vars):
    python -m scripts.migrate_hints_to_pg
"""

import asyncio
import json
import os
import sys


async def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    from redis.asyncio import Redis
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url)
    redis = Redis.from_url(redis_url, decode_responses=True)

    sources = [
        ("fitting:station_hints", "fitting_station"),
        ("pickup:point_hints", "pickup_point"),
    ]

    total = 0

    try:
        async with engine.begin() as conn:
            for redis_key, point_type in sources:
                raw = await redis.get(redis_key)
                if not raw:
                    print(f"  {redis_key}: no data in Redis, skipping")
                    continue

                hints = json.loads(raw)
                print(f"  {redis_key}: {len(hints)} hints found")

                for point_id, hint in hints.items():
                    await conn.execute(
                        text(
                            "INSERT INTO point_hints "
                            "(point_type, point_id, district, landmarks, description) "
                            "VALUES (:point_type, :point_id, :district, :landmarks, :description) "
                            "ON CONFLICT (point_type, point_id) DO NOTHING"
                        ),
                        {
                            "point_type": point_type,
                            "point_id": point_id,
                            "district": hint.get("district", ""),
                            "landmarks": hint.get("landmarks", ""),
                            "description": hint.get("description", ""),
                        },
                    )
                    total += 1

        print(f"Done: {total} hints migrated to PostgreSQL")
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
