"""Nova Poshta quote client: buckets, request shape, failures → None, cache, no key leaks.

The API is faked at the aiohttp-session boundary (``aioresponses`` 0.7.x does
not work with the installed aiohttp 3.14 — ``ClientResponse`` grew a required
``stream_writer``): ``FakeSession.post`` hands each request body to ``FakeNP``,
which routes by ``calledMethod`` and records it. ``_post`` — status check,
JSON parsing, timeout — runs for real. Answers follow the facts checked
read-only against the live API on 2026-10-27 (see the run README).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import aiohttp
import pytest
from aiobreaker import CircuitBreaker

from src.config import NovaPoshtaSettings
from src.integrations.novaposhta import CargoLine, NovaPoshtaClient, Quote, cargo_bucket

URL = "https://np.test/v2.0/json/"
KEY = "np-secret-key-7f3a9c"
DNIPRO_REF = "db5c88f0-391c-11dd-90d9-001a92567626"
KYIV_REF = "8d5a980d-391c-11dd-90d9-001a92567626"

# getTiresWheelsList as Nova Poshta returns it: trailing spaces, comma decimals.
TIRES_WHEELS = [
    {"Ref": "ref-t-13-14", "Description": "Шина легкова R 13-14 ", "DescriptionType": "Tires"},
    {"Ref": "ref-t-15-17", "Description": "Шина легкова R 15-17 ", "DescriptionType": "Tires"},
    {"Ref": "ref-t-18-19", "Description": "Шина легкова R 18-19 ", "DescriptionType": "Tires"},
    {"Ref": "ref-t-20-21", "Description": "Шина легкова R 20-21", "DescriptionType": "Tires"},
    {"Ref": "ref-t-22-23", "Description": "Шина легкова R 22-23 ", "DescriptionType": "Tires"},
    {"Ref": "ref-tt-17.5", "Description": "Шина вантажна R 17,5 ", "DescriptionType": "Tires"},
    {"Ref": "ref-tt-19.5", "Description": "Шина вантажна R 19,5", "DescriptionType": "Tires"},
    {"Ref": "ref-tt-20", "Description": "Шина вантажна R 20 ", "DescriptionType": "Tires"},
    {"Ref": "ref-tt-22.5", "Description": "Шина вантажна R 22,5 ", "DescriptionType": "Tires"},
    {"Ref": "ref-w-15-17", "Description": "Диск легковий R 15-17 ", "DescriptionType": "Wheels"},
    {"Ref": "ref-w-18-19", "Description": "Диск легковий R 18-19 ", "DescriptionType": "Wheels"},
    {"Ref": "ref-wt-22.5", "Description": "Диск вантажний R 22,5 ", "DescriptionType": "Wheels"},
]


def _settings(**overrides: Any) -> NovaPoshtaSettings:
    base: dict[str, Any] = {"api_url": URL, "timeout_sec": 4.0, "sender_city": "Дніпро"}
    base.update(overrides)
    return NovaPoshtaSettings(**base)


def _ok(data: list[dict[str, Any]]) -> dict[str, Any]:
    return {"success": True, "data": data, "errors": [], "warnings": []}


@dataclass
class Answer:
    """One HTTP answer: ``payload`` as JSON, or raw ``text``, with ``status``."""

    status: int = 200
    payload: Any = None
    text: str = ""


class _FakeResponse:
    def __init__(self, answer: Answer) -> None:
        self.status = answer.status
        self._answer = answer

    async def json(self, content_type: str | None = "application/json") -> Any:
        if self._answer.payload is not None:
            return self._answer.payload
        return json.loads(self._answer.text)


class _FakeRequest:
    def __init__(self, produce: Any) -> None:
        self._produce = produce

    async def __aenter__(self) -> _FakeResponse:
        return _FakeResponse(self._produce())

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeSession:
    """Stands in for ``aiohttp.ClientSession``: only ``post`` and ``close``."""

    def __init__(self, server: FakeNP) -> None:
        self._server = server
        self.closed = False

    def post(self, url: str, *, json: dict[str, Any], timeout: Any) -> _FakeRequest:
        assert url == URL
        assert timeout.total == 4.0
        return _FakeRequest(lambda: self._server(url, json=json))

    async def close(self) -> None:
        self.closed = True


class FakeNP:
    """Routes by ``calledMethod``; ``overrides[method]`` replaces the answer."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.overrides: dict[str, Any] = {}
        self.door_cost = 870
        self.door_answer: Answer | None = None

    def __call__(self, url: Any, **kwargs: Any) -> Answer:
        body = kwargs["json"]
        self.calls.append(body)
        method = body["calledMethod"]
        if method in self.overrides:
            override = self.overrides[method]
            if isinstance(override, BaseException):
                raise override
            return override
        if method == "searchSettlements":
            ref = DNIPRO_REF if body["methodProperties"]["CityName"] == "Дніпро" else KYIV_REF
            return Answer(payload=_ok([{"TotalCount": 1, "Addresses": [{"DeliveryCity": ref}]}]))
        if method == "getTiresWheelsList":
            return Answer(payload=_ok(TIRES_WHEELS))
        if method == "getDocumentPrice":
            props = body["methodProperties"]
            if props["ServiceType"] == "WarehouseDoors":
                if self.door_answer is not None:
                    return self.door_answer
                return Answer(payload=_ok([{"Cost": self.door_cost}]))
            return Answer(payload=_ok([{"Cost": 810, "CostRedelivery": 260}]))
        if method == "getWarehouses":
            number = body["methodProperties"]["WarehouseId"]
            data = [{"Number": number, "Description": "Відділення"}] if number == "12" else []
            return Answer(payload=_ok(data))
        raise AssertionError(f"unexpected method {method}")

    def methods(self) -> list[str]:
        return [c["calledMethod"] for c in self.calls]

    def price_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["calledMethod"] == "getDocumentPrice"]


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value


