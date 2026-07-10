from energybridge.simulation.appliance_sim import ApplianceSuite


def test_day_one_ev_can_charge_in_same_day_morning_window() -> None:
    suite = ApplianceSuite(
        {
            "ev": {
                "present": True,
                "charger_kw": 7.4,
                "capacity_kwh": 60.0,
                "target_soc": 0.8,
                "min_soc": 0.2,
                "departure_h": 7.5,
                "daily_drive_kwh": 20.0,
            }
        },
        sim_days=1,
        vpp_events=[],
        explicit_only=True,
    )

    assert suite.set_ev_charge_window(0, start_h=0.0, end_h=8.0)
    for step in range(16):
        suite.step(step * 0.5, 0.5)

    result = suite.all_results()["ev"][0]
    assert result["energy_kwh"] > 0.0
    assert result["target_reached"] is True
