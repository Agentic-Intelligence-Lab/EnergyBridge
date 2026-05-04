"""Translate external grid signal into normalized internal signal semantics."""


def translate_vpp_context_to_grid_demand(vpp_context: dict) -> dict:
    context = vpp_context or {}
    task_type = str(context.get("vpp_task_type", "INVITATION_DEMAND_RESPONSE")).upper()
    trigger_reason = str(context.get("vpp_trigger_reason", "REGIONAL_PEAK_LOAD")).upper()
    time_scale = str(context.get("vpp_time_scale", "DAY_AHEAD")).upper()
    response_direction = str(context.get("vpp_response_direction", "load_reduction")).lower()

    if "EMERGENCY" in task_type or trigger_reason in {"LOCAL_OVERLOAD", "POWER_SHORTAGE"}:
        price_level = "critical"
    elif trigger_reason in {"PRICE_SIGNAL", "REGIONAL_PEAK_LOAD"}:
        price_level = "high"
    else:
        price_level = "normal"

    control_intent = "reduce_load" if response_direction == "load_reduction" else "normal_operation"
    if control_intent == "normal_operation" and price_level in {"high", "critical"}:
        control_intent = "cost_saving"

    urgency = "low"
    if price_level == "critical":
        urgency = "high"
    elif time_scale == "REAL_TIME" or int(context.get("vpp_notice_minutes", 0) or 0) <= 60:
        urgency = "medium"

    strictness = "soft"
    if price_level in {"high", "critical"}:
        strictness = "moderate"
    if urgency == "high":
        strictness = "hard"

    response_deadline = str(context.get("vpp_declaration_deadline", "") or context.get("vpp_start_time", ""))

    return {
        "type": "EMERGENCY_DR" if "EMERGENCY" in task_type else "DR_EVENT",
        "price_level": price_level,
        "control_intent": control_intent,
        "urgency": urgency,
        "strictness": strictness,
        "start_time": str(context.get("vpp_start_time", "")),
        "end_time": str(context.get("vpp_end_time", "")),
        "duration_minutes": int(context.get("vpp_duration_minutes", 60) or 60),
        "response_deadline": response_deadline,
        "total_required_capacity_kw": float(context.get("vpp_required_capacity_kw", 0.0) or 0.0),
        "capacity_scope": str(context.get("vpp_capacity_scope", "upstream_total_capacity")),
    }