def _client(server: FakeNP, **kwargs: Any) -> NovaPoshtaClient:
    kwargs.setdefault("breaker", CircuitBreaker(fail_max=5, timeout_duration=timedelta(seconds=30)))
    return NovaPoshtaClient(KEY, _settings(), http=FakeSession(server), **kwargs)


@pytest.fixture
def np_api() -> FakeNP:
    return FakeNP()


@pytest.fixture
async def client(np_api: FakeNP):
    c = _client(np_api)
    yield c
    await c.close()


FOUR_R16 = [CargoLine("tire", 16, 4)]


# ---------------------------------------------------------------------------
# cargo_bucket
# ---------------------------------------------------------------------------


class TestCargoBucket:
    @pytest.mark.parametrize(
        ("diameter", "expected"),
        [
            (13, "R 13-14"),
            (14, "R 13-14"),
            (15, "R 15-17"),
            (16, "R 15-17"),
            (17, "R 15-17"),
            (18, "R 18-19"),
            (19, "R 18-19"),
            (20, "R 20-21"),
            (21, "R 20-21"),
            (22, "R 22-23"),
            (23, "R 22-23"),
            ("R17", "R 15-17"),
            ("16", "R 15-17"),
            (17.0, "R 15-17"),
        ],
    )
    def test_passenger_buckets(self, diameter: Any, expected: str) -> None:
        assert cargo_bucket(diameter, "tire") == expected
        assert cargo_bucket(diameter, "wheel") == expected

    @pytest.mark.parametrize("diameter", [12, 24, 17.5, "abc", "", True])
    def test_passenger_out_of_range_is_none(self, diameter: Any) -> None:
        assert cargo_bucket(diameter, "tire") is None

    @pytest.mark.parametrize(
        ("diameter", "expected"),
        [("17,5", "R 17.5"), (17.5, "R 17.5"), ("19,5", "R 19.5"), (20, "R 20"), (22.5, "R 22.5")],
    )
    def test_truck_sizes(self, diameter: Any, expected: str) -> None:
        assert cargo_bucket(diameter, "tire", truck=True) == expected

    @pytest.mark.parametrize("diameter", [16, 18, 21, 24.5])
    def test_truck_out_of_list_is_none(self, diameter: Any) -> None:
        assert cargo_bucket(diameter, "tire", truck=True) is None

    def test_unknown_kind_is_none(self) -> None:
        assert cargo_bucket(16, "battery") is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# cargo_refs / resolve_city
# ---------------------------------------------------------------------------


