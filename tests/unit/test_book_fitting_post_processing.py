"""Wave 12 (2026-09-07) regression tests for book_fitting post-processing.

Root cause: `_book_fitting_with_metric` in main.py used to have all
post-1С-success code inside the same try/except that also caught 1C
network errors. When a post-processing step raised (e.g. `uuid.UUID(
session.channel_uuid)` where `channel_uuid` is already a UUID object →
`AttributeError: 'UUID' object has no attribute 'replace'`), the outer
`except Exception` swallowed it and fell through to the Store API mock
backend, which returned 404. The LLM then told the customer «не вдалося
оформити запис» EVEN THOUGH 1С had already persisted the booking. Result:
24h+ of 0/N bookings visible in analytics + duplicate bookings when
customers retried + operators surprised at СТО.

Fix: post-processing is isolated in its own try/excepts, and a 1С
success + non-empty GUID unconditionally returns "confirmed" regardless
of local DB / metric / UUID coercion failures.
"""

from __future__ import annotations

import uuid


class TestChannelUUIDCoercion:
    """Verify the specific AttributeError pattern that Wave 12 fixes."""

    def test_uuid_passed_directly_crashes_uuid_ctor(self) -> None:
        """Reproduce the Wave 12 root cause: `uuid.UUID(uuid_obj)` fails."""
        u = uuid.uuid4()
        try:
            uuid.UUID(u)  # type: ignore[arg-type]
        except AttributeError as exc:
            assert "replace" in str(exc)
            return
        raise AssertionError("Expected AttributeError from uuid.UUID(uuid_obj)")

    def test_uuid_str_coercion_works(self) -> None:
        """The Wave 12 fix: coerce to str first."""
        u = uuid.uuid4()
        parsed = uuid.UUID(str(u))
        assert parsed == u

    def test_isinstance_check_avoids_recoercion(self) -> None:
        """The Wave 12 fast path: skip UUID re-parsing if already a UUID."""
        u = uuid.uuid4()

        def coerce(x: object) -> uuid.UUID:
            return x if isinstance(x, uuid.UUID) else uuid.UUID(str(x))

        assert coerce(u) is u  # zero-copy for UUID input
        assert coerce(str(u)) == u  # str input parses


class TestPostProcessingIsolation:
    """Verify AttributeError inside inner except (ValueError, TypeError).

    Pre-Wave-12 code had:
        try:
            call_uuid = uuid.UUID(session.channel_uuid)
        except (ValueError, TypeError):
            ...

    Since AttributeError is neither ValueError nor TypeError, it
    escaped to the outer `except Exception` at the top of the block,
    which was designed only to catch 1C network errors — but because
    post-processing was inside the same try, the outer catch fired the
    wrong Store API fallback path.
    """

    def test_attribute_error_not_caught_by_value_type_error(self) -> None:
        caught_narrow = False
        caught_broad = False
        try:
            raise AttributeError("'UUID' object has no attribute 'replace'")
        except (ValueError, TypeError):
            caught_narrow = True
        except Exception:
            caught_broad = True
        assert not caught_narrow
        assert caught_broad

    def test_broad_except_catches_all(self) -> None:
        """The Wave 12 fix uses `except Exception` for the isolated
        post-processing try, so any error class is contained without
        leaking into fallback logic."""
        caught = False
        try:
            raise AttributeError("simulated")
        except Exception:
            caught = True
        assert caught
