from __future__ import annotations

from energybridge.harness.decision_evidence_v3 import (
    DECISION_EVIDENCE_LEDGER_VERSION,
    build_decision_evidence_ledger,
)


def test_current_statement_has_event_scope_without_action_or_score() -> None:
    ledger = build_decision_evidence_ledger(
        observable_profile={"summary": "Routine flexibility remains uncertain."},
        memory={"episodes": [{"feedback": "Please ask next time."}]},
        event={"current_user_message": "You may move laundry if it still finishes today."},
    )

    assert ledger["schema_version"] == DECISION_EVIDENCE_LEDGER_VERSION
    assert ledger["selection_performed"] is False
    assert ledger["action_recommendation"] is None
    assert "acceptance_probability" not in ledger
    assert ledger["entries"][0] == {
        "evidence_id": "current_household_statement",
        "evidence_path": "/event/current_user_message",
        "evidence_class": "direct_household_statement",
        "temporal_scope": "current_event",
        "interpretation_scope": (
            "use the statement literally for this event; preserve every condition "
            "and do not broaden permission"
        ),
    }
    assert "takes precedence" in ledger["conflict_policy"]["same_topic"]
    assert "not blanket permission" in ledger["conflict_policy"]["conditions"]


def test_ledger_does_not_parse_or_copy_free_text() -> None:
    first = build_decision_evidence_ledger(
        observable_profile={"belief": "uncertain"},
        memory={},
        event={"current_user_message": "Move the washer."},
    )
    second = build_decision_evidence_ledger(
        observable_profile={"belief": "uncertain"},
        memory={},
        event={"current_user_message": "Do not move the washer."},
    )

    assert first == second
    serialized = str(first)
    assert "Move the washer" not in serialized
    assert "Do not move" not in serialized


def test_absent_sources_are_not_fabricated() -> None:
    ledger = build_decision_evidence_ledger(
        observable_profile={},
        memory={},
        event={},
    )

    assert ledger["entries"] == []