class TestReferences:
    async def test_cargo_refs_strip_descriptions_and_comma_decimals(self, np_api, client) -> None:
        refs = await client.cargo_refs()
        assert refs is not None
        assert refs[("tire", False, "R 15-17")] == "ref-t-15-17"
        assert refs[("tire", False, "R 20-21")] == "ref-t-20-21"
        assert refs[("tire", True, "R 17.5")] == "ref-tt-17.5"
        assert refs[("tire", True, "R 20")] == "ref-tt-20"
        assert refs[("wheel", False, "R 18-19")] == "ref-w-18-19"
        assert refs[("wheel", True, "R 22.5")] == "ref-wt-22.5"
        # every bucket cargo_bucket can produce for these rows resolves
        for kind, truck, d in [("tire", False, 13), ("tire", True, "19,5"), ("wheel", False, 16)]:
            assert (kind, truck, cargo_bucket(d, kind, truck)) in refs

    async def test_cargo_refs_cached(self, np_api, client) -> None:
        await client.cargo_refs()
        await client.cargo_refs()
        assert np_api.methods().count("getTiresWheelsList") == 1

    async def test_resolve_city_takes_delivery_city(self, np_api, client) -> None:
        assert await client.resolve_city("Київ") == KYIV_REF
        assert await client.resolve_city("  київ ") == KYIV_REF
        assert np_api.methods().count("searchSettlements") == 1

    async def test_resolve_city_not_found(self, np_api, client) -> None:
        np_api.overrides["searchSettlements"] = Answer(
            payload=_ok([{"TotalCount": 0, "Addresses": []}])
        )
        assert await client.resolve_city("Нідекуди") is None

    async def test_resolve_city_blank_sends_nothing(self, np_api, client) -> None:
        assert await client.resolve_city("  ") is None
        assert np_api.calls == []


# ---------------------------------------------------------------------------
# quote
# ---------------------------------------------------------------------------


class TestQuote:
    async def test_request_shape(self, np_api, client) -> None:
        quote = await client.quote(KYIV_REF, FOUR_R16, declared_value=12000)
        assert quote == Quote(cost=810.0, door_cost=None, cod_fee=260.0)
        [call] = np_api.price_calls()
        assert call["apiKey"] == KEY
        assert call["modelName"] == "InternetDocument"
        props = call["methodProperties"]
        assert props["CitySender"] == DNIPRO_REF
        assert props["CityRecipient"] == KYIV_REF
        assert props["ServiceType"] == "WarehouseWarehouse"
        assert props["CargoType"] == "TiresWheels"
        assert props["Cost"] == "12000"
        assert props["CargoDetails"] == [{"CargoDescription": "ref-t-15-17", "Amount": "4"}]
        assert props["RedeliveryCalculate"] == {"CargoType": "Money", "Amount": "12000"}

    async def test_sender_city_comes_from_settings(self, np_api) -> None:
        c = _client(np_api)
        try:
            await c.quote(KYIV_REF, FOUR_R16, declared_value=5000)
        finally:
            await c.close()
        names = [
            x["methodProperties"]["CityName"]
            for x in np_api.calls
            if x["calledMethod"] == "searchSettlements"
        ]
        assert names == ["Дніпро"]

    async def test_mixed_diameters_give_several_cargo_details(self, np_api, client) -> None:
        lines = [("tire", 16, 2), ("tire", 17, 2), ("tire", 19, 4), ("wheel", 18, 4)]
        await client.quote(KYIV_REF, lines, declared_value=40000)
        [call] = np_api.price_calls()
        assert call["methodProperties"]["CargoDetails"] == [
            {"CargoDescription": "ref-t-15-17", "Amount": "4"},
            {"CargoDescription": "ref-t-18-19", "Amount": "4"},
            {"CargoDescription": "ref-w-18-19", "Amount": "4"},
        ]

    async def test_door_sends_second_request(self, np_api, client) -> None:
        quote = await client.quote(KYIV_REF, FOUR_R16, declared_value=12000, door=True)
        assert quote == Quote(cost=810.0, door_cost=870.0, cod_fee=260.0)
        assert quote.door_surcharge == 60.0
        calls = np_api.price_calls()
        assert [c["methodProperties"]["ServiceType"] for c in calls] == [
            "WarehouseWarehouse",
            "WarehouseDoors",
        ]
        assert calls[1]["methodProperties"]["Cost"] == "12000"

    async def test_no_door_no_second_request(self, np_api, client) -> None:
        quote = await client.quote(KYIV_REF, FOUR_R16, declared_value=12000)
        assert quote is not None and quote.door_surcharge is None
        assert len(np_api.price_calls()) == 1

    async def test_unsupported_size_is_none_without_price_call(self, np_api, client) -> None:
        assert await client.quote(KYIV_REF, [("tire", 24, 4)], declared_value=9000) is None
        assert np_api.price_calls() == []

    @pytest.mark.parametrize("value", [0, -5])
    async def test_non_positive_value_is_none(self, np_api, client, value: int) -> None:
        assert await client.quote(KYIV_REF, FOUR_R16, declared_value=value) is None
        assert np_api.calls == []

    async def test_second_identical_quote_is_cached(self, np_api, client) -> None:
        first = await client.quote(KYIV_REF, FOUR_R16, declared_value=12000)
        calls_after_first = len(np_api.calls)
        second = await client.quote(KYIV_REF, FOUR_R16, declared_value=12000)
        assert second == first
        assert len(np_api.calls) == calls_after_first

    async def test_declared_value_is_part_of_cache_key(self, np_api, client) -> None:
        await client.quote(KYIV_REF, FOUR_R16, declared_value=12000)
        np_api.overrides["getDocumentPrice"] = Answer(
            payload=_ok([{"Cost": 1150, "CostRedelivery": 540}])
        )
        quote = await client.quote(KYIV_REF, FOUR_R16, declared_value=26000)
        assert quote == Quote(cost=1150.0, door_cost=None, cod_fee=540.0)
        assert len(np_api.price_calls()) == 2

    async def test_door_flag_is_part_of_cache_key(self, np_api, client) -> None:
        await client.quote(KYIV_REF, FOUR_R16, declared_value=12000)
        quote = await client.quote(KYIV_REF, FOUR_R16, declared_value=12000, door=True)
        assert quote is not None and quote.door_cost == 870.0

    async def test_redis_cache_shared_between_instances(self, np_api) -> None:
        redis = FakeRedis()
        a = _client(np_api, redis=redis)
        b = _client(np_api, redis=redis)
        try:
            first = await a.quote(KYIV_REF, FOUR_R16, declared_value=12000)
            n = len(np_api.calls)
            assert await b.quote(KYIV_REF, FOUR_R16, declared_value=12000) == first
            assert len(np_api.calls) == n
        finally:
            await a.close()
            await b.close()
        assert all(k.startswith("np:") for k in redis.store)
        assert all(KEY not in v for v in redis.store.values())

    async def test_cache_expires(self, np_api) -> None:
        now = [1000.0]
        c = _client(np_api, clock=lambda: now[0])
        try:
            await c.quote(KYIV_REF, FOUR_R16, declared_value=12000)
            now[0] += 6 * 3600 + 1
            await c.quote(KYIV_REF, FOUR_R16, declared_value=12000)
        finally:
            await c.close()
        assert len(np_api.price_calls()) == 2


