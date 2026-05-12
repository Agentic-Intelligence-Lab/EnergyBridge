"""Simple rule-based baselines for EnergyBridge benchmark.

Each baseline is a pure function:

    baseline(home_state, vpp_context, user_input) -> control_plan dict

These are deterministic, fast, and do not use LLM or memory.
MPC integration is out of scope here; see Family_Model/control_model/.
"""

from __future__ import annotations
from typing import Any

_MIN_SP, _MAX_SP, _DEFAULT_SP = 18.0, 30.0, 25.0


def _clamp(v: float) -> float:
    return max(_MIN_SP, min(_MAX_SP, v))


def _cur_sp(home_state: dict) -> float:
    return float(home_state.get("hvac_setpoint") or _DEFAULT_SP)


def comfort_first(
    home_state: dict[str, Any],
    vpp_context: dict[str, Any],
    user_input: str = "",
) -> dict[str, Any]:
    """Small (+0.5 °C) or no adjustment; prioritises comfort."""
    sp = _clamp(_cur_sp(home_state) + 0.5)
    kw = float(home_state.get("hvac_power_kw") or 0.0)
    return {
        "action": "set_hvac_temperature",
        "setpoint": sp,
        "duration_minutes": int(vpp_context.get("duration_minutes", 60)),
        "estimated_power_kw": round(kw * 0.95, 3),
        "estimated_reduction_kw": round(kw * 0.05, 3),
        "controller": "comfort_first_v0",
        "notes": "Small adjustment; prioritises comfort over VPP compliance.",
    }


def grid_first(
    home_state: dict[str, Any],
    vpp_context: dict[str, Any],
    user_input: str = "",
) -> dict[str, Any]:
    """Large setpoint increase; prioritises load reduction.

    urgency → delta: high=+3°C, medium=+2°C, low=+1°C
    """
    urgency = str(vpp_context.get("urgency", "medium")).lower()
    delta = {"high": 3.0, "medium": 2.0, "low": 1.0}.get(urgency, 2.0)
    sp = _clamp(_cur_sp(home_state) + delta)
    kw = float(home_state.get("hvac_power_kw") or 0.0)
    frac = delta / 6.0
    return {
        "action": "set_hvac_temperature",
        "setpoint": sp,
        "duration_minutes": int(vpp_context.get("duration_minutes", 60)),
        "estimated_power_kw": round(kw * (1.0 - frac), 3),
        "estimated_reduction_kw": round(kw * frac, 3),
        "controller": "grid_first_v0",
        "notes": f"urgency={urgency}; delta={delta}°C; prioritises VPP compliance.",
    }


def rule_based_balanced(
    home_state: dict[str, Any],
    vpp_context: dict[str, Any],
    user_input: str = "",
) -> dict[str, Any]:
    """Moderate adjustment proportional to requested reduction and urgency."""
    urgency = str(vpp_context.get("urgency", "medium")).lower()
    req_kw = float(vpp_context.get("requested_reduction_kw") or 0.5)
    kw = float(home_state.get("hvac_power_kw") or 0.0)
    frac = min(req_kw / kw, 0.5) if kw > 0 else 0.1
    delta = min(round(frac * 10.0, 1),
                {"high": 3.0, "medium": 2.0, "low": 1.0}.get(urgency, 2.0))
    sp = _clamp(_cur_sp(home_state) + delta)
    red = round(kw * frac, 3)
    return {
        "action": "set_hvac_temperature",
        "setpoint": sp,
        "duration_minutes": int(vpp_context.get("duration_minutes", 60)),
        "estimated_power_kw": round(kw - red, 3),
        "estimated_reduction_kw": red,
        "controller": "rule_based_balanced_v0",
        "notes": f"urgency={urgency}; req={req_kw}kW; delta={delta}°C.",
    }


BASELINES: dict[str, Any] = {
    "comfort_first": comfort_first,
    "grid_first": grid_first,
    "rule_based_balanced": rule_based_balanced,
}


def get_baseline(name: str):
    if name not in BASELINES:
        raise KeyError(f"Unknown baseline '{name}'. Available: {list(BASELINES)}")
    return BASELINES[name]
