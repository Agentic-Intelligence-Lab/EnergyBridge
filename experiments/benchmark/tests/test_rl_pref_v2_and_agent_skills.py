from __future__ import annotations

from experiments.benchmark.baselines.rl_ppo_pref_v2 import MODEL_ENV_VAR, resolve_model_path
from experiments.benchmark.family_runner import _requested_agent_skill_names
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
