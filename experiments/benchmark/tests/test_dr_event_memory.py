from __future__ import annotations

from energybridge.quantification.dr_event_memory import (
    apply_dr_memory_capacity_estimate,
    build_dr_event_memory,
)


def test_dr_event_memory_estimates_capacity_from_historical_delivery() -> None:
    historical_result = {
        "method": "eb_rule_milp",
        "weather": "Germany",
        "sim_days": 1,
        "start_date": "2025-06-01",
        "vpp_event_log": [
            {
                "id": "hist1",
                "day": 1,
                "trigger_h": 18.0,
                "end_h": 19.0,
                "actual_kwh": 1.0,
                "counterfactual_baseline_kwh": 3.0,
                "counterfactual_actual_shed_kwh": 2.0,
                "capacity_window_summary": {"recommended_bid_energy_kwh": 1.0},
                "score": 4,
            }
        ],
    }
    memory = build_dr_event_memory(
        [
            (
                historical_result,
                {
                    "household_id": "household_s1",
                    "method": "eb_rule_milp",
                    "city": "Germany",
                    "days": 1,
                    "start_date": "2025-06-01",
                },
                "/tmp/hist/benchmark_result.json",
            )
        ],
        methods=["eb_rule_milp"],
    )

    future_result = {
        "method": "eb_rule_milp",
        "weather": "Germany",
        "sim_days": 1,
        "start_date": "2025-07-01",
        "vpp_event_log": [
            {
                "id": "future1",
                "day": 1,
                "trigger_h": 18.0,
                "end_h": 19.0,
                "capacity_window_summary": {"recommended_bid_energy_kwh": 1.5},
            }
        ],
    }
    estimated = apply_dr_memory_capacity_estimate(
        future_result,
        memory,
        metadata={"household_id": "household_s1", "method": "eb_rule_milp", "city": "Germany"},
        top_k=3,
        factor_cap=3.0,
    )

    event_estimate = estimated["vpp_event_log"][0]["historical_dr_memory_capacity_estimate"]
    assert event_estimate["correction_factor"] == 2.0
    assert event_estimate["reported_capacity_kwh"] == 3.0
    assert estimated["historical_dr_memory_reported_capacity_total_kwh"] == 3.0