# ---------------------------------------------------------------------------
# failures → None (R16), key never logged (R15)
# ---------------------------------------------------------------------------


class TestFailures:
    @pytest.mark.parametrize(
        "answer",
        [
            TimeoutError(),
            aiohttp.ClientConnectionError("connection reset"),
            Answer(status=500),
            Answer(payload={"success": False, "data": [], "errors": [f"API key {KEY} expired"]}),
            Answer(payload=_ok([])),
            Answer(payload=_ok([{"Cost": 810}])),
            Answer(text="not json"),
        ],
        ids=[
            "timeout",
            "conn_error",
            "http500",
            "success_false",
            "empty_data",
            "no_cod_fee",
            "not_json",
        ],
    )
    async def test_price_failure_is_none_and_key_not_logged(
        self, np_api, client, caplog, answer: Any
    ) -> None:
        np_api.overrides["getDocumentPrice"] = answer
        with caplog.at_level(logging.DEBUG):
            result = await client.quote(KYIV_REF, FOUR_R16, declared_value=12000)
        assert result is None
        assert any(r.levelno == logging.WARNING for r in caplog.records)
        assert KEY not in caplog.text
        for record in caplog.records:
            assert KEY not in record.getMessage()

    async def test_failure_is_not_cached(self, np_api, client) -> None:
        np_api.overrides["getDocumentPrice"] = Answer(status=502)
        assert await client.quote(KYIV_REF, FOUR_R16, declared_value=12000) is None
        del np_api.overrides["getDocumentPrice"]
        assert await client.quote(KYIV_REF, FOUR_R16, declared_value=12000) is not None

    async def test_door_failure_is_none(self, np_api, client) -> None:
        np_api.door_answer = Answer(status=503)
        assert await client.quote(KYIV_REF, FOUR_R16, declared_value=12000, door=True) is None
        assert len(np_api.price_calls()) == 2

    async def test_reference_failures_are_none(self, np_api, client) -> None:
        np_api.overrides["getTiresWheelsList"] = TimeoutError()
        assert await client.quote(KYIV_REF, FOUR_R16, declared_value=12000) is None
        del np_api.overrides["getTiresWheelsList"]
        np_api.overrides["searchSettlements"] = Answer(status=500)
        assert await client.resolve_city("Київ") is None
        assert await client.quote(KYIV_REF, FOUR_R16, declared_value=12000) is None

    async def test_breaker_open_is_none_without_request(self, np_api, caplog) -> None:
        breaker = CircuitBreaker(fail_max=2, timeout_duration=timedelta(seconds=30))
        c = _client(np_api, breaker=breaker)
        np_api.overrides["searchSettlements"] = Answer(status=500)
        try:
            assert await c.resolve_city("Київ") is None
            assert await c.resolve_city("Львів") is None
            sent = len(np_api.calls)
            with caplog.at_level(logging.WARNING):
                assert await c.resolve_city("Одеса") is None
            assert len(np_api.calls) == sent
        finally:
            await c.close()
        assert "breaker open" in caplog.text
        assert KEY not in caplog.text

    async def test_success_false_does_not_trip_breaker(self, np_api) -> None:
        breaker = CircuitBreaker(fail_max=1, timeout_duration=timedelta(seconds=30))
        c = _client(np_api, breaker=breaker)
        np_api.overrides["searchSettlements"] = Answer(
            payload={"success": False, "data": [], "errors": ["bad"]}
        )
        try:
            assert await c.resolve_city("Київ") is None
            del np_api.overrides["searchSettlements"]
            assert await c.resolve_city("Київ") == KYIV_REF
        finally:
            await c.close()


