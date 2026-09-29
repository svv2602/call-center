"""HTTP client for the tshina Data API (read-only, cursor-paged).

Contract (accepted 2026-09-29): ``GET /api/v1/<resource>`` with
``updated_since`` (ISO-8601 with a zone), ``cursor`` and ``limit`` (≤ 5000);
the answer is ``{"items": [...], "next_cursor": str | null, "server_time": str}``.
The next incremental run starts from ``server_time`` of the FIRST page of
the previous walk — the caller takes it from the first yielded page.

Errors: 429 → wait ``Retry-After`` (capped at 60 s) and retry; 5xx and
network errors → retry with a pause, at most ``retries`` times; 400/401/403
and any other 4xx → fail at once with the server's ``{"error"}`` text.
Requests are spaced to stay under ``max_rps`` (the API allows 600/min, the
agreement is ≤ 5/s).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

logger = logging.getLogger(__name__)

RESOURCES: tuple[str, ...] = (
    "eu-labels",
    "tire-tests",
    "vehicles/brands",
    "vehicles/models",
    "vehicles/modifications",
    "vehicles/tire-sizes",
    "vehicles/disk-sizes",
)
MAX_LIMIT = 5000
MAX_RETRY_AFTER = 60.0


class TshinaApiError(Exception):
    """A request that failed for good (after retries, or not retryable).

    ``kind``: ``auth`` (401/403), ``bad_request`` (400), ``http`` (other
    status), ``rate_limit`` (429 retries exhausted), ``network`` (connection
    or timeout, retries exhausted), ``protocol`` (a 200 that breaks the contract).
    """

    def __init__(self, kind: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{kind}: {message}" if status is None else f"{kind} {status}: {message}")
        self.kind = kind
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Page:
    items: list[dict[str, Any]]
    next_cursor: str | None
    server_time: str


def format_since(value: datetime) -> str:
    """``updated_since`` value: UTC, seconds precision, trailing ``Z``."""
    if value.tzinfo is None:
        raise ValueError("updated_since must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_server_time(value: Any) -> datetime:
    """``server_time`` of a page → aware datetime; contract breach → ``TshinaApiError``."""
    if not isinstance(value, str) or not value:
        raise TshinaApiError("protocol", f"server_time missing or not a string: {value!r}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TshinaApiError("protocol", f"server_time not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise TshinaApiError("protocol", f"server_time without a zone: {value!r}")
    return parsed.astimezone(UTC)


def _retry_after_seconds(header: str | None, default: float) -> float:
    try:
        seconds = float(header) if header is not None else default
    except ValueError:
        seconds = default
    return max(0.0, min(seconds, MAX_RETRY_AFTER))


def _error_text(body: Any, raw: str) -> str:
    if isinstance(body, dict) and body.get("error"):
        return str(body["error"])
    return raw[:500] or "(empty body)"


class TshinaApiClient:
    """Read-only client; one instance per sync run."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        basic_user: str = "",
        basic_password: str = "",
        timeout: float = 60,
        session: aiohttp.ClientSession | None = None,
        max_rps: float = 5.0,
        retries: int = 3,
        retry_pause: float = 2.0,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not base_url.strip() or not token.strip():
            raise ValueError("tshina API needs both base_url and token")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._basic_user = basic_user
        self._basic_password = basic_password
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session = session
        self._own_session = session is None
        self._min_interval = 1.0 / max_rps if max_rps > 0 else 0.0
        self._retries = retries
        self._retry_pause = retry_pause
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None
        self.requests_sent = 0

    def headers(self) -> dict[str, str]:
        """Auth headers: basic-auth + ``X-Api-Token`` on the stand, else Bearer."""
        headers = {"Accept": "application/json"}
        if self._basic_user:
            pair = f"{self._basic_user}:{self._basic_password}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(pair).decode("ascii")
            headers["X-Api-Token"] = self._token
        else:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def __aenter__(self) -> TshinaApiClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def _throttle(self) -> None:
        now = self._clock()
        if self._last_request_at is not None:
            wait = self._min_interval - (now - self._last_request_at)
            if wait > 0:
                await self._sleep(wait)
        self._last_request_at = self._clock()

    async def fetch_page(
        self,
        resource: str,
        *,
        updated_since: datetime | None = None,
        cursor: str | None = None,
        limit: int = 1000,
    ) -> Page:
        if resource not in RESOURCES:
            raise ValueError(f"unknown tshina resource {resource!r}")
        params: dict[str, str] = {"limit": str(max(1, min(limit, MAX_LIMIT)))}
        if updated_since is not None:
            params["updated_since"] = format_since(updated_since)
        if cursor:
            params["cursor"] = cursor
        url = f"{self._base_url}/api/v1/{resource}"

        attempt = 0
        while True:
            await self._throttle()
            self.requests_sent += 1
            try:
                async with self._get_session().get(
                    url, params=params, headers=self.headers(), timeout=self._timeout
                ) as resp:
                    raw = await resp.text()
                    status = resp.status
                    retry_after = resp.headers.get("Retry-After")
            except (aiohttp.ClientError, TimeoutError) as exc:
                if attempt >= self._retries:
                    raise TshinaApiError("network", f"{resource}: {exc!r}") from exc
                attempt += 1
                logger.warning(
                    "tshina %s: network error %r, retry %d/%d",
                    resource,
                    exc,
                    attempt,
                    self._retries,
                )
                await self._sleep(self._retry_pause * attempt)
                continue

            body: Any = None
            try:
                body = json.loads(raw) if raw else None
            except ValueError:
                body = None

            if status == 200:
                return self._page(resource, body)
            if status == 429:
                if attempt >= self._retries:
                    raise TshinaApiError("rate_limit", _error_text(body, raw), status)
                attempt += 1
                wait = _retry_after_seconds(retry_after, self._retry_pause * attempt)
                logger.warning(
                    "tshina %s: 429, waiting %.0fs, retry %d/%d",
                    resource,
                    wait,
                    attempt,
                    self._retries,
                )
                await self._sleep(wait)
                continue
            if status >= 500:
                if attempt >= self._retries:
                    raise TshinaApiError("http", _error_text(body, raw), status)
                attempt += 1
                logger.warning(
                    "tshina %s: HTTP %d, retry %d/%d", resource, status, attempt, self._retries
                )
                await self._sleep(self._retry_pause * attempt)
                continue
            kind = {400: "bad_request", 401: "auth", 403: "auth"}.get(status, "http")
            raise TshinaApiError(kind, _error_text(body, raw), status)

    @staticmethod
    def _page(resource: str, body: Any) -> Page:
        if not isinstance(body, dict) or not isinstance(body.get("items"), list):
            raise TshinaApiError("protocol", f"{resource}: answer has no items list")
        items = body["items"]
        if not all(isinstance(item, dict) for item in items):
            raise TshinaApiError("protocol", f"{resource}: an item is not an object")
        next_cursor = body.get("next_cursor")
        if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor):
            raise TshinaApiError("protocol", f"{resource}: bad next_cursor {next_cursor!r}")
        server_time = body.get("server_time")
        parse_server_time(server_time)
        return Page(items=items, next_cursor=next_cursor, server_time=server_time)

    async def iter_pages(
        self,
        resource: str,
        *,
        updated_since: datetime | None = None,
        limit: int = 1000,
    ) -> AsyncIterator[Page]:
        """Every page of one walk; the first page's ``server_time`` is the next watermark."""
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            page = await self.fetch_page(
                resource, updated_since=updated_since, cursor=cursor, limit=limit
            )
            yield page
            if page.next_cursor is None:
                return
            if page.next_cursor in seen:
                raise TshinaApiError("protocol", f"{resource}: cursor {page.next_cursor!r} repeats")
            seen.add(page.next_cursor)
            cursor = page.next_cursor
