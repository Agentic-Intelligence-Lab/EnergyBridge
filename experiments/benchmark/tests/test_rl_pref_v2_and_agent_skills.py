from __future__ import annotations

from types import SimpleNamespace

from experiments.benchmark import family_runner
from experiments.benchmark.baselines.rl_ppo_pref_v2 import MODEL_ENV_VAR, resolve_model_path
from experiments.benchmark.family_runner import (
    _agent_rule_milp_hvac_feedback_adjustment_c,
    _multi_user_household_comfort_first_mode,
    _requested_agent_skill_names,
)
from experiments.benchmark.run_baseline_matrix import (
    DEFAULT_METHODS as PERSONAL_DEFAULT_METHODS,
    ENERGYBRIDGE_METHOD_ID,
    METHOD_CHOICES,
    _canonical_method,
)
from experiments.benchmark.run_household_matrix import DEFAULT_METHODS as HOUSEHOLD_DEFAULT_METHODS


def test_default_matrix_uses_rl_pref_v2_and_no_old_rl() -> None:
    assert "rl_ppo_pref_v2" in PERSONAL_DEFAULT_METHODS
    assert "rl_ppo_pref_v2" in HOUSEHOLD_DEFAULT_METHODS
    assert "rl_ppo_3day" not in METHOD_CHOICES
    assert "rl_ppo_3day" not in PERSONAL_DEFAULT_METHODS
    assert "rl_ppo_3day" not in HOUSEHOLD_DEFAULT_METHODS


def test_rl_pref_v2_resolves_region_specific_checkpoints(monkeypatch) -> None:
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    assert resolve_model_path("Germany").name == "rl_ppo_pref_v2_germany.zip"
    assert resolve_model_path("Tianjin").name == "rl_ppo_pref_v2_tianjin.zip"


def test_agent_skill_requests_are_llm_directed_and_exclude_rl() -> None:
    request = {
        "skill_calls": ["mpc", "rule+milp", "dynamics", "rl_ppo_pref_v2", "dynamic_hvac"],
    }
    assert _requested_agent_skill_names(request) == [
        "mpc_dynamic",
        "rule_milp",
        "dynamic_hvac",
    ]


def test_legacy_hybrid_alias_maps_to_energybridge() -> None:
    assert _canonical_method("eb_rule_milp") == ENERGYBRIDGE_METHOD_ID
    assert _canonical_method("agent+milp") == ENERGYBRIDGE_METHOD_ID


def test_agent_rule_milp_skill_uses_standalone_rule_state(monkeypatch) -> None:
    captured: dict[str, bool] = {}

    def fake_plan_rule_milp_action(*, state, price_profile=None, run_start_date=None):
        captured["standalone"] = bool(state.get("standalone_baseline"))
        return {
            "setpoint": 28.0,
            "appliances": {"washer_start_h": 8.0, "washer_skip": False},
            "objective_terms": {"total": 1.0},
            "reason": "fake rule milp",
        }

    def fake_plan_rule_milp_options(*, state, price_profile=None, run_start_date=None, max_options=5):
        captured["options_standalone"] = bool(state.get("standalone_baseline"))
        return {"strategy_options": []}

    monkeypatch.setattr(
        "experiments.benchmark.baselines.rule_milp.plan_rule_milp_action",
        fake_plan_rule_milp_action,
    )
    monkeypatch.setattr(
        "experiments.benchmark.baselines.rule_milp.plan_rule_milp_options",
        fake_plan_rule_milp_options,
    )

    loop = SimpleNamespace(
        sp=25.0,
        appliance_suite=None,
        weather_label="Tianjin",
        current_occupied=True,
        current_occupancy_count=1.0,
        current_occupancy_source="test",
        sim_days=1,
        vpp_event_log=[],
    )
    bundle = family_runner._build_agent_skill_bundle(
        loop,
        sim_h=0.0,
        hod=0.0,
        temp=25.0,
        out_t=30.0,
        facility_w=1000.0,
        vpp_event=None,
        appliance_config={"washer": {"present": True}, "ac": {"setpoint_preferred_min_c": 24.0, "setpoint_preferred_max_c": 26.0}},
        price_profile=None,
        run_start_date=None,
        idf_path="",
        epw_path="",
        mpc_horizon_steps=6,
        sp_min=23.0,
        sp_max=40.0,
        requested_skills=["rule_milp"],
    )

    assert captured == {"standalone": True, "options_standalone": True}
    assert bundle["skills"]["rule_milp"]["setpoint"] == 28.0


def test_agent_rule_milp_feedback_adjustment_is_energy_bounded() -> None:
    events = [
        {
            "setpoint": 40.0,
            "comfort_score": 1,
            "comment": "It got too warm during the event.",
        }
    ]

    assert (
        _agent_rule_milp_hvac_feedback_adjustment_c(
            events,
            preferred_max_c=26.0,
            run_sp_min_c=23.0,
            run_sp_max_c=40.0,
        )
        == 28.0
    )


def test_agent_rule_milp_repeated_comfort_complaints_restore_preferred_cap() -> None:
    events = [
        {
            "setpoint": 40.0,
            "comfort_score": 1,
            "comment": "It got too warm during the event.",
        },
        {
            "setpoint": 28.0,
            "comfort_score": 2,
            "comment": "The same comfort issue is still unresolved.",
        },
    ]

    assert (
        _agent_rule_milp_hvac_feedback_adjustment_c(
            events,
            preferred_max_c=26.0,
            run_sp_min_c=23.0,
            run_sp_max_c=40.0,
        )
        == 26.0
    )


def test_multi_user_household_comfort_first_mode_is_household_only() -> None:
    assert _multi_user_household_comfort_first_mode(
        {"meta": {"persona_type": "multi_user_household_independent_roleplay"}}
    )
    assert not _multi_user_household_comfort_first_mode(
        {"meta": {"persona_type": "single_user_roleplay"}}
    )
