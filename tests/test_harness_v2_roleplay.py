from __future__ import annotations

import json
from pathlib import Path

import pytest

from energybridge.harness.profile import RESUME_SCHEMA_VERSION, build_household_resume
from energybridge.harness.roleplay import (
    ACCEPTANCE_SCHEMA_VERSION,
    PROBABILITY_ROUNDING_TOLERANCE,
    RoleplayResponseError,
    build_roleplay_acceptance_prompts,
    normalize_roleplay_acceptance_response,
    sanitize_household_resume_for_roleplay,
    validate_roleplay_response_against_verified_facts,
)
from energybridge.llm.roleplay_user import (
    RoleplayUserSimulator,
    infer_observable_profile_from_answers,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PERSONA_DIR = PROJECT_ROOT / "energybridge" / "roleplay" / "personas"


def _load_persona(name: str) -> dict:
    return json.loads((PERSONA_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _valid_response(**updates) -> dict:
    response = {
        "decision": "counteroffer",
        "baseline_acceptance_probability": 0.37,
        "adjustments": [
            {
                "dimension": "return-home comfort",
                "delta": -0.08,
                "evidence": "E1",
                "reason": "I do not want to arrive to a warmer home.",
            },
            {
                "dimension": "appliance service",
                "delta": 0.03,
                "evidence": "E2",
                "reason": "The schedule preserves the chore I need today.",
            },
        ],
        "final_acceptance_probability": 0.32,
        "confidence": 0.74,
        "evidence": [
            {
                "id": "E1",
                "source": "resume",
                "fact": "Comfort on arrival is a recurring preference.",
                "effect": "supports_rejection",
            },
            {
                "id": "E2",
                "source": "plan",
                "fact": "The washer remains scheduled outside the event.",
                "effect": "supports_acceptance",
            },
        ],
        "counterfactual": {
            "changes": ["Restore the normal setpoint before arrival."],
            "decision_if_changed": "accept",
            "acceptance_probability_if_changed": 0.81,
            "reason": "That change would remove my main concern.",
        },
        "reason": "I would agree only if comfort is restored before I get home.",
        "user_feedback": "Please restore the normal temperature before my arrival.",
    }
    response.update(updates)
    return response


def test_all_fixed_family_personas_build_distinct_auditable_resumes() -> None:
    paths = sorted(PERSONA_DIR.glob("basic_role_*.json"))
    assert len(paths) >= 6

    resumes = [
        build_household_resume(json.loads(path.read_text(encoding="utf-8")))
        for path in paths
    ]
    fingerprints = [resume["audit"]["profile_fingerprint"] for resume in resumes]
    narratives = [resume["biography"]["first_person_roleplay_source"] for resume in resumes]

    assert len(set(fingerprints)) == len(resumes)
    assert len(set(narratives)) == len(resumes)
    assert all(resume["schema_version"] == RESUME_SCHEMA_VERSION for resume in resumes)
    assert all(resume["audit"]["field_provenance"]["decision_profile"] for resume in resumes)
    assert all(resume["voice"]["example_utterances"] for resume in resumes)

    # Building the same household again is deterministic and auditable.
    persona = json.loads(paths[0].read_text(encoding="utf-8"))
    first = build_household_resume(persona)
    second = build_household_resume(persona)
    assert first["resume_id"] == second["resume_id"]
    assert first["audit"] == second["audit"]


def test_resume_uses_physical_appliance_override_and_bounded_relationship_history() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    physical = {
        "ac": {
            "present": True,
            "setpoint_preferred_min_c": 23.5,
            "setpoint_preferred_max_c": 25.5,
            "temp_tolerance_c": 0.5,
        },
        "ev": {"present": True, "target_soc": 0.9, "departure_h": 6.5},
    }
    history = [
        {
            "id": f"event-{index}",
            "score": index % 5 + 1,
            "comment": f"feedback {index}",
            "method": "MUST_NOT_ENTER_RESUME_HISTORY",
        }
        for index in range(12)
    ]
    resume = build_household_resume(persona, appliance_config=physical, past_events=history)

    commitments = {
        item["device"]: item
        for item in resume["comfort_and_service"]["appliance_commitments"]
    }
    assert commitments["ev"]["target_soc"] == 0.9
    assert commitments["ac"]["setpoint_preferred_max_c"] == 25.5
    assert resume["audit"]["appliance_source"] == "appliance_config_argument"
    assert len(resume["relationship_history"]) == 8
    assert resume["relationship_history"][0]["event_id"] == "event-4"
    assert "MUST_NOT_ENTER_RESUME_HISTORY" not in json.dumps(resume, ensure_ascii=False)


def test_acceptance_prompt_is_model_blind_and_has_one_compact_response_shape() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    system, user, payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        appliance_config=persona["appliances"],
        event={"id": "vpp1", "day": 1, "trigger_h": 18.0, "end_h": 19.0},
        proposed_plan={
            "setpoint": 26.0,
            "reason": (
                "Keep comfort stable and move the washer after the event; "
                "generated by SECRET_AGENT_MODEL with mpc_pdf_v15."
            ),
            "appliance_actions": {
                "washer_start_h": 19.5,
                "washer_skip": False,
                "washer": {
                    "start_h": 19.5,
                    "status": "selected by SECRET_NESTED_MODEL_SENTINEL",
                    "model": "ppo_policy SECRET_NESTED_MODEL_SENTINEL",
                },
                "metadata": {
                    "controller": "SECRET_CONTROLLER_ID",
                    "objective_source": "SECRET_OBJECTIVE_SOURCE",
                },
            },
            "strategy_explanation": {
                "natural_language": "EnergyBridge recommends the later washer time.",
                "recommended_actions": [
                    {
                        "device": "washer",
                        "action": "start at 19:30",
                        "provider": "SECRET_PROVIDER_SENTINEL",
                    }
                ],
            },
            "method": "SECRET_CONTROLLER_ID",
            "model": "SECRET_AGENT_MODEL",
            "objective_source": "SECRET_OBJECTIVE_SOURCE",
        },
        default_plan={
            "setpoint": 25.0,
            "reason": "controller=SECRET_DEFAULT_CONTROLLER",
            "appliance_actions": {"washer_start_h": 19.0},
        },
        past_events=[{
            "id": "prior",
            "comment": "The MPC model made this uncomfortable: SECRET_HISTORY_MODEL_SENTINEL.",
            "appliance_summary": {
                "washer": {
                    "completion_h": 20.0,
                    "source": "SECRET_HISTORY_CONTROLLER_SENTINEL",
                }
            },
        }],
        user_preference_text="I need the home comfortable when I return.",
        baseline_acceptance_probability=0.27,
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["schema_version"] == ACCEPTANCE_SCHEMA_VERSION
    assert payload["persona_baseline_acceptance_probability"] == 0.27
    assert "persona_baseline_audit" not in payload
    assert "audit" not in payload["household_resume"]
    assert "resume_id" not in payload["household_resume"]
    assert "household_id" not in payload["household_resume"]
    assert "display_name" not in payload["household_resume"]
    assert "controller_context_source" not in serialized
    assert "the the plan" not in serialized.lower()
    assert "objective_source" not in serialized
    assert "policy_source" not in serialized
    assert "selected_skill" not in serialized
    assert "response_contract" not in payload
    assert (system + user).count('"decision": "accept|reject|counteroffer"') == 1
    assert "2-4" in system
    assert "at most two short sentences" in user
    assert "exactly one short first-person sentence" in user
    assert payload["offered_vpp_plan"]["appliance_actions"]["washer_start_h"] == 19.5
    assert payload["offered_vpp_plan"]["appliance_actions"]["washer"]["start_h"] == 19.5
    assert "SECRET_CONTROLLER_ID" not in serialized
    assert "SECRET_AGENT_MODEL" not in serialized
    assert "SECRET_OBJECTIVE_SOURCE" not in serialized
    assert "SECRET_NESTED_MODEL_SENTINEL" not in serialized
    assert "SECRET_PROVIDER_SENTINEL" not in serialized
    assert "SECRET_DEFAULT_CONTROLLER" not in serialized
    assert "SECRET_HISTORY_MODEL_SENTINEL" not in serialized
    assert "SECRET_HISTORY_CONTROLLER_SENTINEL" not in serialized
    assert "mpc_dynamic" not in serialized.lower()
    assert "mpc_pdf_v15" not in serialized.lower()
    assert "ppo_policy" not in serialized.lower()
    assert "EnergyBridge recommends" not in serialized
    assert "synthetic_energybridge" not in serialized.lower()
    assert "fixed probability band" not in (system + user).lower()
    assert "probability floor" not in (system + user).lower()
    assert "probability cap" not in (system + user).lower()


def test_roleplay_privacy_projection_blocks_nested_persona_secrets() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    persona["description"] = (
        "Returns home around 18:30 after the late shift. "
        "Utility portal https://utility.example/account is used for bills. "
        "Use https://private.example/v1 with AcmeCloud and CustomSolver. "
        "The backup host is api.internal.example.net:8443/v1 and 10.0.0.4:8080/v1. "
        "provider zetacorp model fooxyz planner bazqux controller gizmo42 "
        "algorithm alphabeta endpoint internalbox:8443/v1 "
        "endpoint [2001:db8::1]:8443/v1. "
        "key ABCDEF123456 api key ZYXWVUT987654 "
        "scoring_weights={comfort:0.99,energy:0.01} "
        "sk-PERSONAFAKEKEY123 Authorization: Bearer bearer.fake.secret "
        "Token: token.fake.secret API_KEY=description-api-secret "
        "endpoint=https://description.private.example/v1 "
        "secret=description-secret-value "
        "-----BEGIN PRIVATE KEY-----\nPEM-DESCRIPTION-SECRET\n"
        "-----END PRIVATE KEY-----"
    )
    persona["llm_prompts"]["system_prompt"] = (
        "I need hot water before 21:00. provider=PRIVATE_PROVIDER_NAME "
        "model=PRIVATE_MODEL_NAME Token first-person-token-secret"
    )
    persona["preferences"].update({
        "baseline_acceptance_probability": 0.91,
        "vpp_acceptance_baseline_probability": 0.92,
        "private": {"note": "PRIVATE_FIELD_SENTINEL"},
        "developer": {"instruction": "DEVELOPER_FIELD_SENTINEL"},
        "evaluator": {"rubric": "EVALUATOR_FIELD_SENTINEL"},
        "APIKey": "sk-NESTEDFAKEKEY123",
        "accessToken": "NESTED_ACCESS_TOKEN_SENTINEL",
        "endpoint": "https://nested.private.example/v1",
        "model": "NESTED_MODEL_SENTINEL",
        "provider": "NESTED_PROVIDER_SENTINEL",
        "method": "NESTED_METHOD_SENTINEL",
        "note_sk-ABCDEFGHIJKLMNO": "ordinary",
        "https://key.private.example/v1": "ordinary",
        "normal_family_facts": {
            "hot_water_note": "Hot water must be ready before 21:00.",
            "bedtime_note": (
                "Bedtime remains 23:00; secret=nested-note-secret-value"
            ),
            "deep": {
                "acceptanceProbability": 0.99,
                "vpp_override_prob": 0.88,
                "api_key": "sk-DEEPFAKEKEY123",
                "token": "DEEP_TOKEN_SENTINEL",
                "base_url": "https://deep.private.example/v1",
                "host": "deep-private-host.example/v1",
                "developerInstructions": "DEEP_DEVELOPER_SENTINEL",
                "evaluatorNotes": "DEEP_EVALUATOR_SENTINEL",
                "household_fact": "Laundry should finish before 22:00.",
            },
        },
    })

    _, user_prompt, payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        baseline_acceptance_probability=0.37,
        user_preference_text=(
            "The utility provider offers a portal at https://live.utility.example/bill, "
            "and the key routine remains visible. "
            "The visible evidence refs are /memory/feedback/id, /profile/capabilities/ev, "
            "and /device_capabilities/washer/earliest_h. Never expose "
            "private.service.academy, memory.private.academy, profile.secret.com, "
            "or endpoint tenant.backend.solutions. "
            "provider livecorp model livexyz planner liveplan controller livecontrol42 "
            "algorithm livealgo endpoint livebox:9443/v2 "
            "endpoint [fd00::1234]:9443/v2"
        ),
    )
    resume = payload["household_resume"]
    serialized_payload = json.dumps(payload, ensure_ascii=False)

    assert payload["persona_baseline_acceptance_probability"] == 0.37
    assert "persona_baseline_audit" not in payload
    assert "Returns home around 18:30 after the late shift." in (
        resume["biography"]["description"]
    )
    assert "Utility portal [private endpoint] is used for bills." in (
        resume["biography"]["description"]
    )
    assert "I need hot water before 21:00." in (
        resume["biography"]["first_person_roleplay_source"]
    )
    assert "utility service company offers a portal at [private endpoint]" in (
        payload["live_household_statement"].lower()
    )
    assert "key routine remains visible" in payload["live_household_statement"]
    assert "/memory/feedback/id" in payload["live_household_statement"]
    assert "/profile/capabilities/ev" in payload["live_household_statement"]
    assert "/device_capabilities/washer/earliest_h" in payload["live_household_statement"]
    for endpoint in (
        "private.service.academy",
        "memory.private.academy",
        "profile.secret.com",
        "tenant.backend.solutions",
    ):
        assert endpoint not in serialized_payload
    projected_preferences = resume["decision_profile"]["other_preferences"]
    assert projected_preferences["normal_family_facts"]["hot_water_note"] == (
        "Hot water must be ready before 21:00."
    )
    assert projected_preferences["normal_family_facts"]["deep"]["household_fact"] == (
        "Laundry should finish before 22:00."
    )

    leaked_values = (
        "sk-PERSONAFAKEKEY123",
        "https://utility.example/account",
        "https://private.example/v1",
        "api.internal.example.net:8443/v1",
        "10.0.0.4:8080/v1",
        "AcmeCloud",
        "CustomSolver",
        "zetacorp",
        "fooxyz",
        "bazqux",
        "gizmo42",
        "alphabeta",
        "internalbox:8443/v1",
        "2001:db8::1",
        "https://live.utility.example/bill",
        "livecorp",
        "livexyz",
        "liveplan",
        "livecontrol42",
        "livealgo",
        "livebox:9443/v2",
        "fd00::1234",
        "ABCDEF123456",
        "ZYXWVUT987654",
        "scoring_weights",
        "bearer.fake.secret",
        "token.fake.secret",
        "description-api-secret",
        "https://description.private.example/v1",
        "description-secret-value",
        "PEM-DESCRIPTION-SECRET",
        "PRIVATE_PROVIDER_NAME",
        "PRIVATE_MODEL_NAME",
        "first-person-token-secret",
        "PRIVATE_FIELD_SENTINEL",
        "DEVELOPER_FIELD_SENTINEL",
        "EVALUATOR_FIELD_SENTINEL",
        "sk-NESTEDFAKEKEY123",
        "NESTED_ACCESS_TOKEN_SENTINEL",
        "https://nested.private.example/v1",
        "NESTED_MODEL_SENTINEL",
        "NESTED_PROVIDER_SENTINEL",
        "NESTED_METHOD_SENTINEL",
        "note_sk-ABCDEFGHIJKLMNO",
        "key.private.example",
        "nested-note-secret-value",
        "sk-DEEPFAKEKEY123",
        "DEEP_TOKEN_SENTINEL",
        "https://deep.private.example/v1",
        "deep-private-host.example/v1",
        "DEEP_DEVELOPER_SENTINEL",
        "DEEP_EVALUATOR_SENTINEL",
    )
    for leaked in leaked_values:
        assert leaked not in serialized_payload
        assert leaked not in user_prompt

    forbidden_resume_keys = {
        "private",
        "developer",
        "evaluator",
        "acceptance_probability",
        "vpp_acceptance_baseline_probability",
        "vpp_override_prob",
        "api_key",
        "access_token",
        "endpoint",
        "base_url",
        "host",
        "audit",
        "resume_id",
        "household_id",
        "display_name",
        "behavioral_dimensions",
        "stable_priorities",
        "scoring_weights",
        "tags",
        "model",
        "provider",
        "method",
    }

    def collect_keys(value: object) -> set[str]:
        if isinstance(value, dict):
            keys = {
                str(key).strip().lower().replace("-", "_").replace(" ", "_")
                for key in value
            }
            for nested in value.values():
                keys.update(collect_keys(nested))
            return keys
        if isinstance(value, list):
            keys: set[str] = set()
            for nested in value:
                keys.update(collect_keys(nested))
            return keys
        return set()

    assert collect_keys(resume).isdisjoint(forbidden_resume_keys)

    direct_projection = sanitize_household_resume_for_roleplay({
        "schema_version": "energybridge.household_resume.v2",
        "household_id": "privacy-test-household",
        "biography": {"description": "A normal household fact; sk-DIRECTFAKEKEY123"},
        "private": {"developer": "DIRECT_PRIVATE_SENTINEL"},
        "nested": {"evaluatorNotes": "DIRECT_EVALUATOR_SENTINEL"},
    })
    direct_serialized = json.dumps(direct_projection, ensure_ascii=False)
    assert "A normal household fact" in direct_serialized
    assert "sk-DIRECTFAKEKEY123" not in direct_serialized
    assert "DIRECT_PRIVATE_SENTINEL" not in direct_serialized
    assert "DIRECT_EVALUATOR_SENTINEL" not in direct_serialized
    assert "privacy-test-household" not in direct_serialized


def test_roleplay_projection_omits_latent_weights_labels_and_hash_side_channels() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    source_resume = build_household_resume(persona)
    _, user_prompt, payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        baseline_acceptance_probability=0.41,
    )
    visible_resume = payload["household_resume"]
    serialized = json.dumps(payload, ensure_ascii=False)
    lowered = serialized.lower()

    for forbidden_key in (
        "stable_priorities",
        "scoring_weights",
        "behavioral_dimensions",
        "decision_weight",
        "audit",
        "resume_id",
        "household_id",
        "display_name",
    ):
        assert forbidden_key not in serialized
        assert forbidden_key not in user_prompt
    for hidden_value in (
        source_resume["resume_id"],
        source_resume["household_id"],
        source_resume["display_name"],
        source_resume["audit"]["profile_fingerprint"],
        source_resume["audit"]["resume_fingerprint"],
    ):
        assert hidden_value not in serialized
        assert hidden_value not in user_prompt
    assert "basic_role_a" not in lowered
    assert "price-cooperative" not in lowered
    assert "cooperative user prototype" not in lowered
    for latent_tag in (
        "regular_commuter",
        "normal_comfort",
        "price_sensitive",
        "suggestion_first",
        "evening_peak",
    ):
        assert latent_tag not in lowered
    assert visible_resume["biography"]["description"]
    assert visible_resume["biography"]["first_person_roleplay_source"]
    assert visible_resume["daily_life"]["schedule"]["returns_home_h"] == 18.5
    assert visible_resume["voice"]["example_utterances"]

    other_persona = _load_persona("basic_role_e_caregiver_low_dr")
    _, _, other_payload = build_roleplay_acceptance_prompts(
        persona_config=other_persona,
        baseline_acceptance_probability=0.41,
    )
    assert visible_resume["biography"] != other_payload["household_resume"]["biography"]


def test_hidden_weight_and_label_changes_do_not_change_model_visible_prompt() -> None:
    left = _load_persona("basic_role_a_commuter_price_cooperative")
    right = json.loads(json.dumps(left))
    left.update({"id": "hidden-archetype-left", "display_name": "Hidden Label Left"})
    right.update({"id": "hidden-archetype-right", "display_name": "Hidden Label Right"})
    left["tags"] = {"latent_group": "left"}
    right["tags"] = {"latent_group": "right"}
    left["preferences"].update({
        "scoring_weights": {"comfort": 0.99, "energy": 0.005, "vpp": 0.005},
        "vpp_override_prob": 0.01,
        "scoring_rubric": "HIDDEN_LEFT_RUBRIC",
    })
    right["preferences"].update({
        "scoring_weights": {"comfort": 0.01, "energy": 0.49, "vpp": 0.50},
        "vpp_override_prob": 0.99,
        "scoring_rubric": "HIDDEN_RIGHT_RUBRIC",
    })

    left_system, left_user, left_payload = build_roleplay_acceptance_prompts(
        persona_config=left,
        baseline_acceptance_probability=0.43,
    )
    right_system, right_user, right_payload = build_roleplay_acceptance_prompts(
        persona_config=right,
        baseline_acceptance_probability=0.43,
    )

    assert left_payload == right_payload
    assert left_system == right_system
    assert left_user == right_user


def test_acceptance_prompt_penalizes_generic_explanations_without_method_targets() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    system, _, payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        appliance_config=persona["appliances"],
        event={"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0},
        proposed_plan={
            "setpoint": 27.5,
            "reason": "Save energy, help the grid, and keep routines unchanged.",
            "appliance_actions": {"washer_start_h": 19.0},
            "method": "SECRET_METHOD_SENTINEL",
            "model": "SECRET_MODEL_SENTINEL",
        },
        default_plan={
            "setpoint": 26.0,
            "appliance_actions": {"washer_start_h": 19.0},
        },
    )
    instruction = system.lower()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "even when the household is generally cooperative" in instruction
    assert "compare offered_vpp_plan with ordinary_household_plan" in instruction
    assert "benefit or tradeoff" in instruction
    assert "let the explanation materially raise willingness" in instruction
    assert "communication value separately from any correctable physical drawback" in instruction
    assert "merely preserving ordinary comfort, safety, or service earns no extra credit" in instruction
    assert "inherited ordinary actions are context, not new benefits" in instruction
    assert "generic claims" in instruction
    assert "should lower willingness" in instruction
    assert "incomplete request for consent" in instruction
    assert "not positive evidence while that condition is still unmet" in instruction
    assert "an empty conflict list" in instruction
    assert "context_only unless this offer improves them" in instruction
    assert "in 100 comparable situations" in instruction
    assert "prior is a starting point, not a floor" in instruction
    assert "do not apply hidden clipping or canned deltas" in instruction
    assert "decimal probability unit" in instruction
    assert "never as a percentage or percentage-point" in instruction
    assert "explanation specificity" not in instruction
    assert "one explicit negative" not in instruction
    assert "largest-magnitude" not in instruction
    assert "target acceptance rate" not in instruction
    assert "SECRET_METHOD_SENTINEL" not in serialized
    assert "SECRET_MODEL_SENTINEL" not in serialized


def test_acceptance_prompt_treats_conditions_and_unverified_plan_claims_as_negative() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    system, _, payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        appliance_config=persona["appliances"],
        event={"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0},
        proposed_plan={
            "setpoint": 27.5,
            "reason": "The washer is moved outside the event and every routine is protected.",
            "appliance_actions": {"washer_start_h": 18.5},
        },
        default_plan={
            "setpoint": 26.0,
            "appliance_actions": {"washer_start_h": 17.0},
        },
        user_preference_text=(
            "I will participate only if the washer runs outside the event, and as long as it finishes on time."
        ),
        verified_plan_facts={
            "proposed_setpoint_c": 27.5,
            "vpp_conflicts": ["washer: 18:30 start overlaps VPP 18:00-19:00"],
            "present_services": ["washer"],
            "specified_services": ["washer"],
            "unspecified_services": [],
            "method": "SECRET_METHOD_SENTINEL",
            "model": "SECRET_MODEL_SENTINEL",
        },
    )
    instruction = system.lower()

    assert "unresolved 'if/only if/as long as' conditions" in instruction
    assert "credits it only in the counterfactual" in instruction
    assert "check times and claims against the event, both plans, and verified_offer_facts" in instruction
    assert "exclusive event end is outside the event" in instruction
    assert "do not invent savings, guarantees, outcomes, failures, or personal details" in instruction
    assert payload["event"]["trigger_h"] == 18.0
    assert payload["event"]["end_h"] == 19.0
    assert payload["event"]["trigger_hod"] == 18.0
    assert payload["event"]["end_hod"] == 19.0
    assert "[trigger_hod, end_hod)" in payload["event"]["window_semantics"]
    assert payload["ordinary_household_plan"]["appliance_actions"]["washer_start_h"] == 17.0
    assert payload["offered_vpp_plan"]["appliance_actions"]["washer_start_h"] == 18.5
    assert "only if" in payload["live_household_statement"]
    assert "as long as" in payload["live_household_statement"]
    assert payload["verified_offer_facts"] == {
        "proposed_setpoint_c": 27.5,
        "vpp_conflicts": ["washer: 18:30 start overlaps VPP 18:00-19:00"],
        "present_services": ["washer"],
        "specified_services": ["washer"],
        "unspecified_services": [],
        "event_overlap_note": (
            "vpp_conflicts is the checked overlap result under the event's half-open interval. An empty list means "
            "none of the supplied effective appliance intervals overlaps this event."
        ),
    }
    assert "SECRET_METHOD_SENTINEL" not in json.dumps(payload, ensure_ascii=False)
    assert "SECRET_MODEL_SENTINEL" not in json.dumps(payload, ensure_ascii=False)


def test_verified_missing_service_actions_are_explained_without_controller_identity() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    _, _, payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        proposed_plan={"setpoint": 26.5, "appliance_actions": {}},
        verified_plan_facts={
            "present_services": ["washer", "water_heater"],
            "specified_services": [],
            "unspecified_services": ["washer", "water_heater"],
            "method": "SECRET_METHOD_SENTINEL",
        },
    )

    facts = payload["verified_offer_facts"]
    assert facts["present_services"] == ["washer", "water_heater"]
    assert facts["specified_services"] == []
    assert facts["unspecified_services"] == ["washer", "water_heater"]
    assert "does not explicitly cover these present household services" in facts["action_coverage_note"]
    assert "SECRET_METHOD_SENTINEL" not in json.dumps(payload, ensure_ascii=False)


