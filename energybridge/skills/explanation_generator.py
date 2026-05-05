"""User-facing explanation for the selected plan and safety decision."""


def _get_session_summary(memory: dict | None) -> dict:
    session_summary = (memory or {}).get("session_summary", {})
    return session_summary if isinstance(session_summary, dict) else {}


def generate_explanation(
    candidate_strategy: dict,
    control_plan: dict,
    safety_report: dict,
    memory: dict | None = None,
) -> str:
    session_summary = _get_session_summary(memory)
    recent_preferences = session_summary.get("recent_preferences", {})
    recent_control_plan = session_summary.get("recent_control_plan", {})
    safe = bool(safety_report.get("safe", False))
    fallback_applied = bool(safety_report.get("fallback_applied", False))
    mode = candidate_strategy.get("mode", "unknown")
    setpoint = control_plan.get("setpoint", "n/a")
    duration = control_plan.get("duration_minutes", "n/a")

    preference_hint = ""
    if recent_preferences:
        comfort_priority = recent_preferences.get("comfort_priority")
        grid_priority = recent_preferences.get("grid_priority")
        if comfort_priority is not None and grid_priority is not None:
            preference_hint = (
                f" Recent session context suggests comfort_priority={comfort_priority} "
                f"and grid_priority={grid_priority}."
            )

    continuity_hint = ""
    if recent_control_plan:
        continuity_hint = (
            f" The previous safe plan used setpoint {recent_control_plan.get('setpoint', 'n/a')}C."
        )

    if not safe:
        violations = "; ".join(safety_report.get("violations", [])) or "unknown reason"
        return (
            "The proposed control action was rejected by safety validation. "
            f"Reason: {violations}. A conservative fallback action was applied.{preference_hint}"
        )

    if fallback_applied:
        original_violations = "; ".join(safety_report.get("original_violations", [])) or "unknown reason"
        return (
            "The original plan did not pass safety validation. "
            f"Reason: {original_violations}. A conservative fallback plan was applied at {setpoint}C "
            f"for about {duration} minutes.{preference_hint}"
        )

    rationale = candidate_strategy.get("rationale", [])
    rationale_text = " ".join(rationale) if rationale else "Policy selected by deterministic rules."
    return (
        f"Mode: {mode}. HVAC setpoint will be adjusted to {setpoint}C for about {duration} minutes. "
        f"Safety checks passed. {rationale_text}{preference_hint}{continuity_hint}"
    )
