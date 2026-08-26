"""Outcome calibration records for professional planning evidence.

The module compares a selected candidate's observable forecasts with later
physical observations. It does not learn a controller policy, rank methods,
or turn agreement into a scalar reward. The resulting records let a future
base model calibrate how much confidence to place in a tool for a similar
household context.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from typing import Any, Mapping, Sequence


OUTCOME_CALIBRATION_VERSION = "energybridge.outcome_calibration.v1"
CALIBRATION_CAPSULE_VERSION = "energybridge.calibration_capsule.v1"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    try:
        return float(value) > 1e-9
    except (TypeError, ValueError):
        return False


def _wilson_interval(successes: int, observations: int) -> list[float] | None:
    """Return a descriptive 95% binomial interval without deriving a weight."""
    if observations <= 0:
        return None
    z = 1.959963984540054
    n = float(observations)
    rate = float(successes) / n
    denominator = 1.0 + (z * z / n)
    centre = (rate + (z * z / (2.0 * n))) / denominator
    margin = (
        z
        * math.sqrt((rate * (1.0 - rate) / n) + (z * z / (4.0 * n * n)))
        / denominator
    )
    return [round(max(0.0, centre - margin), 6), round(min(1.0, centre + margin), 6)]


def _observed_service_complete(name: str, value: Mapping[str, Any]) -> bool | None:
    if not bool(value.get("present", False)):
        return None
    if name == "water_heater":
        return bool(value.get("ready_at_bath"))
    if name == "ev":
        return bool(value.get("target_reached"))
    if "completed" in value:
        return bool(value.get("completed"))
    return None


def _predicted_service_complete(name: str, value: Mapping[str, Any]) -> bool | None:
    if name == "water_heater" and "ready_by_declared_deadline" in value:
        return bool(value.get("ready_by_declared_deadline"))
    if name == "ev" and "target_reached" in value:
        return bool(value.get("target_reached"))
    if "task_completed" in value:
        return bool(value.get("task_completed"))
    return None


def build_outcome_calibration_record(
    planning_evidence: Mapping[str, Any] | None,
    outcome: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Compare selected planning evidence with an attributed physical outcome."""

    evidence = _mapping(planning_evidence)
    observed = _mapping(outcome)
    predicted_devices = _mapping(evidence.get("device_impacts"))
    observed_devices = _mapping(observed.get("appliance_summary"))
    observations: list[dict[str, Any]] = []

    if predicted_devices and observed_devices:
        predicted_overlap = sorted(
            name
            for name, value in predicted_devices.items()
            if isinstance(value, Mapping)
            and (
                _positive(value.get("vpp_overlap_h"))
                or _positive(value.get("vpp_overlap_energy_kwh"))
                or _positive(value.get("vpp_overlap_energy_upper_bound_kwh"))
            )
        )
        observed_overlap = sorted(
            name
            for name, value in observed_devices.items()
            if isinstance(value, Mapping)
            and bool(value.get("present", False))
            and bool(value.get("ran_during_vpp", False))
        )
        observations.append({
            "signal": "non_ac_event_overlap",
            "forecast": {"overlapping_devices": predicted_overlap},
            "measurement": {"overlapping_devices": observed_overlap},
            "agreement": predicted_overlap == observed_overlap,
            "evidence_paths": [
                "/planning_evidence/device_impacts",
                "/outcome/appliance_summary",
            ],
        })

        for name in sorted(set(predicted_devices) & set(observed_devices)):
            predicted_value = predicted_devices.get(name)
            observed_value = observed_devices.get(name)
            if not isinstance(predicted_value, Mapping) or not isinstance(observed_value, Mapping):
                continue
            forecast = _predicted_service_complete(name, predicted_value)
            measurement = _observed_service_complete(name, observed_value)
            if forecast is None or measurement is None:
                continue
            observations.append({
                "signal": "service_completion",
                "device": str(name),
                "forecast": forecast,
                "measurement": measurement,
                "agreement": forecast == measurement,
                "evidence_paths": [
                    f"/planning_evidence/device_impacts/{name}",
                    f"/outcome/appliance_summary/{name}",
                ],
            })

    hvac = _mapping(evidence.get("hvac_impact"))
    if hvac.get("comfort_violation_c") is not None and observed.get(
        "comfort_violation_minutes"
    ) is not None:
        forecast = _positive(hvac.get("comfort_violation_c"))
        measurement = _positive(observed.get("comfort_violation_minutes"))
        observations.append({
            "signal": "comfort_violation",
            "forecast": forecast,
            "measurement": measurement,
            "agreement": forecast == measurement,
            "evidence_paths": [
                "/planning_evidence/hvac_impact/comfort_violation_c",
                "/outcome/comfort_violation_minutes",
            ],
        })

    supported_claims = list(
        _mapping(evidence.get("offer_specific_comparison")).get(
            "supported_benefit_claims"
        )
        or []
    )
    return json.loads(json.dumps({
        "schema_version": OUTCOME_CALIBRATION_VERSION,
        "candidate_id": evidence.get("candidate_id"),
        "plan_fingerprint": evidence.get("plan_fingerprint"),
        "observations": observations,
        "observation_count": len(observations),
        "supported_claims": deepcopy(supported_claims[:12]),
        "claim_measurement_status": (
            "not_comparable_without_same-scope_measurement"
            if supported_claims else "no_supported_numeric_claim"
        ),
        "policy_update_performed": False,
        "ranking_performed": False,
    }, ensure_ascii=False, allow_nan=False, default=str))


def build_calibration_capsule(
    records: Sequence[Mapping[str, Any]] | None,
    *,
    max_recent_disagreements: int = 6,
) -> dict[str, Any]:
    """Aggregate evidence agreement without deriving a weight or controller."""

    summaries: dict[str, dict[str, Any]] = {}
    disagreements: list[dict[str, Any]] = []
    record_count = 0
    for record in list(records or []):
        if not isinstance(record, Mapping):
            continue
        record_count += 1
        for item in list(record.get("observations") or []):
            if not isinstance(item, Mapping):
                continue
            signal = str(item.get("signal") or "unspecified")
            summary = summaries.setdefault(signal, {
                "observation_count": 0,
                "agreement_count": 0,
                "disagreement_count": 0,
            })
            summary["observation_count"] += 1
            if bool(item.get("agreement")):
                summary["agreement_count"] += 1
            else:
                summary["disagreement_count"] += 1
                disagreements.append({
                    "signal": signal,
                    "device": item.get("device"),
                    "forecast": deepcopy(item.get("forecast")),
                    "measurement": deepcopy(item.get("measurement")),
                    "evidence_paths": deepcopy(item.get("evidence_paths") or []),
                })

    for summary in summaries.values():
        count = int(summary["observation_count"])
        agreements = int(summary["agreement_count"])
        summary["descriptive_agreement_rate"] = round(agreements / count, 6)
        summary["wilson_95_interval"] = _wilson_interval(agreements, count)

    return {
        "schema_version": CALIBRATION_CAPSULE_VERSION,
        "source_record_count": record_count,
        "signal_summaries": summaries,
        "recent_disagreements": disagreements[-max(0, int(max_recent_disagreements)):],
        "epistemic_note": (
            "Agreement and disagreement calibrate forecast confidence only; "
            "rates and intervals are descriptive rather than learned weights; "
            "they are not a reward, ranking, or action recommendation."
        ),
        "policy_update_performed": False,
        "ranking_performed": False,
    }


__all__ = [
    "OUTCOME_CALIBRATION_VERSION",
    "CALIBRATION_CAPSULE_VERSION",
    "build_outcome_calibration_record",
    "build_calibration_capsule",
]