def test_sparse_offer_inherits_ordinary_actions_without_format_penalty() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    _, _, payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        default_plan={
            "setpoint": 25.5,
            "reason": "The ordinary-plan explanation must not become an offer explanation.",
            "appliance_actions": {
                "washer_start_h": 20.0,
                "washer_skip": False,
                "water_heater_preheat_start_h": 16.0,
                "water_heater_preheat_end_h": 18.0,
            },
        },
        proposed_plan={
            "setpoint": 26.0,
            "appliance_actions": {"washer_start_h": 21.0},
            "reason": "OpenAI o3 via DMXAPI produced this DeepSeek-V3 plan.",
        },
        verified_plan_facts={
            "proposed_setpoint_c": 26.0,
            "default_setpoint_c": 25.5,
            "preferred_min_c": 24.0,
            "preferred_max_c": 26.0,
            "preference_tolerance_c": 1.0,
            "comfort_excess_c": 99.0,
            "default_delta_c": 99.0,
            "hvac_off": True,
            "present_services": ["washer", "water_heater"],
            "specified_services": ["washer", "water_heater"],
            "unspecified_services": [],
            "vpp_conflicts": [],
        },
    )

    offered = payload["offered_vpp_plan"]
    effective = payload["effective_plan_if_accepted"]
    assert offered["setpoint_c"] == 26.0
    assert offered["appliance_actions"]["washer_start_h"] == 21.0
    assert "washer_skip" not in offered["appliance_actions"]
    assert "water_heater_preheat_start_h" not in offered["appliance_actions"]
    assert effective["setpoint_c"] == 26.0
    assert effective["appliance_actions"]["washer_start_h"] == 21.0
    assert effective["appliance_actions"]["washer_skip"] is False
    assert effective["appliance_actions"]["water_heater_preheat_start_h"] == 16.0
    assert "inherit the ordinary household plan" in effective["plan_field_semantics"]
    assert effective["reason_shown_to_household"] == offered["reason_shown_to_household"]
    assert "ordinary-plan explanation" not in effective["reason_shown_to_household"]
    facts = payload["verified_offer_facts"]
    assert facts["preferred_min_c"] == 24.0
    assert facts["preference_tolerance_c"] == 1.0
    assert "comfort_excess_c" not in facts
    assert "default_delta_c" not in facts
    assert "hvac_off" not in facts
    assert facts["vpp_conflicts"] == []
    assert "empty list means none" in facts["event_overlap_note"]
    assert facts["setpoint_change_c"] == 0.5
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    for identity in ("openai", "o3", "dmxapi", "deepseek"):
        assert identity not in serialized

    _, _, no_explanation_payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        default_plan={"setpoint": 25.5, "reason": "Ordinary comfort explanation."},
        proposed_plan={"setpoint": 26.0},
    )
    assert "reason_shown_to_household" not in no_explanation_payload[
        "effective_plan_if_accepted"
    ]


