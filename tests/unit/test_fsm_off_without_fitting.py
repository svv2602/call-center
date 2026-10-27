"""The FSM is the fitting machine — off on a network without fitting.

Call f9cca234 (Про Колесо, 2026-10-27, tyre consultation): the FSM ran on
every turn, charged intent parser_null 3/3 and escalated to TRANSFER; only
the classifier's confidence floor kept the caller on the line.
"""

from __future__ import annotations

import pytest

from src.agent.network_policy import NetworkPolicy
from src.core.pipeline import FSM_MODE_LIVE, FSM_MODE_OFF
from tests.unit.test_pipeline_fsm_wire import Harness, fsm_flags

PK = {"sales_enabled": True, "network_policy": {"services": []}}
TSH = {"network_policy": {"services": ["fitting", "storage"]}}


def _mode(policy: NetworkPolicy | None) -> str:
    h = Harness()
    if policy is not None:
        h.streaming_loop._network_policy = policy
    with fsm_flags(enabled=True, shadow_mode=False):
        return h.pipeline._fsm_mode()


@pytest.mark.parametrize("cfg", [PK, {"network_policy": {"services": ["storage"]}}])
def test_network_without_fitting_has_the_fsm_off(cfg: dict) -> None:
    assert _mode(NetworkPolicy.from_tenant_config(cfg)) == FSM_MODE_OFF


def test_fitting_network_keeps_the_fsm() -> None:
    assert _mode(NetworkPolicy.from_tenant_config(TSH)) == FSM_MODE_LIVE


def test_unconfigured_policy_keeps_the_fsm() -> None:
    # No policy written: services unknown, not "none" — fitting-only scope.
    assert _mode(NetworkPolicy.from_tenant_config({})) == FSM_MODE_LIVE


def test_no_policy_at_all_keeps_the_fsm() -> None:
    assert _mode(None) == FSM_MODE_LIVE
