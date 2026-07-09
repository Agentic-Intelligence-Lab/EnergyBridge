"""Customer-facing VPP participation explanation skill.

This skill turns the internal EnergyBridge strategy explanation into the text
that EnergyBridge shows to, and scores with, the household customer.
"""

from __future__ import annotations

from typing import Any


REVIEW_DIMENSION_KEYS = (
    "comfort_guarantee",
    "task_guarantee",
    "controllability",
    "economic_benefit",
    "executable_actions",
    "personalization",
    "verifiable_values",
    "alternatives",
)


def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _first_present(*values: Any) -> str:
    for value in values:
        text = _as_text(value)
        if text:
            return text
    return ""


def _coerce_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return [item for item in value if item not in ("", None)]
    if value in ("", None):
        return []
    return [value]


def _human_join(items: list[str]) -> str:
    clean = [item.strip() for item in items if item and item.strip()]
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0]
    if len(clean) == 2:
        return f"{clean[0]} and {clean[1]}"
    return ", ".join(clean[:-1]) + f", and {clean[-1]}"


def _fmt_hour(hour: Any) -> str:
    try:
        h = float(hour) % 24.0
    except (TypeError, ValueError):
        return "?"
    hh = int(h)
    mm = int(round((h - hh) * 60))
    if mm >= 60:
        hh = (hh + 1) % 24
        mm = 0
    return f"{hh:02d}:{mm:02d}"


def _action_sentence(action: dict[str, Any]) -> str:
    device = _as_text(action.get("device")).replace("_", " ")
    rationale = _as_text(action.get("rationale"))
    amount = action.get("amount")
    if rationale:
        return rationale.rstrip(".") + "."
    if isinstance(amount, dict):
        start = amount.get("start_h") or amount.get("charge_start_h") or amount.get("preheat_start_h")
        end = amount.get("charge_end_h") or amount.get("preheat_end_h")
        if start is not None and end is not None:
            return f"Schedule {device} from {_fmt_hour(start)} to {_fmt_hour(end)}."
        if start is not None:
            return f"Start {device} at {_fmt_hour(start)}."
        if amount.get("avoid_window"):
            return f"Keep {device} out of {amount.get('avoid_window')}."
    if device:
        return f"Manage {device} according to the selected VPP plan."
    return ""


def _action_summary(actions: list[Any], limit: int = 5) -> str:
    sentences = [
        _action_sentence(item)
        for item in actions
        if isinstance(item, dict)
    ]
    sentences = [item for item in sentences if item]
    if not sentences:
        return "EnergyBridge will keep flexible appliance load out of the VPP window where feasible."
    return " ".join(sentences[:limit])


def _alternatives_summary(alternatives: list[Any]) -> str:
    lines: list[str] = []
    for item in alternatives[:3]:
        if not isinstance(item, dict):
            text = _as_text(item)
            if text:
                lines.append(text.rstrip(".") + ".")
            continue
        name = _as_text(item.get("name"))
        summary = _as_text(item.get("summary"))
        tradeoff = _as_text(item.get("tradeoff"))
        if name and summary and tradeoff:
            lines.append(f"{name}: {summary.rstrip('.')} ({tradeoff.rstrip('.')}).")
        elif summary:
            lines.append(summary.rstrip(".") + ".")
    if not lines:
        return ""
    return "Alternatives: " + " ".join(lines)


def _service_protection_summary(protected: list[Any]) -> str:
    texts = [_as_text(item).rstrip(".") for item in protected if _as_text(item)]
    if not texts:
        return "Comfort, required services, and user override remain hard constraints."
    return "Protected boundaries: " + " ".join(text + "." for text in texts[:4])


def _control_summary(user_control: list[Any]) -> str:
    texts = [_as_text(item).rstrip(".") for item in user_control if _as_text(item)]
    if not texts:
        return "The customer can opt out, pause, or restore normal settings."
    return "Customer control: " + " ".join(text + "." for text in texts[:3])


def _benefit_summary(benefit: dict[str, Any]) -> str:
    message = _as_text(benefit.get("message") if isinstance(benefit, dict) else "")
    compensation = _as_text(benefit.get("compensation_note") if isinstance(benefit, dict) else "")
    if message and compensation:
        return f"Expected benefit: {message.rstrip('.')}. {compensation.rstrip('.')}."
    if message:
        return "Expected benefit: " + message.rstrip(".") + "."
    return "Expected benefit: lower event-window load with no invented monetary claim."


def _personalization_summary(notes: list[Any]) -> str:
    texts = [_as_text(item).rstrip(".") for item in notes if _as_text(item)]
    if not texts:
        return ""
    return "Personalization: " + " ".join(text + "." for text in texts[:3])


