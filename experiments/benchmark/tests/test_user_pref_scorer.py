from experiments.benchmark.user_pref_scorer import (
    build_vpp_preference_memory_notes,
    score_user_preference,
)


def test_skipped_shiftable_task_forces_low_score() -> None:
    result = score_user_preference(
        building="family",
        method="agent",
        mean_temp_c=25.5,
        pmv_ok_fraction=0.9,
        energy_kwh_per_day=20.0,
        agent_setpoint_c=26.0,
        event_index=2,
        persona={"id": "basic_test", "scoring_weights": {"comfort": 0.5, "energy": 0.2, "vpp": 0.3}},
        appliance_summary={
            "washer": {"present": True, "skipped": True, "completed": False, "ran_during_vpp": False},
            "dishwasher": {"present": True, "skipped": False, "completed": True, "ran_during_vpp": False},
        },
    )

    assert result["score"] == 1
    assert result["vpp_score"] == 1
    assert "skipped" in result["comment"].lower()


def test_memory_notes_enable_cautious_energy_exploration_after_positive_feedback() -> None:
    persona = {
        "id": "generic_price_cooperative",
        "tags": {"comfort": "normal_comfort", "price": "price_sensitive", "control": "suggestion_first"},
        "appliances": {"ac": {"setpoint_preferred_min_c": 24.0, "setpoint_preferred_max_c": 26.0}},
    }
    events = [
        {"score": 4, "comfort_score": 4, "comment": "Comfort stayed within range."},
        {"score": 4, "comfort_score": 4, "comment": "Comfort stayed reasonable and VPP succeeded."},
    ]

    notes = build_vpp_preference_memory_notes(events, persona)

    joined = " ".join(notes).lower()
    assert "warm edge" in joined
    assert "avoid unnecessary cooling" in joined


def test_memory_notes_do_not_escalate_after_warmth_feedback() -> None:
    persona = {
        "id": "generic_price_cooperative",
        "tags": {"comfort": "normal_comfort", "price": "price_sensitive", "control": "suggestion_first"},
        "appliances": {"ac": {"setpoint_preferred_min_c": 24.0, "setpoint_preferred_max_c": 26.0}},
    }
    events = [
        {"score": 4, "comfort_score": 4, "comment": "Acceptable."},
        {"score": 4, "comfort_score": 4, "comment": "A bit too warm near 26.5."},
    ]

    notes = build_vpp_preference_memory_notes(events, persona)

    joined = " ".join(notes).lower()
    assert "warm edge" not in joined
    assert "do not escalate" in joined


def test_memory_notes_learn_fixed_vpp_overlap_as_constraint() -> None:
    persona = {
        "id": "fixed_water_heater_user",
        "tags": {"comfort": "normal_comfort", "price": "price_indifferent", "control": "low_auto_accept"},
        "appliances": {
            "water_heater": {"present": True, "dr_adjustable": False},
            "washer": {"present": True, "dr_adjustable": True},
        },
    }
    events = [
        {
            "score": 4,
            "comfort_score": 4,
            "comment": "Comfort and routine were preserved.",
            "appliance_summary": {
                "water_heater": {"present": True, "ran_during_vpp": True},
                "washer": {"present": True, "ran_during_vpp": False},
            },
        }
    ]

    notes = build_vpp_preference_memory_notes(events, persona)

    joined = " ".join(notes).lower()
    assert "fixed appliances overlap" in joined
    assert "controllable devices" in joined