def test_persona_prior_is_neutral_without_relabelling_other_persona_weights() -> None:
    a = _load_persona("basic_role_a_commuter_price_cooperative")
    e = _load_persona("basic_role_e_caregiver_low_dr")
    _, _, payload_a = build_roleplay_acceptance_prompts(persona_config=a)
    _, _, payload_e = build_roleplay_acceptance_prompts(persona_config=e)

    assert payload_a["persona_baseline_acceptance_probability"] == pytest.approx(0.5)
    assert payload_e["persona_baseline_acceptance_probability"] == pytest.approx(0.5)
    assert "persona_baseline_audit" not in payload_a
    assert "persona_baseline_audit" not in payload_e
    assert "audit" not in payload_a["household_resume"]
    assert "audit" not in payload_e["household_resume"]
    assert payload_a["household_resume"]["biography"] != (
        payload_e["household_resume"]["biography"]
    )


def test_normalizer_preserves_arithmetic_model_probability_without_reshaping() -> None:
    normalized = normalize_roleplay_acceptance_response(
        _valid_response(),
        expected_baseline=0.37,
    )

    assert normalized["decision"] == "counteroffer"
    assert normalized["baseline_acceptance_probability"] == 0.37
    assert normalized["final_acceptance_probability"] == 0.32
    assert normalized["acceptance_probability"] == 0.32
    assert normalized["confidence"] == 0.74
    assert normalized["adjustments"][0]["delta"] == -0.08
    assert normalized["counterfactual"]["decision_if_changed"] == "accept"
    assert normalized["normalization"]["adjustment_sum"] == pytest.approx(-0.05)
    assert normalized["normalization"]["arithmetic_residual"] == pytest.approx(0.0)
    assert normalized["normalization"]["expected_baseline"] == 0.37
    assert normalized["hard_veto_applied"] is False


