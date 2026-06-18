from __future__ import annotations

import pytest

from experiments.benchmark.family_runner import (
    _annotate_event_demand_achievement,
    _update_event_reference_shed_diagnostics,
)


def test_reference_pbase_minus_actual_is_diagnostic_not_actual_shed() -> None:
    event = {
        "trigger_h": 18.0,
        "end_h": 19.0,
        "demand_baseline_kwh": 8.853,
        "demand_target_shed_kwh": 1.0,
        "actual_kwh": 1.3298,
        "capacity_window_summary": {"recommended_bid_energy_kwh": 0.5453},
    }

    _update_event_reference_shed_diagnostics(event)
    _annotate_event_demand_achievement(event)

    assert event["reference_pbase_minus_actual_kwh"] == pytest.approx(7.5232)
    assert event["capacity_limited_reference_shed_kwh"] == pytest.approx(0.5453)
    assert event["actual_shed_kwh"] is None
    assert event["target_mode"] == "shed_requires_counterfactual"
    assert event["target_achieved"] is None
    assert event["demand_achievement_ratio"] is None
