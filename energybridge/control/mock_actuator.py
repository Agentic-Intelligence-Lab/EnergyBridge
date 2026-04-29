"""Mock actuation layer for local EnergyBridge demos."""


def execute_control_plan(control_plan: dict, approved: bool = True) -> dict:
    if not approved:
        return {
            "status": "skipped",
            "actuator": "mock_electrical_actuator_v0",
            "reason": "user_did_not_approve",
        }

    return {
        "status": "executed",
        "actuator": "mock_electrical_actuator_v0",
        "action": control_plan.get("action", "unknown"),
        "setpoint": control_plan.get("setpoint"),
        "duration_minutes": control_plan.get("duration_minutes"),
    }