def _extract_verifiable_values(explanation: dict[str, Any]) -> list[str]:
    values: list[str] = []
    why = _as_text(explanation.get("why_request"))
    if "kW" in why or "kWh" in why:
        values.append(why)
    benefit = explanation.get("expected_benefit") or {}
    if isinstance(benefit, dict):
        for label, key in (
            ("target", "target_shed_kw"),
            ("available load", "load_shift_kw_estimate"),
            ("recommended bid", "recommended_bid_kw"),
        ):
            value = benefit.get(key)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                values.append(f"{label}: {number:.2f} kW")
    structured = explanation.get("structured_control_constraints") or {}
    if isinstance(structured, dict):
        window = ((structured.get("vpp_window") or {}) if isinstance(structured.get("vpp_window"), dict) else {})
        text = _as_text(window.get("text"))
        if text:
            values.append(f"event window: {text}")
    return values[:4]


def _review_dimensions(explanation: dict[str, Any]) -> dict[str, bool]:
    benefit = explanation.get("expected_benefit") or {}
    structured = explanation.get("structured_control_constraints") or {}
    hard_constraints = []
    if isinstance(structured, dict):
        hard_constraints = _coerce_list(structured.get("hard_constraints"))
    protected_text = " ".join(_as_text(item).lower() for item in _coerce_list(explanation.get("protected_constraints")))
    actions = _coerce_list(explanation.get("recommended_actions"))
    alternatives = _coerce_list(explanation.get("alternatives"))
    return {
        "comfort_guarantee": "comfort" in protected_text or bool(hard_constraints),
        "task_guarantee": any(
            token in protected_text
            for token in ("ev", "hot water", "washer", "dishwasher", "dryer", "routine", "service")
        ),
        "controllability": bool(_coerce_list(explanation.get("user_control"))),
        "economic_benefit": bool(isinstance(benefit, dict) and benefit.get("message")),
        "executable_actions": bool(actions),
        "personalization": bool(_coerce_list(explanation.get("personalization_notes"))),
        "verifiable_values": bool(_extract_verifiable_values(explanation)),
        "alternatives": len(alternatives) >= 2,
    }


def build_customer_explanation(explanation: dict[str, Any]) -> str:
    """Build the household-facing explanation used by UI and scoring."""
    why = _first_present(
        explanation.get("why_request"),
        "The VPP has requested a short demand-response action for the next event window.",
    )
    actions = _coerce_list(explanation.get("recommended_actions"))
    protected = _coerce_list(explanation.get("protected_constraints"))
    user_control = _coerce_list(explanation.get("user_control"))
    alternatives = _coerce_list(explanation.get("alternatives"))
    benefit = explanation.get("expected_benefit") if isinstance(explanation.get("expected_benefit"), dict) else {}
    personalization = _coerce_list(explanation.get("personalization_notes"))
    values = _extract_verifiable_values(explanation)

    parts = [
        f"EnergyBridge recommends this VPP participation plan because {why if why else 'the grid has requested short-term flexibility.'}",
        _action_summary(actions),
        _service_protection_summary(protected),
        _control_summary(user_control),
        _benefit_summary(benefit),
    ]
    personalization_text = _personalization_summary(personalization)
    if personalization_text:
        parts.append(personalization_text)
    if values:
        parts.append("Verifiable values: " + _human_join(values) + ".")
    alternatives_text = _alternatives_summary(alternatives)
    if alternatives_text:
        parts.append(alternatives_text)
    return "\n\n".join(part for part in parts if part).strip()


def finalize_vpp_participation_explanation(
    explanation: dict[str, Any] | None,
    *,
    score_prompt_text: str | None = None,
) -> dict[str, Any]:
    """Return a copy annotated as EnergyBridge's explanation to the customer."""
    result = dict(explanation or {})
    customer_text = _as_text(score_prompt_text) or build_customer_explanation(result)
    result["audience"] = "household_customer"
    result["speaker"] = "EnergyBridge"
    result["communication_goal"] = (
        "Explain the VPP participation strategy to the household customer before score and feedback."
    )
    result["customer_explanation"] = customer_text
    result["score_prompt_text"] = customer_text
    result["natural_language"] = customer_text

    existing_review = result.get("review_dimensions") if isinstance(result.get("review_dimensions"), dict) else {}
    result["review_dimensions"] = {
        **existing_review,
        **_review_dimensions(result),
    }
    result["review_dimension_keys"] = list(REVIEW_DIMENSION_KEYS)
    return result


def scoring_explanation_text(explanation: dict[str, Any] | None) -> str:
    """Return the EnergyBridge-to-customer text that should enter user scoring."""
    if not isinstance(explanation, dict):
        return ""
    return _first_present(
        explanation.get("score_prompt_text"),
        explanation.get("customer_explanation"),
        explanation.get("natural_language"),
    )