class TestKeyHygiene:
    def test_key_not_in_repr(self) -> None:
        c = NovaPoshtaClient(KEY, _settings())
        assert KEY not in repr(c)
        assert KEY not in str(c)

    @pytest.mark.parametrize("key", ["", "   "])
    def test_empty_key_rejected_without_echo(self, key: str) -> None:
        with pytest.raises(ValueError, match="needs an API key"):
            NovaPoshtaClient(key, _settings())

    def test_settings_have_no_key_field(self) -> None:
        fields = set(NovaPoshtaSettings.model_fields)
        assert not any("key" in name for name in fields)
        s = NovaPoshtaSettings()
        assert s.sender_city == "Дніпро"
        assert s.timeout_sec == 4.0
        assert s.api_url == "https://api.novaposhta.ua/v2.0/json/"


# ---------------------------------------------------------------------------
# find_warehouse
# ---------------------------------------------------------------------------


class TestFindWarehouse:
    async def test_found(self, np_api, client) -> None:
        assert await client.find_warehouse(KYIV_REF, "12") == "found"
        assert await client.find_warehouse(KYIV_REF, "№ 12") == "found"
        props = np_api.calls[0]["methodProperties"]
        assert props == {"CityRef": KYIV_REF, "WarehouseId": "12"}

    async def test_not_found(self, np_api, client) -> None:
        assert await client.find_warehouse(KYIV_REF, "999") == "not_found"

    async def test_other_numbers_in_answer_are_not_a_match(self, np_api, client) -> None:
        np_api.overrides["getWarehouses"] = Answer(
            payload=_ok([{"Number": "1"}, {"Number": "120"}])
        )
        assert await client.find_warehouse(KYIV_REF, "12") == "not_found"

    async def test_failure_is_none(self, np_api, client) -> None:
        np_api.overrides["getWarehouses"] = TimeoutError()
        assert await client.find_warehouse(KYIV_REF, "12") is None

    async def test_refusal_is_none_not_not_found(self, np_api, client) -> None:
        # success=false with an empty data list is an API refusal, not «no such branch»
        np_api.overrides["getWarehouses"] = Answer(
            payload={"success": False, "data": [], "errors": ["CityRef is invalid"]}
        )
        assert await client.find_warehouse(KYIV_REF, "12") is None

    async def test_blank_number_sends_nothing(self, np_api, client) -> None:
        assert await client.find_warehouse(KYIV_REF, " ") is None
        assert np_api.calls == []
