"""User-facing explanation for the selected plan and safety decision."""


def generate_explanation(
    candidate_strategy: dict,
    control_plan: dict,
    safety_report: dict,
) -> str:
    safe = bool(safety_report.get("safe", False))
    fallback_applied = bool(safety_report.get("fallback_applied", False))
    mode = candidate_strategy.get("mode", "unknown")
    setpoint = control_plan.get("setpoint", "n/a")
    duration = control_plan.get("duration_minutes", "n/a")

    if not safe:
        violations = "; ".join(safety_report.get("violations", [])) or "unknown reason"
        return (
            "The proposed control action was rejected by safety validation. "
            f"Reason: {violations}. A conservative fallback action was applied."
        )

    if fallback_applied:
        original_violations = "; ".join(safety_report.get("original_violations", [])) or "unknown reason"
        return (
            "The original plan did not pass safety validation. "
            f"Reason: {original_violations}. A conservative fallback plan was applied at {setpoint}C "
            f"for about {duration} minutes."
        )

    rationale = candidate_strategy.get("rationale", [])
    rationale_text = " ".join(rationale) if rationale else "Policy selected by deterministic rules."
    return (
        f"Mode: {mode}. HVAC setpoint will be adjusted to {setpoint}C for about {duration} minutes. "
        f"Safety checks passed. {rationale_text}"
    )