def test_normalizer_sanitizes_model_authored_text_and_preserves_evidence_links() -> None:
    response = _valid_response()
    response["adjustments"][0].update({
        "dimension": "arrival comfort planner outputplan",
        "reason": "Arrival comfort matters; model outputxyz endpoint outputbox:8555/v1.",
    })
    response["adjustments"][1]["reason"] = (
        "The washer remains usable; provider outputcorp."
    )
    response["evidence"][0]["fact"] = (
        "Arrival comfort is important; sk-OUTPUTFAKEKEY123 "
        "provider evidencecorp endpoint evidencebox:8666/v1."
    )
    response["evidence"][1]["fact"] = (
        "The washer stays scheduled and the key routine remains unchanged."
    )
    response["counterfactual"] = {
        "changes": [
            "Restore the ordinary setpoint; controller outputcontrol "
            "endpoint [fd00::77]:8555/v1."
        ],
        "decision_if_changed": "accept",
        "acceptance_probability_if_changed": 0.81,
        "reason": "That protects hot water; algorithm outputalgo.",
    }
    response["reason"] = (
        "I need arrival comfort. Authorization: Bearer output.bearer.secret"
    )
    response["user_feedback"] = (
        "Keep my utility provider portal utility.output.example/bill and key routine; "
        "api key OUTPUTKEY123456."
    )

    normalized = normalize_roleplay_acceptance_response(
        response,
        expected_baseline=0.37,
    )
    serialized = json.dumps(normalized, ensure_ascii=False)
    lowered = serialized.lower()

    for leaked in (
        "sk-OUTPUTFAKEKEY123",
        "outputplan",
        "outputxyz",
        "outputbox:8555/v1",
        "outputcorp",
        "evidencecorp",
        "evidencebox:8666/v1",
        "outputcontrol",
        "fd00::77",
        "outputalgo",
        "output.bearer.secret",
        "utility.output.example/bill",
        "OUTPUTKEY123456",
    ):
        assert leaked.lower() not in lowered
    assert [item["id"] for item in normalized["evidence"]] == ["E1", "E2"]
    assert [item["evidence"] for item in normalized["adjustments"]] == ["E1", "E2"]
    assert "arrival comfort is important" in normalized["evidence"][0]["fact"].lower()
    assert "key routine remains unchanged" in normalized["evidence"][1]["fact"].lower()
    assert "arrival comfort" in normalized["reason"].lower()
    assert "utility service company portal [private endpoint]" in (
        normalized["user_feedback"].lower()
    )
    assert "key routine" in normalized["user_feedback"].lower()


