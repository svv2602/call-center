"""A/B testing for prompt variants.

Randomly assigns calls to prompt variants and tracks metrics.
Provides statistical significance calculation for test results.
"""

from __future__ import annotations

import logging
import math
import random
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

QUALITY_CRITERIA = [
    "accuracy",
    "completeness",
    "politeness",
    "response_time",
    "problem_resolution",
    "language_quality",
    "tool_usage",
    "scenario_adherence",
]


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
        is_variant_a = random.random() < test["traffic_split"]

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
            call_id,
            test["test_name"],
            variant_label,
            variant_name,
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
            for _variant_field, quality_field, vid in [
                ("variant_a_id", "quality_a", test.variant_a_id),
                ("variant_b_id", "quality_b", test.variant_b_id),
            ]:
                q_result = await conn.execute(
                    text("""
                        SELECT
                            AVG(c.quality_score) AS avg_quality,
                            COUNT(*) AS count
                        FROM calls c
                        JOIN prompt_versions pv ON pv.name = c.prompt_version
                        WHERE pv.id = :version_id
                          AND c.quality_score IS NOT NULL
                    """),
                    {"version_id": str(vid)},
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
            row = updated.first()
            if row is None:
                msg = f"A/B test {test_id} not found after update"
                raise RuntimeError(msg)
            return dict(row._mapping)

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
            row = result.first()
            if row is None:
                msg = "Expected row from INSERT RETURNING"
                raise RuntimeError(msg)
            return dict(row._mapping)

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
            tests = []
            for row in result:
                test = dict(row._mapping)
                if test["status"] in ("completed", "stopped"):
                    test["significance"] = calculate_significance(
                        n_a=test["calls_a"],
                        n_b=test["calls_b"],
                        mean_a=float(test["quality_a"] or 0),
                        mean_b=float(test["quality_b"] or 0),
                    )
                else:
                    test["significance"] = None
                tests.append(test)
            return tests


    async def delete_test(self, test_id: UUID) -> None:
        """Delete an A/B test. Cannot delete an active test."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("SELECT status FROM prompt_ab_tests WHERE id = :test_id"),
                {"test_id": str(test_id)},
            )
            row = result.first()
            if not row:
                msg = f"A/B test {test_id} not found"
                raise ValueError(msg)
            if row.status == "active":
                msg = "Cannot delete an active A/B test — stop it first"
                raise ValueError(msg)

            await conn.execute(
                text("DELETE FROM prompt_ab_tests WHERE id = :test_id"),
                {"test_id": str(test_id)},
            )

    async def get_report(self, test_id: UUID) -> dict[str, Any]:
        """Generate a detailed analytics report for an A/B test."""
        async with self._engine.begin() as conn:
            # 1. Test metadata + summary
            result = await conn.execute(
                text("""
                    SELECT
                        t.id, t.test_name, t.variant_a_id, t.variant_b_id,
                        t.traffic_split, t.calls_a, t.calls_b,
                        t.quality_a, t.quality_b, t.status,
                        t.started_at, t.ended_at,
                        a.name AS variant_a_name,
                        b.name AS variant_b_name
                    FROM prompt_ab_tests t
                    JOIN prompt_versions a ON t.variant_a_id = a.id
                    JOIN prompt_versions b ON t.variant_b_id = b.id
                    WHERE t.id = :test_id
                """),
                {"test_id": str(test_id)},
            )
            row = result.first()
            if not row:
                msg = f"A/B test {test_id} not found"
                raise ValueError(msg)
            test_data = dict(row._mapping)

            variant_a_id = str(test_data["variant_a_id"])
            variant_b_id = str(test_data["variant_b_id"])
            test_started = test_data["started_at"]

            # Build time filter
            time_filter = "AND c.started_at >= :test_started"
            base_params: dict[str, Any] = {
                "version_a_id": variant_a_id,
                "version_b_id": variant_b_id,
                "test_started": test_started,
            }

            # Summary: avg duration, transfer rate per variant
            summary_result = await conn.execute(
                text(f"""
                    SELECT
                        SUM(CASE WHEN pv.id = :version_a_id THEN 1 ELSE 0 END) AS calls_a,
                        SUM(CASE WHEN pv.id = :version_b_id THEN 1 ELSE 0 END) AS calls_b,
                        AVG(CASE WHEN pv.id = :version_a_id THEN c.quality_score END) AS quality_a,
                        AVG(CASE WHEN pv.id = :version_b_id THEN c.quality_score END) AS quality_b,
                        AVG(CASE WHEN pv.id = :version_a_id THEN c.duration_seconds END) AS avg_duration_a,
                        AVG(CASE WHEN pv.id = :version_b_id THEN c.duration_seconds END) AS avg_duration_b,
                        AVG(CASE WHEN pv.id = :version_a_id AND c.transferred_to_operator THEN 1.0 ELSE CASE WHEN pv.id = :version_a_id THEN 0.0 END END) AS transfer_rate_a,
                        AVG(CASE WHEN pv.id = :version_b_id AND c.transferred_to_operator THEN 1.0 ELSE CASE WHEN pv.id = :version_b_id THEN 0.0 END END) AS transfer_rate_b
                    FROM calls c
                    JOIN prompt_versions pv ON pv.name = c.prompt_version
                    WHERE pv.id IN (:version_a_id, :version_b_id)
                      {time_filter}
                """),
                base_params,
            )
            s = summary_result.first()

            significance = calculate_significance(
                n_a=int(s.calls_a or 0) if s else 0,
                n_b=int(s.calls_b or 0) if s else 0,
                mean_a=float(s.quality_a or 0) if s else 0.0,
                mean_b=float(s.quality_b or 0) if s else 0.0,
            )

            summary = {
                "calls_a": int(s.calls_a or 0) if s else 0,
                "calls_b": int(s.calls_b or 0) if s else 0,
                "quality_a": round(float(s.quality_a or 0), 4) if s and s.quality_a else None,
                "quality_b": round(float(s.quality_b or 0), 4) if s and s.quality_b else None,
                "avg_duration_a": round(float(s.avg_duration_a or 0), 1) if s and s.avg_duration_a else None,
                "avg_duration_b": round(float(s.avg_duration_b or 0), 1) if s and s.avg_duration_b else None,
                "transfer_rate_a": round(float(s.transfer_rate_a or 0), 4) if s and s.transfer_rate_a else None,
                "transfer_rate_b": round(float(s.transfer_rate_b or 0), 4) if s and s.transfer_rate_b else None,
                "significance": significance,
            }

            # 2. Per-criterion breakdown from quality_details JSONB
            per_criterion = []
            for criterion in QUALITY_CRITERIA:
                cr_result = await conn.execute(
                    text(f"""
                        SELECT
                            AVG(CASE WHEN pv.id = :version_a_id
                                THEN (c.quality_details->>:criterion)::float END) AS avg_a,
                            AVG(CASE WHEN pv.id = :version_b_id
                                THEN (c.quality_details->>:criterion)::float END) AS avg_b,
                            COUNT(CASE WHEN pv.id = :version_a_id
                                AND c.quality_details ? :criterion THEN 1 END) AS count_a,
                            COUNT(CASE WHEN pv.id = :version_b_id
                                AND c.quality_details ? :criterion THEN 1 END) AS count_b
                        FROM calls c
                        JOIN prompt_versions pv ON pv.name = c.prompt_version
                        WHERE pv.id IN (:version_a_id, :version_b_id)
                          AND c.quality_details IS NOT NULL
                          {time_filter}
                    """),
                    {**base_params, "criterion": criterion},
                )
                cr = cr_result.first()
                per_criterion.append({
                    "criterion": criterion,
                    "avg_a": round(float(cr.avg_a), 4) if cr and cr.avg_a is not None else None,
                    "avg_b": round(float(cr.avg_b), 4) if cr and cr.avg_b is not None else None,
                    "count_a": int(cr.count_a) if cr else 0,
                    "count_b": int(cr.count_b) if cr else 0,
                })

            # 3. By scenario
            sc_result = await conn.execute(
                text(f"""
                    SELECT
                        c.scenario,
                        SUM(CASE WHEN pv.id = :version_a_id THEN 1 ELSE 0 END) AS calls_a,
                        SUM(CASE WHEN pv.id = :version_b_id THEN 1 ELSE 0 END) AS calls_b,
                        AVG(CASE WHEN pv.id = :version_a_id THEN c.quality_score END) AS quality_a,
                        AVG(CASE WHEN pv.id = :version_b_id THEN c.quality_score END) AS quality_b
                    FROM calls c
                    JOIN prompt_versions pv ON pv.name = c.prompt_version
                    WHERE pv.id IN (:version_a_id, :version_b_id)
                      {time_filter}
                      AND c.scenario IS NOT NULL
                    GROUP BY c.scenario
                    ORDER BY (SUM(CASE WHEN pv.id = :version_a_id THEN 1 ELSE 0 END) +
                              SUM(CASE WHEN pv.id = :version_b_id THEN 1 ELSE 0 END)) DESC
                """),
                base_params,
            )
            by_scenario = [
                {
                    "scenario": r.scenario,
                    "calls_a": int(r.calls_a),
                    "calls_b": int(r.calls_b),
                    "quality_a": round(float(r.quality_a), 4) if r.quality_a is not None else None,
                    "quality_b": round(float(r.quality_b), 4) if r.quality_b is not None else None,
                }
                for r in sc_result
            ]

            # 4. Daily dynamics
            daily_result = await conn.execute(
                text(f"""
                    SELECT
                        DATE(c.started_at) AS day,
                        SUM(CASE WHEN pv.id = :version_a_id THEN 1 ELSE 0 END) AS calls_a,
                        SUM(CASE WHEN pv.id = :version_b_id THEN 1 ELSE 0 END) AS calls_b,
                        AVG(CASE WHEN pv.id = :version_a_id THEN c.quality_score END) AS quality_a,
                        AVG(CASE WHEN pv.id = :version_b_id THEN c.quality_score END) AS quality_b
                    FROM calls c
                    JOIN prompt_versions pv ON pv.name = c.prompt_version
                    WHERE pv.id IN (:version_a_id, :version_b_id)
                      {time_filter}
                    GROUP BY DATE(c.started_at)
                    ORDER BY DATE(c.started_at)
                """),
                base_params,
            )
            daily = [
                {
                    "date": str(r.day),
                    "calls_a": int(r.calls_a),
                    "calls_b": int(r.calls_b),
                    "quality_a": round(float(r.quality_a), 4) if r.quality_a is not None else None,
                    "quality_b": round(float(r.quality_b), 4) if r.quality_b is not None else None,
                }
                for r in daily_result
            ]

        return {
            "test": {
                "id": str(test_data["id"]),
                "test_name": test_data["test_name"],
                "variant_a_name": test_data["variant_a_name"],
                "variant_b_name": test_data["variant_b_name"],
                "status": test_data["status"],
                "started_at": str(test_data["started_at"]) if test_data["started_at"] else None,
                "ended_at": str(test_data["ended_at"]) if test_data["ended_at"] else None,
            },
            "summary": summary,
            "per_criterion": per_criterion,
            "by_scenario": by_scenario,
            "daily": daily,
        }


def calculate_significance(
    n_a: int,
    n_b: int,
    mean_a: float,
    mean_b: float,
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
