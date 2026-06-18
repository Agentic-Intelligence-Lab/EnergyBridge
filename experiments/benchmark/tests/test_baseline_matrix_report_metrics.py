from __future__ import annotations

import pytest

from experiments.benchmark.generate_baseline_matrix_report import (
    _observed_appliance_action_services,
    _vpp_reduction_kwh,
)


def test_vpp_reduction_uses_average_per_vpp_hour_not_sum() -> None:
    result = {
        "vpp_energy_reduction_kwh": 0.0,  # legacy default should not mask event data
        "vpp_event_log": [
            {"actual_shed_kwh": 1.0, "trigger_h": 18.0, "end_h": 19.0},
            {"actual_shed_kwh": 1.0, "trigger_h": 12.0, "end_h": 12.5},
        ],
    }

    assert _vpp_reduction_kwh(result, {}) == pytest.approx(2.0 / 1.5)


def test_vpp_reduction_caps_legacy_reference_shed_with_physical_capacity() -> None:
    result = {
        "vpp_event_log": [
            {
                "actual_shed_kwh": 8.0,
                "trigger_h": 18.0,
                "end_h": 19.0,
                "capacity_window_summary": {"recommended_bid_energy_kwh": 0.55},
            }
        ],
    }

    assert _vpp_reduction_kwh(result, {}) == pytest.approx(0.55)


def test_vpp_reduction_prefers_explicit_average_field() -> None:
    result = {
        "vpp_energy_reduction_avg_per_hour_kwh": 0.42,
        "vpp_event_log": [{"actual_shed_kwh": 5.0, "trigger_h": 18.0, "end_h": 19.0}],
    }

    assert _vpp_reduction_kwh(result, {}) == 0.42


def test_observed_services_require_ev_charge_window_not_mode_only() -> None:
    result = {
        "vpp_event_log": [
            {
                "vpp_trigger_actions": {"ev_mode": "smart"},
                "day_decisions": [
                    {"actions": {"ev_charge_start_h": 21.0, "ev_charge_end_h": 7.0}},
                ],
            }
        ]
    }

    assert _observed_appliance_action_services(result) == {"ev"}
    result["vpp_event_log"][0]["day_decisions"] = []
    assert _observed_appliance_action_services(result) == set()
