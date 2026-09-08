"""Unit tests for fitting tool definitions and schemas."""

from __future__ import annotations

import pytest

from src.agent.tools import ALL_TOOLS, FITTING_TOOLS, MVP_TOOLS, ORDER_TOOLS, PROFILE_TOOLS


class TestFittingToolsList:
    """Test FITTING_TOOLS list structure."""

    def test_fitting_tools_defined(self) -> None:
        tool_names = {t["name"] for t in FITTING_TOOLS}
        assert tool_names == {
            "get_fitting_stations",
            "get_fitting_slots",
            "book_fitting",
            "cancel_fitting",
            "get_fitting_price",
            "get_customer_bookings",
            "reserve_fitting_slot",
            "find_storage",
            "search_knowledge_base",
        }

    def test_all_tools_is_combined(self) -> None:
        assert ALL_TOOLS == MVP_TOOLS + ORDER_TOOLS + FITTING_TOOLS + PROFILE_TOOLS
        assert len(ALL_TOOLS) == 19

    def test_canonical_tool_names(self) -> None:
        """Tool names must match canonical list from 00-overview.md."""
        all_names = {t["name"] for t in ALL_TOOLS}
        expected = {
            # Phase 1
            "get_vehicle_tire_sizes",
            "search_tires",
            "check_availability",
            "transfer_to_operator",
            # Phase 2
            "get_order_status",
            "create_order_draft",
            "update_order_delivery",
            "confirm_order",
            "get_pickup_points",
            # Phase 3
            "get_fitting_stations",
            "get_fitting_slots",
            "book_fitting",
            "cancel_fitting",
            "get_fitting_price",
            "get_customer_bookings",
            "reserve_fitting_slot",
            "find_storage",
            "search_knowledge_base",
            # Profile
            "update_customer_profile",
        }
        assert all_names == expected


