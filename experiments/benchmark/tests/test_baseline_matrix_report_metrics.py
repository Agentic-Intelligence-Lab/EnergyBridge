from __future__ import annotations

import pytest

from experiments.benchmark.generate_baseline_matrix_report import (
    _observed_appliance_action_services,
    _vpp_window_energy_per_hour,
)


def test_vpp_window_energy_uses_average_per_vpp_hour_not_sum() -> None:
    result = {
        "vpp_window_energy_kwh": 3.0,
        "vpp_event_log": [
            {"actual_kwh": 1.0, "trigger_h": 18.0, "end_h": 19.0},
            {"actual_kwh": 2.0, "trigger_h": 12.0, "end_h": 12.5},
        ],
    }

    assert _vpp_window_energy_per_hour(result, {}) == pytest.approx(3.0 / 1.5)


def test_vpp_window_energy_ignores_legacy_reference_shed() -> None:
    result = {
        "vpp_window_energy_kwh": 1.2,
        "vpp_event_log": [
            {
                "actual_shed_kwh": 8.0,
                "reference_pbase_minus_actual_kwh": 8.0,
                "trigger_h": 18.0,
                "end_h": 19.0,
                "capacity_window_summary": {"recommended_bid_energy_kwh": 0.55},
            }
        ],
    }

    assert _vpp_window_energy_per_hour(result, {}) == pytest.approx(1.2)


def test_vpp_window_energy_prefers_explicit_average_field() -> None:
    result = {
        "vpp_window_energy_avg_per_hour_kwh": 0.42,
        "vpp_window_energy_kwh": 5.0,
        "vpp_event_log": [{"actual_kwh": 5.0, "trigger_h": 18.0, "end_h": 19.0}],
    }

    assert _vpp_window_energy_per_hour(result, {}) == 0.42


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