def test_explanation_quality_adjustment_remains_model_authored() -> None:
    response = _valid_response(
        decision="counteroffer",
        baseline_acceptance_probability=0.62,
        adjustments=[{
            "dimension": "proposal evidence quality",
            "delta": -0.29,
            "evidence": "E1",
            "reason": "The offer gives no household-specific benefit or schedule rationale.",
        }],
        final_acceptance_probability=0.33,
    )
    normalized = normalize_roleplay_acceptance_response(
        response,
        expected_baseline=0.62,
    )

    assert normalized["baseline_acceptance_probability"] == 0.62
    assert normalized["adjustments"] == response["adjustments"]
    assert normalized["normalization"]["adjustment_sum"] == pytest.approx(-0.29)
    assert normalized["final_acceptance_probability"] == 0.33
    assert normalized["acceptance_probability"] == 0.33


def test_normalizer_allows_no_change_but_rejects_mismatch_zero_item_and_unexplained_final() -> None:
    with pytest.raises(RoleplayResponseError, match="supplied persona baseline"):
        normalize_roleplay_acceptance_response(
            _valid_response(baseline_acceptance_probability=0.44, final_acceptance_probability=0.41),
            expected_baseline=0.37,
        )

    unchanged = normalize_roleplay_acceptance_response(
        _valid_response(adjustments=[], final_acceptance_probability=0.37),
        expected_baseline=0.37,
    )
    assert unchanged["adjustments"] == []
    assert unchanged["final_acceptance_probability"] == 0.37
    assert unchanged["normalization"]["adjustment_sum"] == 0.0

    with pytest.raises(RoleplayResponseError, match="non-zero signed adjustment"):
        normalize_roleplay_acceptance_response(
            _valid_response(
                adjustments=[{
                    "dimension": "no material change",
                    "delta": 0.0,
                    "evidence": "E1",
                    "reason": "Nothing changed.",
                }],
                final_acceptance_probability=0.37,
            ),
            expected_baseline=0.37,
        )

    with pytest.raises(RoleplayResponseError, match="baseline plus signed adjustments"):
        normalize_roleplay_acceptance_response(
            _valid_response(final_acceptance_probability=0.613),
            expected_baseline=0.37,
        )


