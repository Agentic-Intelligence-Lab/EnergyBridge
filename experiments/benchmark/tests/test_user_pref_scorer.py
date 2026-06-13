from experiments.benchmark.user_pref_scorer import score_user_preference


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
