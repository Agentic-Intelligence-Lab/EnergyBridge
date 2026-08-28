"""Evidence-backed operational knowledge for the adaptive household harness.

This module keeps device know-how separate from household preferences and
episodic memory.  Curated priors are deliberately weak, while observations
from actuator-facing executions can revise the useful operating envelope.
Nothing in this store selects an action or rewards a controller identity.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

from .profile_v3 import sanitize_observable_payload


OPERATIONS_KNOWLEDGE_VERSION = "energybridge.operations_knowledge.v3"
OPERATIONS_CAPSULE_VERSION = "energybridge.operations_knowledge_capsule.v3"


def _now(value: str | None = None) -> str:
    return str(value or datetime.now(timezone.utc).isoformat())


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _duration(start: Any, end: Any) -> float | None:
    left, right = _finite(start), _finite(end)
    if left is None or right is None:
        return None
    value = right - left
    if value < 0:
        value += 24.0
    return round(value, 6) if 0.0 < value <= 24.0 else None


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _entry(
    knowledge_id: str,
    *,
    device: str,
    statement: str,
    source: str,
    confidence: float,
    evidence: list[dict] | None = None,
    value: Any = None,
    unit: str | None = None,
    status: str = "advisory",
) -> dict:
    item = {
        "knowledge_id": knowledge_id,
        "device": device,
        "statement": statement,
        "source": source,
        "confidence": round(max(0.0, min(1.0, confidence)), 4),
        "status": status,
        "evidence": deepcopy(evidence or []),
    }
    if value is not None:
        item["value"] = value
    if unit:
        item["unit"] = unit
    return item


def initialize_operations_knowledge(
    devices: Mapping[str, Any] | None,
    *,
    observed_at: str | None = None,
) -> dict:
    """Create a cold operational store from observable device capabilities.

    The one-hour water-heater value is a low-confidence probe prior, not a
    claimed physical minimum.  It is replaced/refined by household outcomes.
    """
    safe_devices = sanitize_observable_payload(devices or {})
    safe_devices = safe_devices if isinstance(safe_devices, dict) else {}
    facts: list[dict] = []
    wh = safe_devices.get("water_heater")
    if isinstance(wh, dict) and bool(wh.get("present", False)):
        deadline = _finite(wh.get("bath_required_h"))
        if deadline is not None:
            facts.append(_entry(
                "water_heater.service_deadline",
                device="water_heater",
                statement="Hot water should be ready by the observed household service deadline.",
                source="observable_device_configuration",
                confidence=1.0,
                value=round(deadline, 4),
                unit="hour_of_day",
                status="observed_constraint",
                evidence=[{"path": "/device_capabilities/water_heater/bath_required_h"}],
            ))
        configured_duration = _duration(
            wh.get("pre_heat_window_start_h"), wh.get("pre_heat_window_end_h")
        )
        if configured_duration is not None:
            facts.append(_entry(
                "water_heater.configured_service_envelope",
                device="water_heater",
                statement=(
                    "The configured preheat window is a conservative service envelope, not proof "
                    "that the heater must run for its full duration."
                ),
                source="observable_device_configuration",
                confidence=0.8,
                value=configured_duration,
                unit="hours",
                evidence=[
                    {"path": "/device_capabilities/water_heater/pre_heat_window_start_h"},
                    {"path": "/device_capabilities/water_heater/pre_heat_window_end_h"},
                ],
            ))
        facts.append(_entry(
            "water_heater.initial_duration_probe",
            device="water_heater",
            statement=(
                "Without tank-temperature or recovery telemetry, about one hour is a reasonable "
                "first preheat probe for this simplified controller. Verify readiness and revise; "
                "do not treat this as a guaranteed minimum."
            ),
            source="curated_low_confidence_operational_prior",
            confidence=0.35,
            value=1.0,
            unit="hours",
            evidence=[{"reference": "cold_start_probe_prior_v1"}],
        ))

    for device in ("washer", "dishwasher", "dryer"):
        cfg = safe_devices.get(device)
        duration = _finite(cfg.get("duration_h")) if isinstance(cfg, dict) else None
        if isinstance(cfg, dict) and bool(cfg.get("present", False)) and duration is not None:
            facts.append(_entry(
                f"{device}.declared_cycle_duration",
                device=device,
                statement="Use the declared full cycle duration when checking windows and overlap.",
                source="observable_device_configuration",
                confidence=1.0,
                value=round(duration, 4),
                unit="hours",
                status="observed_constraint",
                evidence=[{"path": f"/device_capabilities/{device}/duration_h"}],
            ))

    ev = safe_devices.get("ev")
    if isinstance(ev, dict) and bool(ev.get("present", False)):
        charger = _finite(ev.get("charger_kw")) or 7.0
        efficiency = _finite(ev.get("efficiency")) or 0.92
        daily_drive = _finite(ev.get("daily_drive_kwh")) or 8.0
        soc_contract_complete = all(
            key in ev for key in ("capacity_kwh", "target_soc", "min_soc")
        )
        capacity = _finite(ev.get("capacity_kwh")) or 60.0
        target_soc = _finite(ev.get("target_soc"))
        target_soc = 0.8 if target_soc is None else max(0.0, min(1.0, target_soc))
        min_soc = _finite(ev.get("min_soc"))
        min_soc = 0.15 if min_soc is None else max(0.0, min(target_soc, min_soc))
        soc_requirement = (target_soc - min_soc) * capacity if soc_contract_complete else 0.0
        required_kwh = max(daily_drive, soc_requirement)
        arithmetic = required_kwh / max(0.1, charger * efficiency)
        # Half an hour covers one 10-minute actuation step, floating boundary
        # effects, and a small uncertainty reserve.  It remains advisory; the
        # runtime validator independently enforces its own service minimum.
        robust_probe = min(8.0, arithmetic + 0.5)
        facts.append(_entry(
            "ev.departure_target_charge_duration",
            device="ev",
            statement=(
                "The arithmetic recharge duration is only a lower bound. For an exact departure "
                "SOC target, leave a visible control-step and uncertainty margin and revise it from "
                "observed target outcomes."
            ),
            source="observable_device_physics_with_control_margin",
            confidence=0.7,
            value={
                "energy_only_lower_bound_h": round(arithmetic, 4),
                "initial_robust_probe_h": round(robust_probe, 4),
                "energy_requirement_kwh": round(required_kwh, 4),
            },
            unit="hours",
            evidence=[
                {"path": "/device_capabilities/ev/charger_kw"},
                {"path": "/device_capabilities/ev/daily_drive_kwh"},
                {"path": "/device_capabilities/ev/efficiency"},
            ],
        ))

    return {
        "schema_version": OPERATIONS_KNOWLEDGE_VERSION,
        "updated_at": _now(observed_at),
        "revision": 0,
        "policy": {
            "selection_performed": False,
            "controller_identity_used": False,
            "curated_priors_are_hard_constraints": False,
            "learning_requires_observed_execution": True,
        },
        "device_capabilities_fingerprint": _fingerprint(safe_devices),
        "facts": facts,
        "observations": [],
        "revision_ledger": [],
    }


def update_operations_knowledge(
    knowledge: Mapping[str, Any],
    *,
    event_id: str,
    executed_plan: Mapping[str, Any] | None,
    outcome: Mapping[str, Any] | None,
    observed_at: str | None = None,
) -> dict:
    """Learn operational envelopes only from a recorded execution and outcome."""
    updated = deepcopy(dict(knowledge or {}))
    if updated.get("schema_version") != OPERATIONS_KNOWLEDGE_VERSION:
        raise ValueError("unsupported operational knowledge schema")
    executed = sanitize_observable_payload(executed_plan or {})
    result = sanitize_observable_payload(outcome or {})
    executed = executed if isinstance(executed, dict) else {}
    result = result if isinstance(result, dict) else {}
    actions = executed.get("appliance_actions", executed.get("appliances", {}))
    actions = actions if isinstance(actions, dict) else {}
    summary = result.get("appliance_summary")
    summary = summary if isinstance(summary, dict) else {}
    if not executed or not summary:
        return updated

    observations = list(updated.get("observations") or [])
    wh_result = summary.get("water_heater")
    duration = _duration(
        actions.get("water_heater_preheat_start_h"),
        actions.get("water_heater_preheat_end_h"),
    )
    if (
        isinstance(wh_result, dict)
        and actions.get("water_heater_preheat") is True
        and duration is not None
        and "ready_at_bath" in wh_result
    ):
        observation = {
            "observation_id": f"{event_id}:water_heater:{len(observations) + 1}",
            "event_id": str(event_id),
            "device": "water_heater",
            "metric": "preheat_duration_h",
            "value": duration,
            "unit": "hours",
            "service_succeeded": bool(wh_result.get("ready_at_bath")),
            "observed_at": _now(observed_at),
            "source": "actuator_execution_and_service_outcome",
            "execution_fingerprint": _fingerprint(executed),
        }
        observations.append(observation)

    updated["observations"] = observations[-128:]
    wh_observations = [
        item for item in updated["observations"]
        if isinstance(item, dict)
        and item.get("device") == "water_heater"
        and item.get("metric") == "preheat_duration_h"
    ]
    facts = [
        deepcopy(item) for item in list(updated.get("facts") or [])
        if not (isinstance(item, dict) and item.get("knowledge_id") == "water_heater.learned_duration_envelope")
    ]
    if wh_observations:
        successes = [float(item["value"]) for item in wh_observations if item.get("service_succeeded")]
        failures = [float(item["value"]) for item in wh_observations if not item.get("service_succeeded")]
        upper = min(successes) if successes else None
        lower = max(failures) if failures else None
        statement_parts = []
        if upper is not None:
            statement_parts.append(f"The shortest observed successful preheat was {upper:g} h")
        if lower is not None:
            statement_parts.append(f"the longest observed failed preheat was {lower:g} h")
        statement = "; ".join(statement_parts) + ". Treat this as household-specific evidence, not a universal rule."
        facts.append(_entry(
            "water_heater.learned_duration_envelope",
            device="water_heater",
            statement=statement,
            source="observed_execution_outcomes",
            confidence=min(0.92, 0.45 + 0.1 * len(wh_observations)),
            value={"failed_lower_bound_h": lower, "successful_upper_bound_h": upper},
            unit="hours",
            evidence=[{"observation_id": item["observation_id"]} for item in wh_observations[-8:]],
        ))
    updated["facts"] = facts
    updated["revision"] = int(updated.get("revision", 0) or 0) + 1
    updated["updated_at"] = _now(observed_at)
    updated.setdefault("revision_ledger", []).append({
        "revision": updated["revision"],
        "event_id": str(event_id),
        "observations_considered": len(wh_observations),
        "updated_at": updated["updated_at"],
    })
    updated["revision_ledger"] = updated["revision_ledger"][-128:]
    return updated


def build_operations_knowledge_capsule(
    knowledge: Mapping[str, Any] | None,
    *,
    event: Mapping[str, Any] | None = None,
    max_facts: int = 12,
) -> dict:
    """Return a compact, model-visible advisory knowledge capsule."""
    store = deepcopy(dict(knowledge or {}))
    facts = [item for item in list(store.get("facts") or []) if isinstance(item, dict)]
    visible_devices = set()
    for value in (event or {}).get("affected_devices", []) if isinstance(event, Mapping) else []:
        visible_devices.add(str(value))
    if visible_devices:
        relevant = [item for item in facts if str(item.get("device")) in visible_devices]
        facts = relevant or facts
    facts.sort(key=lambda item: (-float(item.get("confidence", 0.0) or 0.0), str(item.get("knowledge_id", ""))))
    decision_notes: list[dict] = []
    event_start = _finite((event or {}).get("trigger_h")) if isinstance(event, Mapping) else None
    event_end = _finite((event or {}).get("end_h")) if isinstance(event, Mapping) else None
    fact_map = {str(item.get("knowledge_id")): item for item in facts}
    wh_probe = fact_map.get("water_heater.initial_duration_probe")
    wh_deadline = fact_map.get("water_heater.service_deadline")
    probe_h = _finite((wh_probe or {}).get("value"))
    deadline_h = _finite((wh_deadline or {}).get("value"))
    if (
        event_start is not None and event_end is not None and probe_h is not None
        and deadline_h is not None
    ):
        examples: list[dict] = []
        if event_start - probe_h >= 0:
            examples.append({"start_h": round(event_start - probe_h, 4), "end_h": round(event_start, 4)})
        if event_end + probe_h <= deadline_h:
            examples.append({"start_h": round(event_end, 4), "end_h": round(event_end + probe_h, 4)})
        if examples:
            decision_notes.append({
                "device": "water_heater",
                "kind": "nonbinding_feasible_probe_examples",
                "statement": (
                    "These examples combine the weak duration probe with the current event boundary "
                    "and service deadline. They are not selected actions or guarantees."
                ),
                "examples": examples,
                "evidence_refs": [
                    "water_heater.initial_duration_probe",
                    "water_heater.service_deadline",
                    "/event/trigger_h",
                    "/event/end_h",
                ],
            })
    ev_fact = fact_map.get("ev.departure_target_charge_duration")
    ev_value = (ev_fact or {}).get("value")
    robust_h = _finite(ev_value.get("initial_robust_probe_h")) if isinstance(ev_value, dict) else None
    if event_end is not None and robust_h is not None:
        start_h = event_end % 24.0
        decision_notes.append({
            "device": "ev",
            "kind": "nonbinding_post_event_probe_example",
            "statement": (
                "Starting at the event end avoids the event; the proposed end includes an initial "
                "control and uncertainty margin. Verify it against arrival and departure constraints."
            ),
            "example": {
                "start_h": round(start_h, 4),
                "end_h": round((start_h + robust_h) % 24.0, 4),
                "duration_h": round(robust_h, 4),
            },
            "evidence_refs": [
                "ev.departure_target_charge_duration",
                "/event/end_h",
                "/device_capabilities/ev/arrival_h",
                "/device_capabilities/ev/departure_h",
            ],
        })
    return {
        "schema_version": OPERATIONS_CAPSULE_VERSION,
        "knowledge_revision": int(store.get("revision", 0) or 0),
        "usage_contract": (
            "Advisory evidence only: cite relevant facts, respect their confidence, and deviate when "
            "current observations justify it. Do not treat a probe prior as a hard constraint."
        ),
        "selection_performed": False,
        "facts": deepcopy(facts[: max(1, int(max_facts))]),
        "current_decision_notes": decision_notes,
    }
