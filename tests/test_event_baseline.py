from __future__ import annotations

from energybridge.quantification import (
    EventBaselineConfig,
    estimate_event_baseline_and_shed,
    estimate_vpp_event_baselines,
)


def _rows(start_h: float, end_h: float, power_kw: float, *, outdoor_c: float = 30.0):
    rows = []
    h = start_h
    while h < end_h - 1e-9:
        rows.append(
            {
                "sim_h": round(h, 6),
                "dt_h": 1.0 / 6.0,
                "power_kw": power_kw,
                "outdoor_temperature_c": outdoor_c,
            }
        )
        h += 1.0 / 6.0
    return rows


def test_pre_event_window_baseline_estimates_shed_when_history_absent():
    event = {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0}
    actual_rows = (
        _rows(17.0, 18.0, 5.0, outdoor_c=30.0)
        + _rows(18.0, 19.0, 3.0, outdoor_c=30.0)
    )

    result = estimate_event_baseline_and_shed(event, actual_rows)

    assert result["status"] == "computed"
    assert result["baseline_source"] == "pre_event_short_window"
    assert result["baseline_kwh"] == 5.0
    assert result["actual_kwh"] == 3.0
    assert result["actual_shed_kwh"] == 2.0
    assert result["actual_shed_avg_kw"] == 2.0


def test_historical_same_time_baseline_is_preferred_over_pre_event_fallback():
    event = {"id": "vpp1", "trigger_h": 66.0, "end_h": 67.0}
    actual_rows = (
        _rows(65.0, 66.0, 7.0, outdoor_c=30.0)
        + _rows(66.0, 67.0, 3.0, outdoor_c=30.0)
    )
    history = []
    for day in range(3):
        history.extend(_rows(day * 24.0 + 18.0, day * 24.0 + 19.0, 4.0, outdoor_c=30.0))

    result = estimate_event_baseline_and_shed(event, actual_rows, historical_rows=history)

    assert result["status"] == "computed"
    assert result["baseline_source"] == "historical_same_time_weather_adjusted"
    assert result["baseline_kwh"] == 4.0
    assert result["actual_shed_kwh"] == 1.0
    assert result["selected_baseline"]["n_history_days"] == 3


def test_weather_adjusted_history_clamps_hot_weather_multiplier():
    event = {"id": "vpp1", "trigger_h": 42.0, "end_h": 43.0}
    actual_rows = _rows(42.0, 43.0, 3.0, outdoor_c=40.0)
    history = []
    for day in range(3):
        history.extend(_rows(day * 24.0 + 18.0, day * 24.0 + 19.0, 4.0, outdoor_c=25.0))

    result = estimate_event_baseline_and_shed(event, actual_rows, historical_rows=history)

    assert result["baseline_source"] == "historical_same_time_weather_adjusted"
    assert result["selected_baseline"]["weather_adjustment_factor"] == 1.2
    assert result["baseline_kwh"] == 4.8
    assert result["actual_shed_kwh"] == 1.8


def test_multi_event_wrapper_uses_measured_actual_energy_by_event():
    events = [
        {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0},
        {"id": "vpp2", "trigger_h": 42.0, "end_h": 43.0},
    ]
    rows = _rows(17.0, 18.0, 5.0) + _rows(18.0, 19.0, 3.0)

    result = estimate_vpp_event_baselines(
        events,
        rows,
        actual_kwh_by_event={"vpp1": 2.5},
        config=EventBaselineConfig(required_history_days=2),
    )

    assert result["vpp1"]["actual_energy_source"] == "measured_event_energy"
    assert result["vpp1"]["actual_kwh"] == 2.5
    assert result["vpp1"]["actual_shed_kwh"] == 2.5
    assert result["vpp2"]["status"] == "baseline_unavailable"