class TestGetFittingStationsSchema:
    """Test get_fitting_stations tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in FITTING_TOOLS if t["name"] == "get_fitting_stations")

    def test_city_optional(self, tool: dict) -> None:
        assert "city" not in tool["input_schema"]["required"]

    def test_city_is_string(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert props["city"]["type"] == "string"

    def test_has_description(self, tool: dict) -> None:
        assert "шиномонтаж" in tool["description"].lower()


class TestGetFittingSlotsSchema:
    """Test get_fitting_slots tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in FITTING_TOOLS if t["name"] == "get_fitting_slots")

    def test_station_id_required(self, tool: dict) -> None:
        assert tool["input_schema"]["required"] == ["station_id"]

    def test_has_date_params(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert "date_from" in props
        assert "date_to" in props

    def test_service_type_enum(self, tool: dict) -> None:
        st = tool["input_schema"]["properties"]["service_type"]
        assert set(st["enum"]) == {"tire_change", "balancing", "full_service"}


class TestBookFittingSchema:
    """Test book_fitting tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in FITTING_TOOLS if t["name"] == "book_fitting")

    def test_required_fields(self, tool: dict) -> None:
        required = set(tool["input_schema"]["required"])
        assert required == {"station_id", "date", "time", "customer_name", "customer_phone", "auto_number"}

    def test_has_optional_fields(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert "vehicle_info" in props
        assert "service_type" in props
        assert "tire_diameter" in props
        assert "linked_order_id" in props

    def test_tire_diameter_is_integer(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert props["tire_diameter"]["type"] == "integer"

    def test_linked_order_id_is_string(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert props["linked_order_id"]["type"] == "string"


class TestCancelFittingSchema:
    """Test cancel_fitting tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in FITTING_TOOLS if t["name"] == "cancel_fitting")

    def test_required_fields(self, tool: dict) -> None:
        required = set(tool["input_schema"]["required"])
        assert required == {"booking_id", "action"}

    def test_action_enum(self, tool: dict) -> None:
        action = tool["input_schema"]["properties"]["action"]
        assert set(action["enum"]) == {"cancel", "reschedule"}

    def test_has_new_date_time(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert "new_date" in props
        assert "new_time" in props


class TestGetFittingPriceSchema:
    """Test get_fitting_price tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in FITTING_TOOLS if t["name"] == "get_fitting_price")

    def test_tire_diameter_required(self, tool: dict) -> None:
        assert tool["input_schema"]["required"] == ["tire_diameter"]

    def test_tire_diameter_is_integer(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert props["tire_diameter"]["type"] == "integer"

    def test_has_optional_station_and_service(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert "station_id" in props
        assert "service_type" in props


class TestGetCustomerBookingsSchema:
    """Test get_customer_bookings tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in FITTING_TOOLS if t["name"] == "get_customer_bookings")

    def test_phone_required(self, tool: dict) -> None:
        assert tool["input_schema"]["required"] == ["phone"]

    def test_has_optional_station_id(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert "station_id" in props

    def test_phone_is_string(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert props["phone"]["type"] == "string"

    def test_description_mentions_bookings(self, tool: dict) -> None:
        assert "записи" in tool["description"].lower() or "бронюванн" in tool["description"].lower()


class TestSearchKnowledgeBaseSchema:
    """Test search_knowledge_base tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in FITTING_TOOLS if t["name"] == "search_knowledge_base")

    def test_query_required(self, tool: dict) -> None:
        assert tool["input_schema"]["required"] == ["query"]

    def test_has_category_filter(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert "category" in props
        categories = set(props["category"]["enum"])
        # Must include original 4 + 6 extended categories from categories.py
        assert {"brands", "guides", "faq", "comparisons"}.issubset(categories)
        assert {"policies", "procedures", "returns", "warranty", "delivery", "general"}.issubset(
            categories
        )

    def test_description_mentions_knowledge(self, tool: dict) -> None:
        assert "знань" in tool["description"].lower() or "знан" in tool["description"].lower()


class TestFindStorageSchema:
    """Test find_storage tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in FITTING_TOOLS if t["name"] == "find_storage")

    def test_phone_optional(self, tool: dict) -> None:
        assert "phone" not in tool["input_schema"]["required"]

    def test_storage_number_optional(self, tool: dict) -> None:
        assert "storage_number" not in tool["input_schema"]["required"]

    def test_phone_is_string(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert props["phone"]["type"] == "string"

    def test_storage_number_is_string(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert props["storage_number"]["type"] == "string"

    def test_description_mentions_storage(self, tool: dict) -> None:
        assert "зберігання" in tool["description"].lower()


class TestResolveDateHelper:
    """`resolve_tool_date` — the one calendar the tool layer is allowed to use.

    Was `main.py:_resolve_date` until Wave 6-B, with a byte-identical twin in
    `src/sandbox/agent_runner.py`. The cases below are the originals, so a
    regression in the shared implementation reads as a failure here, plus the
    ones that document where the new implementation is deliberately *wider*.
    """

    def test_empty_returns_empty(self) -> None:
        from src.agent.parsers.date_parser import resolve_tool_date

        assert resolve_tool_date("") == ""

    def test_today_returns_iso_date(self) -> None:
        from datetime import UTC, datetime

        from src.agent.parsers.date_parser import resolve_tool_date

        result = resolve_tool_date("today")
        assert result == datetime.now(tz=UTC).date().isoformat()

    def test_tomorrow_returns_next_day(self) -> None:
        from datetime import UTC, datetime, timedelta

        from src.agent.parsers.date_parser import resolve_tool_date

        result = resolve_tool_date("tomorrow")
        expected = (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()
        assert result == expected

    def test_zavtra_returns_next_day(self) -> None:
        from datetime import UTC, datetime, timedelta

        from src.agent.parsers.date_parser import resolve_tool_date

        result = resolve_tool_date("завтра")
        expected = (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()
        assert result == expected

    def test_iso_date_passthrough(self) -> None:
        from src.agent.parsers.date_parser import resolve_tool_date

        assert resolve_tool_date("2026-02-25") == "2026-02-25"

    def test_strips_whitespace(self) -> None:
        from src.agent.parsers.date_parser import resolve_tool_date

        assert resolve_tool_date("  2026-03-01  ") == "2026-03-01"

    @pytest.mark.parametrize(
        "iso",
        ["2026-03-01", "2026-01-02", "2027-12-31", "2028-02-29", "2026-09-08"],
    )
    def test_an_iso_date_is_never_re_read_as_a_day_month_hint(self, iso: str) -> None:
        """The trap that makes the ISO passthrough load-bearing, not cosmetic.

        `_detect_date_hint` scans for *substrings*, so on `"2026-03-01"` it
        matches the tail `"03-01"` → day 3, month 1 → **2027-01-03**. Routing an
        already-resolved date through the hint detector books a different year
        without any error anywhere. The LLM passes ISO on almost every call, so
        this is the common path, not an edge.
        """
        from datetime import UTC, datetime

        from src.agent.parsers.date_parser import resolve_tool_date

        # A reference date far from every case, so a hint-based resolution
        # could not accidentally land on the right answer.
        now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
        assert resolve_tool_date(iso, now=now) == iso

    def test_a_reference_date_may_be_supplied(self) -> None:
        """`now=` exists so a caller (and a test) is not at the clock's mercy."""
        from datetime import UTC, datetime

        from src.agent.parsers.date_parser import resolve_tool_date

        now = datetime(2026, 3, 5, 10, 0, tzinfo=UTC)  # a Thursday
        assert resolve_tool_date("today", now=now) == "2026-03-05"
        assert resolve_tool_date("tomorrow", now=now) == "2026-03-06"
        assert resolve_tool_date("післязавтра", now=now) == "2026-03-07"

    def test_the_case_bug_in_the_old_aftertomorrow_alias_is_gone(self) -> None:
        """`_resolve_date` had `"afterTomorrow"` inside a lowercase-compared set.

        `low = value.strip().lower()` can never equal `"afterTomorrow"`, so the
        alias was dead code and the value fell through to the passthrough
        branch, handing the SOAP layer the literal string. It resolves now.
        """
        from datetime import UTC, datetime

        from src.agent.parsers.date_parser import resolve_tool_date

        now = datetime(2026, 3, 5, 10, 0, tzinfo=UTC)
        assert resolve_tool_date("afterTomorrow", now=now) == "2026-03-07"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("п'ятниця", "2026-03-06"),  # strictly future — Thursday is «today»
            ("четвер", "2026-03-12"),  # today is Thursday → next week
            ("15 березня", "2026-03-15"),
            ("20.03", "2026-03-20"),
            ("20.03.2026", "2026-03-20"),
        ],
    )
    def test_it_is_wider_than_the_helper_it_replaced(
        self, value: str, expected: str
    ) -> None:
        """Weekdays, «15 березня» and `dd.mm` used to pass through untouched.

        The SOAP layer then rejected them, and the LLM was told the date was
        bad rather than being given the date the client actually named. This is
        a behaviour change and it is deliberate.
        """
        from datetime import UTC, datetime

        from src.agent.parsers.date_parser import resolve_tool_date

        now = datetime(2026, 3, 5, 10, 0, tzinfo=UTC)  # a Thursday
        assert resolve_tool_date(value, now=now) == expected

    @pytest.mark.parametrize("value", ["найближча", "next week", "абракадабра", "01.13"])
    def test_what_it_cannot_resolve_it_hands_back_rather_than_drops(
        self, value: str
    ) -> None:
        """Same contract as the original: never turn a bad date into no date.

        Rejecting belongs to the SOAP layer, which answers with a reason. An
        empty string here would read downstream as «no date was requested».
        """
        from datetime import UTC, datetime

        from src.agent.parsers.date_parser import resolve_tool_date

        now = datetime(2026, 3, 5, 10, 0, tzinfo=UTC)
        assert resolve_tool_date(value, now=now) == value

    def test_there_is_exactly_one_implementation_left(self) -> None:
        """`main.py` and `sandbox/agent_runner.py` had a copy each.

        Two calendars in a repo drift — the sandbox copy had already lost the
        `"сегодня"` alias the live one carried. Scanned from source rather than
        by import, because both modules pull in the whole FastAPI/SQLAlchemy
        stack and this assertion must hold in any environment.
        """
        import ast
        import pathlib

        import src.agent.parsers.date_parser as dp

        src_root = pathlib.Path(dp.__file__).parents[3]
        definitions = []
        for path in sorted(src_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            definitions += [
                f"{path}:{node.name}"
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name in {"_resolve_date", "resolve_tool_date"}
            ]
        assert definitions == [f"{pathlib.Path(dp.__file__)}:resolve_tool_date"]