def test_normalizer_rejects_adjustment_evidence_sign_contradictions() -> None:
    cases = (
        ("contradictory positive", 0.08, "E1", 0.45),
        ("contradictory negative", -0.06, "E2", 0.31),
    )
    for dimension, delta, evidence, probability in cases:
        with pytest.raises(RoleplayResponseError, match="same direction"):
            normalize_roleplay_acceptance_response(
                _valid_response(
                    adjustments=[{
                        "dimension": dimension,
                        "delta": delta,
                        "evidence": evidence,
                        "reason": "The cited evidence points the other way.",
                    }],
                    final_acceptance_probability=probability,
                ),
                expected_baseline=0.37,
            )


def test_verified_fact_validator_rejects_false_appliance_overlap_without_reshaping() -> None:
    response = normalize_roleplay_acceptance_response(
        _valid_response(
            adjustments=[{
                "dimension": "appliance timing",
                "delta": -0.04,
                "evidence": "E1",
                "reason": "The washer runs during the avoided hour.",
            }],
            final_acceptance_probability=0.33,
        ),
        expected_baseline=0.37,
    )
    with pytest.raises(RoleplayResponseError, match="verified empty"):
        validate_roleplay_response_against_verified_facts(
            response,
            {"vpp_conflicts": []},
        )


def test_verified_fact_validator_preserves_truthful_model_judgement() -> None:
    response = normalize_roleplay_acceptance_response(
        _valid_response(),
        expected_baseline=0.37,
    )
    validated = validate_roleplay_response_against_verified_facts(
        response,
        {"vpp_conflicts": []},
    )
    assert validated["final_acceptance_probability"] == 0.32
    assert validated["adjustments"] == response["adjustments"]
    assert validated["normalization"]["verified_fact_consistent"] is True
    assert validated["normalization"]["verified_fact_checks"] == [
        "empty_appliance_event_conflicts"
    ]

    explicitly_truthful = _valid_response()
    explicitly_truthful["reason"] = "The washer does not run during the event window."
    explicitly_truthful = normalize_roleplay_acceptance_response(
        explicitly_truthful,
        expected_baseline=0.37,
    )
    assert validate_roleplay_response_against_verified_facts(
        explicitly_truthful,
        {"vpp_conflicts": []},
    )["final_acceptance_probability"] == 0.32


