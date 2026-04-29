"""Translate external grid signal into normalized internal signal semantics."""


def translate_grid_signal(grid_signal: dict) -> dict:
    signal = grid_signal or {}
    event_type = str(signal.get("type", "NORMAL")).upper()
    price_level = str(signal.get("price_level", "normal")).lower()
    target_reduction_kw = float(signal.get("target_reduction_kw", 0.0) or 0.0)

    control_intent = "normal_operation"
    urgency = "low"

    if event_type in {"DR_EVENT", "EMERGENCY_DR"} and target_reduction_kw > 0:
        control_intent = "reduce_load"
        urgency = "high" if event_type == "EMERGENCY_DR" else "medium"
    elif price_level in {"high", "critical"}:
        control_intent = "cost_saving"
        urgency = "medium"

    return {
        "event_type": event_type,
        "price_level": price_level,
        "target_reduction_kw": target_reduction_kw,
        "start_time": str(signal.get("start_time", "")),
        "end_time": str(signal.get("end_time", "")),
        "duration_minutes": int(signal.get("duration_minutes", 60) or 60),
        "control_intent": control_intent,
        "urgency": urgency,
    }
