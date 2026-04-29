"""Fallback control for safety rejection cases."""


def fallback_control_plan(home_state: dict, reason: str) -> dict:
    current_setpoint = float(home_state.get("hvac_setpoint", 25.0))
    safe_setpoint = min(26.0, max(24.0, current_setpoint))

    return {
        "action": "set_hvac_temperature",
        "setpoint": round(safe_setpoint, 2),
        "duration_minutes": 20,
        "estimated_power_kw": float(home_state.get("hvac_power_kw", 2.0)),
        "estimated_reduction_kw": 0.0,
        "controller": "fallback_controller_v0",
        "reason": reason,
    }