def test_verified_fact_validator_rejects_unneeded_move_out_counterfactual() -> None:
    response = _valid_response()
    response["counterfactual"] = {
        "changes": ["Shift the washer out of 18:00-19:00."],
        "decision_if_changed": "accept",
        "acceptance_probability_if_changed": 0.72,
        "reason": "That would avoid the event window.",
    }
    normalized = normalize_roleplay_acceptance_response(
        response,
        expected_baseline=0.37,
    )
    with pytest.raises(RoleplayResponseError, match="verified empty"):
        validate_roleplay_response_against_verified_facts(
            normalized,
            {"vpp_conflicts": []},
        )


def test_normalizer_rejects_duplicate_evidence_ids() -> None:
    response = _valid_response()
    response["evidence"][1]["id"] = "e1"
    with pytest.raises(RoleplayResponseError, match="evidence ids must be unique"):
        normalize_roleplay_acceptance_response(
            response,
            expected_baseline=0.37,
        )


def test_multiday_event_exposes_local_hour_of_day_for_action_comparison() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    _, _, payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        event={"id": "day2-vpp", "day": 2, "trigger_h": 42.0, "end_h": 43.0},
        proposed_plan={"appliance_actions": {"washer_start_h": 19.0}},
    )

    event = payload["event"]
    assert event["trigger_h"] == 42.0
    assert event["end_h"] == 43.0
    assert event["trigger_hod"] == 18.0
    assert event["end_hod"] == 19.0
    assert "absolute simulation hours" in event["window_semantics"]


def test_normalizer_allows_only_ordinary_decimal_rounding_residual() -> None:
    response = _valid_response(
        baseline_acceptance_probability=0.3137,
        adjustments=[{
            "dimension": "routine fit",
            "delta": 0.041,
            "evidence": "E2",
            "reason": "The later start preserves the routine.",
        }],
        final_acceptance_probability=0.355,
    )
    normalized = normalize_roleplay_acceptance_response(
        response,
        expected_baseline=0.314,
    )
    assert normalized["final_acceptance_probability"] == 0.355
    assert abs(normalized["normalization"]["arithmetic_residual"]) < (
        PROBABILITY_ROUNDING_TOLERANCE
    )


def test_normalizer_requires_structured_fields_and_uses_only_caller_fallback() -> None:
    with pytest.raises(RoleplayResponseError):
        normalize_roleplay_acceptance_response({"decision": "accept"})

    fallback = _valid_response(
        decision="reject",
        reason="The proposed temperature conflicts with my stated boundary.",
    )
    normalized = normalize_roleplay_acceptance_response(
        "not json",
        fallback=fallback,
        expected_baseline=0.37,
    )

    assert normalized["decision"] == "reject"
    assert normalized["final_acceptance_probability"] == 0.32
    assert normalized["normalization"]["source"] == "caller_fallback"
    assert "roleplay_error" in normalized["normalization"]


def test_hard_safety_veto_is_the_only_probability_override() -> None:
    normalized = normalize_roleplay_acceptance_response(
        _valid_response(
            decision="accept",
            adjustments=[{
                "dimension": "service reliability",
                "delta": 0.54,
                "evidence": "E2",
                "reason": "Every required service remains ready.",
            }],
            final_acceptance_probability=0.91,
        ),
        hard_veto_reasons=["EV cannot reach the required departure SOC."],
        expected_baseline=0.37,
    )

    assert normalized["decision"] == "reject"
    assert normalized["final_acceptance_probability"] == 0.0
    assert normalized["acceptance_probability"] == 0.0
    assert normalized["roleplay_decision_before_veto"] == "accept"
    assert normalized["roleplay_final_acceptance_probability_before_veto"] == 0.91
    assert normalized["hard_veto_applied"] is True
    assert normalized["hard_veto_reasons"] == ["EV cannot reach the required departure SOC."]
    assert normalized["evidence"][-1]["source"] == "hard_safety_veto"


def test_hard_veto_can_return_a_structured_rejection_when_model_output_is_invalid() -> None:
    normalized = normalize_roleplay_acceptance_response(
        "invalid response",
        hard_veto_reasons=["Unsafe thermostat command."],
    )

    assert normalized["decision"] == "reject"
    assert normalized["final_acceptance_probability"] == 0.0
    assert normalized["counterfactual"]["changes"]
    assert normalized["normalization"]["source"] == "hard_safety_veto"
    assert "roleplay_error" in normalized["normalization"]


def test_observable_onboarding_inference_uses_only_public_answer_selections() -> None:
    answers = [
        {
            "id": "vpp_priority",
            "selected_option_ids": ["bill_savings_first"],
            "answer": "Show me a concrete saving while keeping the home comfortable.",
        },
        {
            "id": "thermostat_flexibility",
            "selected_option_ids": ["small_1c_short"],
            "answer": "About one degree for a short event is fine.",
        },
        {
            "id": "appliance_shift_consent",
            "selected_option_ids": ["do_not_move_without_approval"],
            "answer": "Please ask before moving a task.",
        },
        {
            "id": "calendar_routine_constraints",
            "selected_option_ids": ["irregular_confirm_same_day"],
            "answer": "My plans change, so check the same day.",
        },
    ]
    inferred = infer_observable_profile_from_answers(answers)

    assert inferred["inferred_profile"]["cost_grid_priority"] == "high"
    assert inferred["inferred_profile"]["thermostat_flexibility_c"] == 1.0
    assert inferred["inferred_profile"]["automation_preference"] == (
        "ask_before_vpp_specific_changes"
    )
    assert inferred["inferred_profile"]["calendar_routine_sensitivity"] == "high"
    assert inferred["preference_rules"]
    assert inferred["inference_audit"]["hidden_resume_fields_used"] is False


