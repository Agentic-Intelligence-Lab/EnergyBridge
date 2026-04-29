"""Mock MPC controller for deterministic local demos."""


def run_mock_mpc(
    candidate_strategy: dict,
    home_state: dict,
    translated_grid_signal: dict,
) -> dict:
    setpoint = float(candidate_strategy.get("recommended_setpoint", home_state.get("hvac_setpoint", 25.0)))
    base_power = float(home_state.get("hvac_power_kw", 2.0))

    control_intent = translated_grid_signal.get("control_intent", "normal_operation")
    duration_minutes = int(translated_grid_signal.get("duration_minutes", 0) or 0)
    if duration_minutes <= 0:
        duration_minutes = 60 if control_intent in {"reduce_load", "cost_saving"} else 30

    if setpoint > float(home_state.get("hvac_setpoint", 25.0)):
        estimated_power_kw = max(0.6, base_power - 0.4)
    elif setpoint < float(home_state.get("hvac_setpoint", 25.0)):
        estimated_power_kw = base_power + 0.3
    else:
        estimated_power_kw = base_power

    estimated_reduction_kw = max(0.0, base_power - estimated_power_kw)

    return {
        "action": "set_hvac_temperature",
        "setpoint": round(setpoint, 2),
        "duration_minutes": duration_minutes,
        "estimated_power_kw": round(estimated_power_kw, 3),
        "estimated_reduction_kw": round(estimated_reduction_kw, 3),
        "controller": "mock_mpc_v0",
    }
