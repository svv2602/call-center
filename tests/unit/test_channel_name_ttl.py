"""Перевод на оператора доходит до AMI в любой момент звонка, а не только в первые 120 с.

Ключ `call:channel_name:{uuid}` пишет `POST /internal/caller-id` (его дёргает dialplan
Asterisk в начале звонка), а читает исполнитель `transfer_to_operator` — в момент
перевода. С `ex=120` за 60 дней в проде не было ни одного успешного перевода после
2-й минуты: 21 отказ «оператори недоступні» в 13 звонках.

Тест идёт через НАСТОЯЩИЙ `store_caller_id` и НАСТОЯЩИЙ `transfer_to_operator`
(`Harness` из `test_transfer_executor.py`), между ними — фейковый Redis с часами,
который исполняет `ex=`. Поэтому ловится и TTL (значение), и сама запись (проводка).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

import src.main as main_module
from src.config import get_settings
from src.core.call_session import SESSION_TTL
from tests.unit.test_transfer_executor import CHANNEL_NAME, Harness


class ClockedRedis:
    """Минимальный Redis: `set(..., ex=)` / `get` с управляемыми часами.

    Хранит срок жизни каждого ключа и запоминает переданный `ex=`, чтобы тест мог
    проверить и значение TTL, и его последствие — ключ исчезает после истечения.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self._data: dict[str, tuple[bytes, float | None]] = {}
        self.ex: dict[str, int | None] = {}

    async def set(self, key: str, value: str | bytes, ex: int | None = None) -> bool:
        raw = value if isinstance(value, bytes) else str(value).encode()
        self._data[key] = (raw, None if ex is None else self.now + ex)
        self.ex[key] = ex
        return True

    async def get(self, key: str) -> bytes | None:
        item = self._data.get(key)
        if item is None:
            return None
        raw, expires_at = item
        if expires_at is not None and self.now >= expires_at:
            del self._data[key]
            return None
        return raw


async def _register_channel(redis: ClockedRedis, call_uuid: uuid.UUID) -> dict[str, str]:
    """Вызов эндпоинта так, как его зовёт dialplan в начале звонка."""
    request = MagicMock(spec=["headers"])
    request.headers = {"X-Internal-Secret": get_settings().internal_api.secret}
    data = {
        "uuid": str(call_uuid),
        "number": "+380501234567",
        "exten": "1058",
        "channel_id": "1729754321.42",
        "channel_name": CHANNEL_NAME,
    }
    original = main_module._redis
    main_module._redis = redis
    try:
        return await main_module.store_caller_id(request, data)
    finally:
        main_module._redis = original


class TestChannelNameOutlivesTwoMinutes:
    @pytest.mark.asyncio
    async def test_channel_name_ttl_is_the_session_ttl(self) -> None:
        redis = ClockedRedis()
        call_uuid = uuid.uuid4()

        assert await _register_channel(redis, call_uuid) == {"status": "ok"}

        assert redis.ex[f"call:channel_name:{call_uuid}"] == SESSION_TTL
        assert SESSION_TTL > 120

    @pytest.mark.asyncio
    @pytest.mark.parametrize("elapsed", [121, 600, SESSION_TTL - 1])
    async def test_transfer_late_in_the_call_reaches_ami(self, elapsed: int) -> None:
        h = Harness()
        redis = ClockedRedis()
        h.redis = redis  # type: ignore[assignment]

        await _register_channel(redis, h.session.channel_uuid)
        redis.now += elapsed

        result: Any = await h.run_tool_path()

        assert result["status"] == "transferring"
        h.ami.redirect.assert_awaited_once()
        assert h.ami.redirect.await_args.kwargs["channel_name"] == CHANNEL_NAME
