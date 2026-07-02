from __future__ import annotations

import pytest

from energybridge.quantification.counterfactual_baseline import (
    apply_counterfactual_baseline,
    build_counterfactual_library,
    find_matching_baseline,
)


def test_counterfactual_baseline_keeps_negative_delivery_visible() -> None:
    no_dr = {
        "method": "no_dr",
        "sim_days": 2,
        "start_date": "2025-06-01",
        "weather": "Tianjin",
        "vpp_event_log": [
            {"id": "vpp1", "day": 1, "trigger_h": 18.0, "end_h": 19.0, "actual_kwh": 2.0},
            {"id": "vpp2", "day": 2, "trigger_h": 42.0, "end_h": 43.0, "actual_kwh": 3.0},
        ],
    }
    library = build_counterfactual_library(
        [
            (
                no_dr,
                {
                    "persona_id": "p1",
                    "city": "Tianjin",
                    "days": 2,
                    "start_date": "2025-06-01",
                    "method": "no_dr",
                },
                "/tmp/p1_no_dr/benchmark_result.json",
            )
        ]
    )
    method_result = {
        "method": "agent",
        "sim_days": 2,
        "start_date": "2025-06-01",
        "weather": "Tianjin",
        "vpp_event_log": [
            {
                "id": "vpp1",
                "day": 1,
                "trigger_h": 18.0,
                "end_h": 19.0,
                "actual_kwh": 1.0,
                "demand_target_shed_kwh": 2.0,
                "capacity_window_summary": {"recommended_bid_energy_kwh": 0.5},
            },
            {
                "id": "vpp2",
                "day": 2,
                "trigger_h": 42.0,
                "end_h": 43.0,
                "actual_kwh": 4.0,
                "demand_target_shed_kwh": 2.0,
                "capacity_window_summary": {"recommended_bid_energy_kwh": 0.5},
            },
        ],
    }

    baseline = find_matching_baseline(
        library,
        method_result,
        metadata={
            "persona_id": "p1",
            "city": "Tianjin",
            "days": 2,
            "start_date": "2025-06-01",
        },
    )
    assert baseline is not None

    applied = apply_counterfactual_baseline(
        method_result,
        baseline,
        metadata={"persona_id": "p1", "city": "Tianjin", "days": 2},
    )

    events = applied["vpp_event_log"]
    assert events[0]["actual_shed_kwh"] == pytest.approx(1.0)
    assert events[1]["actual_shed_kwh"] == pytest.approx(-1.0)
    assert events[1]["counterfactual_actual_shed_nonnegative_kwh"] == pytest.approx(0.0)
    assert applied["counterfactual_baseline_vpp_window_kwh"] == pytest.approx(5.0)
    assert applied["counterfactual_actual_shed_total_kwh"] == pytest.approx(0.0)
    assert applied["counterfactual_capacity_upper_bound_total_kwh"] == pytest.approx(5.0)
    assert applied["counterfactual_delivery_ratio_vs_baseline_upper_bound_total"] == pytest.approx(0.0)
    assert applied["vpp_energy_reduction_basis"] == "no_dr_counterfactual_baseline"
    assert applied["counterfactual_delivery_ratio_vs_target_avg"] == pytest.approx(0.0)
    assert applied["counterfactual_delivery_ratio_vs_recommended_bid_avg"] == pytest.approx(0.0)
