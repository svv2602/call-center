"""Known-customer greeting: the name question is found despite stress marks,
and the fitting menu is spoken only on a fitting-scoped call."""

from src.agent.network_policy import NetworkPolicy
from src.main import _known_customer_greeting

PK_GREETING = (
    "{time_greeting}\\! Мене́ зва́ти {agent_name}. Я віртуа́льний помічни́к "
    "інтерне́т-магази́ну Про Колесо. Як мо́жу до вас зверта́тися?"
)
TSH_GREETING = (
    "{time_greeting}! Це {agent_name}, {network_name}, дзвінок автоматичний. "
    "Ви бажаєте записатися на шиномонтаж, дізнатися вартість, скасувати чи перенести запис?"
)

PK_POLICY = NetworkPolicy.from_tenant_config({"network_policy": {"services": []}})
TSH_POLICY = NetworkPolicy.from_tenant_config({"network_policy": {"services": ["fitting"]}})
TSH_SALES = NetworkPolicy.from_tenant_config(
    {"sales_enabled": True, "network_policy": {"services": ["fitting"]}}
)


def _greet(greeting: str, policy: NetworkPolicy, network: str | None = "Мережа") -> str:
    return _known_customer_greeting(
        greeting, "Валерій", agent_name="Олена", network_name=network, network_policy=policy
    )


def test_stressed_name_question_gets_the_name() -> None:
    out = _greet(PK_GREETING, PK_POLICY, "Про Колесо")
    assert out.endswith("Про Колесо. Валерій, чим можу допомогти?")
    assert "Мене́ зва́ти" in out  # the rest of the tenant text is kept as is
    assert "шиномонтаж" not in out


def test_plain_name_question_gets_the_name() -> None:
    out = _greet("Добрий день! Як можу до вас звертатися?", PK_POLICY)
    assert out == "Добрий день! Валерій, чим можу допомогти?"


def test_fitting_network_without_sales_keeps_the_fitting_menu() -> None:
    out = _greet(TSH_GREETING, TSH_POLICY, "Твоя Шина")
    assert out.startswith("{time_greeting}, Валерій! Це Олена, Твоя Шина")
    assert "записатися на шиномонтаж" in out


def test_sales_on_gets_the_neutral_question() -> None:
    out = _greet(TSH_GREETING, TSH_SALES, "Твоя Шина")
    assert out.endswith("дзвінок автоматичний. Чим можу допомогти?")
    assert "шиномонтаж" not in out


def test_network_without_fitting_never_offers_fitting() -> None:
    out = _greet("Вітаємо!", PK_POLICY, "Про Колесо")
    assert "шиномонтаж" not in out
    assert out.endswith("Чим можу допомогти?")


def test_unconfigured_policy_keeps_the_old_fitting_menu() -> None:
    out = _greet("Вітаємо!", NetworkPolicy(), None)
    assert "записатися на шиномонтаж" in out
    assert "{network_name}" not in out and "Олена, дзвінок" in out


def test_name_with_regex_escapes_is_inserted_verbatim() -> None:
    out = _known_customer_greeting(
        PK_GREETING, r"Ві\1", agent_name=None, network_name=None, network_policy=PK_POLICY
    )
    assert r"Ві\1, чим можу допомогти?" in out
