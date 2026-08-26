"""Epistemic provenance for an open household planning decision.

This module does not interpret a household statement or recommend an action.
It only tells the planning model what kind of evidence each existing payload
section contains. A direct statement made for the current event should not be
silently weakened by an older inferred profile, while its conditions must not
be treated as already satisfied.
"""

from __future__ import annotations

from typing import Any, Mapping


DECISION_EVIDENCE_LEDGER_VERSION = "energybridge.decision_evidence_ledger.v1"


def _has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, (list, tuple, set)):
        return bool(value)
    return True


def build_decision_evidence_ledger(
    *,
    observable_profile: Mapping[str, Any] | None,
    memory: Mapping[str, Any] | None,
    event: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Describe evidence authority without deciding what the evidence means.

    Entries are JSON Pointer references into the planning payload, so this
    function neither duplicates potentially sensitive free text nor applies a
    keyword classifier. The base model remains responsible for interpreting
    the household's words and choosing the plan.
    """

    safe_event = event if isinstance(event, Mapping) else {}
    entries: list[dict[str, Any]] = []

    if _has_content(safe_event.get("current_user_message")):
        entries.append({
            "evidence_id": "current_household_statement",
            "evidence_path": "/event/current_user_message",
            "evidence_class": "direct_household_statement",
            "temporal_scope": "current_event",
            "interpretation_scope": (
                "use the statement literally for this event; preserve every "
                "condition and do not broaden permission"
            ),
        })

    if _has_content(observable_profile):
        entries.append({
            "evidence_id": "learned_household_profile",
            "evidence_path": "/observable_profile",
            "evidence_class": "inferred_or_accumulated_profile",
            "temporal_scope": "cross_event",
            "interpretation_scope": (
                "inform matters the current statement leaves unspecified and "
                "retain the confidence and provenance supplied by the profile"
            ),
        })

    if _has_content(memory):
        entries.append({
            "evidence_id": "relevant_observed_history",
            "evidence_path": "/relevant_memory",
            "evidence_class": "historical_observation",
            "temporal_scope": "prior_events",
            "interpretation_scope": (
                "use as context-specific evidence, not as a universal household rule"
            ),
        })

    return {
        "schema_version": DECISION_EVIDENCE_LEDGER_VERSION,
        "entries": entries,
        "conflict_policy": {
            "same_topic": (
                "a direct current-event household statement takes precedence over "
                "an inferred profile or older history for this event"
            ),
            "unspecified_topics": (
                "profile and relevant history may inform issues the current statement "
                "does not address, in proportion to their confidence and provenance"
            ),
            "conditions": (
                "conditional permission is not blanket permission; verify its stated "
                "conditions against the proposed plan and observable evidence"
            ),
            "hard_constraints": "physical and explicit hard constraints remain invariant",
        },
        "selection_performed": False,
        "action_recommendation": None,
    }


__all__ = [
    "DECISION_EVIDENCE_LEDGER_VERSION",
    "build_decision_evidence_ledger",
]
