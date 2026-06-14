from experiments.benchmark.family_runner import (
    _ensure_price_sensitive_reason_estimate,
    _learned_efficiency_floor_c,
    _requested_skip_devices,
)


def test_requested_skip_devices_returns_only_explicit_true_flags() -> None:
    actions = {
        "washer_skip": True,
        "dishwasher_skip": False,
        "dryer_skip": None,
    }
    assert _requested_skip_devices(actions) == ["washer"]


def test_price_sensitive_reason_requires_quantified_impact() -> None:
    persona = {"tags": {"price": "price_sensitive"}}
    appliances = {
        "washer": {"present": True, "power_kw": 1.5},
        "dishwasher": {"present": True, "power_kw": 1.2},
        "water_heater": {"present": True, "rated_kw": 2.0},
    }
    event = {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0}

    repaired = _ensure_price_sensitive_reason_estimate(
        "Shifted flexible loads away from the event.",
        persona,
        appliances,
        event,
        demand_kw=3.077,
    )
    assert "est. shifted ~4.7kW" in repaired

    already_quantified = _ensure_price_sensitive_reason_estimate(
        "Shifted about 3.0kW away from the event.",
        persona,
        appliances,
        event,
        demand_kw=3.077,
    )
    assert already_quantified == "Shifted about 3.0kW away from the event."


def test_learned_efficiency_floor_requires_positive_history() -> None:
    persona = {
        "tags": {"comfort": "normal_comfort", "price": "price_sensitive", "control": "suggestion_first"},
        "schedule": {},
    }
    events = [
        {"score": 4, "comfort_score": 4, "comment": "Comfort stayed within range."},
        {"score": 4, "comfort_score": 4, "comment": "Comfort stayed reasonable."},
    ]

    assert _learned_efficiency_floor_c(
        events,
        persona,
        default_sp_c=25.0,
        preferred_max_c=26.0,
        vpp_active=False,
    ) == 25.5
    assert _learned_efficiency_floor_c(
        events,
        persona,
        default_sp_c=25.0,
        preferred_max_c=26.0,
        vpp_active=True,
    ) == 26.0


def test_learned_efficiency_floor_disabled_for_confirmation_users() -> None:
    persona = {
        "tags": {"comfort": "normal_comfort", "price": "needs_explanation", "control": "confirm_required"},
        "schedule": {},
    }
    events = [
        {"score": 4, "comfort_score": 4, "comment": "Comfort stayed within range."},
        {"score": 4, "comfort_score": 4, "comment": "Comfort stayed reasonable."},
    ]

    assert _learned_efficiency_floor_c(
        events,
        persona,
        default_sp_c=25.0,
        preferred_max_c=26.0,
        vpp_active=True,
    ) is None
