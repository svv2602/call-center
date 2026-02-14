"""A/B testing for prompt variants.

Randomly assigns calls to prompt variants and tracks metrics.
Provides statistical significance calculation for test results.
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


class ABTestManager:
    """Manages A/B tests for prompt variants."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_active_test(self) -> dict[str, Any] | None:
        """Get the currently active A/B test."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT
                        t.id, t.test_name, t.variant_a_id, t.variant_b_id,
                        t.traffic_split, t.calls_a, t.calls_b,
                        t.quality_a, t.quality_b, t.status, t.started_at,
                        a.name AS variant_a_name,
                        b.name AS variant_b_name
                    FROM prompt_ab_tests t
                    JOIN prompt_versions a ON t.variant_a_id = a.id
                    JOIN prompt_versions b ON t.variant_b_id = b.id
                    WHERE t.status = 'active'
                    ORDER BY t.started_at DESC
                    LIMIT 1
                """)
            )
            row = result.first()
            return dict(row._mapping) if row else None

    async def assign_variant(self, call_id: str) -> dict[str, Any] | None:
        """Assign a prompt variant to a call based on active A/B test.

        Returns:
            Dict with variant info (prompt_version_id, variant_name),
            or None if no active test.
        """
        test = await self.get_active_test()
        if not test:
            return None

        # Random assignment based on traffic split
        is_variant_a = random.random() < test["traffic_split"]  # noqa: S311

        variant_id = test["variant_a_id"] if is_variant_a else test["variant_b_id"]
        variant_name = test["variant_a_name"] if is_variant_a else test["variant_b_name"]
        variant_label = "A" if is_variant_a else "B"

        # Increment call count
        count_field = "calls_a" if is_variant_a else "calls_b"
        async with self._engine.begin() as conn:
            await conn.execute(
                text(f"""
                    UPDATE prompt_ab_tests
                    SET {count_field} = {count_field} + 1
                    WHERE id = :test_id
                """),
                {"test_id": str(test["id"])},
            )

        logger.info(
            "A/B test assignment: call=%s, test=%s, variant=%s (%s)",
            call_id, test["test_name"], variant_label, variant_name,
        )

        return {
            "test_id": test["id"],
            "prompt_version_id": variant_id,
            "variant_name": variant_name,
            "variant_label": variant_label,
        }

    async def update_quality(self, test_id: UUID) -> dict[str, Any]:
        """Recalculate quality scores for both variants of a test."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT variant_a_id, variant_b_id
                    FROM prompt_ab_tests
                    WHERE id = :test_id
                """),
                {"test_id": str(test_id)},
            )
            test = result.first()
            if not test:
                msg = f"A/B test {test_id} not found"
                raise ValueError(msg)

            # Calculate average quality for each variant
            for variant_field, quality_field, vid in [
                ("variant_a_id", "quality_a", test.variant_a_id),
                ("variant_b_id", "quality_b", test.variant_b_id),
            ]:
                q_result = await conn.execute(
                    text("""
                        SELECT
                            AVG(quality_score) AS avg_quality,
                            COUNT(*) AS count
                        FROM calls
                        WHERE prompt_version = :version_name
                          AND quality_score IS NOT NULL
                    """),
                    {"version_name": str(vid)},
                )
                q_row = q_result.first()
                avg_quality = float(q_row.avg_quality) if q_row and q_row.avg_quality else 0.0

                await conn.execute(
                    text(f"""
                        UPDATE prompt_ab_tests
                        SET {quality_field} = :avg_quality
                        WHERE id = :test_id
                    """),
                    {"avg_quality": avg_quality, "test_id": str(test_id)},
                )

            # Return updated test
            updated = await conn.execute(
                text("SELECT * FROM prompt_ab_tests WHERE id = :test_id"),
                {"test_id": str(test_id)},
            )
            return dict(updated.first()._mapping)

    async def create_test(
        self,
        test_name: str,
        variant_a_id: UUID,
        variant_b_id: UUID,
        traffic_split: float = 0.5,
    ) -> dict[str, Any]:
        """Create a new A/B test."""
        async with self._engine.begin() as conn:
            # Stop any existing active tests
            await conn.execute(
                text("""
                    UPDATE prompt_ab_tests
                    SET status = 'stopped', ended_at = now()
                    WHERE status = 'active'
                """)
            )

            result = await conn.execute(
                text("""
                    INSERT INTO prompt_ab_tests
                        (test_name, variant_a_id, variant_b_id, traffic_split, status)
                    VALUES (:test_name, :variant_a_id, :variant_b_id, :traffic_split, 'active')
                    RETURNING id, test_name, variant_a_id, variant_b_id, traffic_split,
                              status, started_at
                """),
                {
                    "test_name": test_name,
                    "variant_a_id": str(variant_a_id),
                    "variant_b_id": str(variant_b_id),
                    "traffic_split": traffic_split,
                },
            )
            return dict(result.first()._mapping)

    async def stop_test(self, test_id: UUID) -> dict[str, Any]:
        """Stop an A/B test and record the end time."""
        # Update quality scores first
        await self.update_quality(test_id)

        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("""
                    UPDATE prompt_ab_tests
                    SET status = 'completed', ended_at = now()
                    WHERE id = :test_id
                    RETURNING *
                """),
                {"test_id": str(test_id)},
            )
            row = result.first()
            if not row:
                msg = f"A/B test {test_id} not found"
                raise ValueError(msg)

            test_data = dict(row._mapping)

            # Calculate statistical significance
            test_data["significance"] = calculate_significance(
                n_a=test_data["calls_a"],
                n_b=test_data["calls_b"],
                mean_a=test_data["quality_a"],
                mean_b=test_data["quality_b"],
            )

            return test_data

    async def list_tests(self) -> list[dict[str, Any]]:
        """List all A/B tests."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT
                        t.*, a.name AS variant_a_name, b.name AS variant_b_name
                    FROM prompt_ab_tests t
                    JOIN prompt_versions a ON t.variant_a_id = a.id
                    JOIN prompt_versions b ON t.variant_b_id = b.id
                    ORDER BY t.started_at DESC
                """)
            )
            return [dict(row._mapping) for row in result]


def calculate_significance(
    n_a: int, n_b: int, mean_a: float, mean_b: float,
    std_dev: float = 0.2,
) -> dict[str, Any]:
    """Calculate statistical significance using a Z-test approximation.

    Args:
        n_a: Number of samples for variant A.
        n_b: Number of samples for variant B.
        mean_a: Mean quality score for variant A.
        mean_b: Mean quality score for variant B.
        std_dev: Assumed standard deviation (default 0.2 for quality scores 0-1).

    Returns:
        Dict with z_score, p_value_approx, is_significant, recommended_variant.
    """
    if n_a < 10 or n_b < 10:
        return {
            "z_score": 0.0,
            "p_value_approx": 1.0,
            "is_significant": False,
            "recommended_variant": None,
            "min_samples_needed": 30,
        }

    se = std_dev * math.sqrt(1.0 / n_a + 1.0 / n_b)
    if se == 0:
        return {
            "z_score": 0.0,
            "p_value_approx": 1.0,
            "is_significant": False,
            "recommended_variant": None,
        }

    z_score = (mean_a - mean_b) / se

    # Approximate p-value using error function
    p_value = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z_score) / math.sqrt(2.0))))

    is_significant = p_value < 0.05
    recommended = None
    if is_significant:
        recommended = "A" if mean_a > mean_b else "B"

    return {
        "z_score": round(z_score, 4),
        "p_value_approx": round(p_value, 4),
        "is_significant": is_significant,
        "recommended_variant": recommended,
    }