def test_v2_onboarding_drops_hidden_prompt_and_llm_authored_profile(monkeypatch) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    captured: dict[str, str] = {}
    simulator = object.__new__(RoleplayUserSimulator)

    def fake_call(system_prompt: str, user_prompt: str) -> dict:
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return {
            "data": {
                "answers": [
                    {
                        "id": "vpp_priority",
                        "selected_option_ids": ["balanced_tradeoff", "high_trust_auto"],
                        "answer": "My scoring_weights are SECRET_HIDDEN_PROFILE_SENTINEL.",
                    },
                    {
                        "id": "appliance_shift_consent",
                        "selected_option_ids": ["shift_1_2h_deadline_protected"],
                        "answer": "A short shift is fine if the task finishes.",
                    },
                ],
                "inferred_profile": {"leak": "SECRET_LLM_INFERRED_PROFILE"},
                "preference_rules": ["SECRET_LLM_RULE"],
            },
            "raw_response": "SECRET_RAW_ROLEPLAY_RESPONSE",
            "system_prompt": "SECRET_RETURNED_SYSTEM_PROMPT",
            "user_prompt": "SECRET_RETURNED_USER_PROMPT",
            "metrics": {"used": True, "model": "roleplay-test"},
        }

    monkeypatch.setattr(simulator, "_call_json", fake_call)
    result = simulator.answer_onboarding_questions(
        persona={
            "id": "private-household",
            "description": "SECRET_PERSONA_DESCRIPTION",
            "preferences": {
                "scoring_weights": {"comfort": 0.9, "energy": 0.05, "vpp": 0.05},
                "vpp_override_prob": 0.9,
            },
        },
        questions=[
            {"id": "vpp_priority", "question": "What matters?", "options": []},
            {"id": "appliance_shift_consent", "question": "May tasks move?", "options": []},
        ],
    )
    serialized = json.dumps(result, ensure_ascii=False)

    assert "only `answers`" in captured["user"]
    assert "inferred_profile" in captured["user"]  # present only in the prohibition
    assert set(result) == {"data", "metrics", "privacy"}
    assert result["data"]["inference_audit"]["hidden_resume_fields_used"] is False
    assert result["data"]["inferred_profile"]["strategy_bias"] == "balanced_middle"
    assert result["data"]["inferred_profile"]["appliance_flexibility"] == (
        "shift_1_2h_if_deadlines_protected"
    )
    assert result["privacy"]["hidden_resume_returned"] is False
    assert "high_trust_auto" not in serialized
    assert "SECRET" not in serialized


def test_v2_context_clarification_returns_only_sanitized_natural_answer(monkeypatch) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    captured: dict[str, str] = {}
    simulator = object.__new__(RoleplayUserSimulator)

    def fake_call(system_prompt: str, user_prompt: str) -> dict:
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return {
            "data": {
                "answer": (
                    "Please keep the 21:00 shower. provider zetacorp model fooxyz "
                    "key sk-ABCDEFGHIJKLMNOPQRSTUV endpoint internalbox:8443/v1"
                ),
                "certainty": "conditional",
                "conditions": "I can shift laundry if hot water remains ready.",
            },
            "raw_response": "SECRET_RAW_CONTEXT_REPLY",
            "system_prompt": "SECRET_CONTEXT_SYSTEM",
            "user_prompt": "SECRET_CONTEXT_USER",
            "metrics": {"used": True},
        }

    monkeypatch.setattr(simulator, "_call_json", fake_call)
    result = simulator.answer_context_question(
        persona={
            "id": "hidden-household-id",
            "description": "I shower at 21:00 and usually wash clothes after dinner.",
            "preferences": {
                "scoring_weights": {"comfort": 0.9},
                "api_key": "sk-HIDDENPERSONAKEY123456",
            },
        },
        question="Would shifting laundry past 19:00 disrupt tonight?",
        scenario={"event_window": "18:00-19:00", "providerName": "hidden-provider"},
    )
    serialized = json.dumps(result, ensure_ascii=False).lower()
    prompt = (captured["system"] + captured["user"]).lower()

    assert set(result) == {"data", "metrics", "privacy"}
    assert result["data"]["certainty"] == "conditional"
    assert "21:00 shower" in result["data"]["answer"]
    assert "zetacorp" not in serialized
    assert "fooxyz" not in serialized
    assert "abcdefghijkl" not in serialized
    assert "internalbox" not in serialized
    assert "hidden-provider" not in prompt
    assert "scoring_weights" not in prompt
    assert result["privacy"]["raw_roleplay_response_returned"] is False


def test_v2_feedback_prompt_sanitizes_resume_provenance_and_keeps_household_facts(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    captured: dict[str, str] = {}
    simulator = object.__new__(RoleplayUserSimulator)

    def fake_call(system_prompt: str, user_prompt: str) -> dict:
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return {
            "data": {
                "satisfaction_score": 4,
                "comfort_score": 4,
                "energy_score": 3,
                "vpp_score": 4,
                "satisfaction_label": "satisfied",
                "comment": "The plan protected my evening shower.",
            },
            "metrics": {"used": True},
        }

    monkeypatch.setattr(simulator, "_call_json", fake_call)
    simulator.generate_feedback(
        persona={
            "id": "feedback-household",
            "description": (
                "I need hot water for my evening shower. "
                "OpenAI SECRET_FEEDBACK_MODEL_SENTINEL must not appear."
            ),
            "agent_context": "ideal DR candidate for EnergyBridge evaluation tests",
            "calendar": {
                "source": "synthetic_energybridge_private_calendar",
                "days": [],
            },
        },
        turn_index=1,
        selected_strategy={"pre_event_roleplay_acceptance": {"acceptance_probability": 0.63}},
        projected_control_plan={"setpoint": 25.0},
        projected_safety_report={"status": "approved"},
    )

    prompt = captured["system"] + captured["user"]
    lowered = prompt.lower()
    assert "evening shower" in lowered
    assert "openai" not in lowered
    assert "energybridge" not in lowered
    assert "synthetic_energybridge" not in lowered
    assert "secret_feedback_model_sentinel" not in lowered
    assert "ideal dr candidate" not in lowered
    assert "controller_context_source" not in lowered


def test_roleplay_json_transport_is_adaptive_only(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeClient:
        def chat_with_metrics(self, system_prompt, user_prompt, **kwargs):
            calls.append({
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                **kwargs,
            })
            return {"text": '{"ok":true}', "metrics": {}}

    simulator = object.__new__(RoleplayUserSimulator)
    simulator.client = FakeClient()

    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    assert simulator._call_json("system", "user")["data"] == {"ok": True}
    assert calls[-1]["response_format"] == {"type": "json_object"}

    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "legacy_v1")
    assert simulator._call_json("system", "user")["data"] == {"ok": True}
    assert calls[-1]["response_format"] is None
