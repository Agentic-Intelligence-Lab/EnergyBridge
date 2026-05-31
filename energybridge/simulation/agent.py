"""Agent simulation object wrapping the EnergyBridge graph and skills."""

from __future__ import annotations

from copy import deepcopy

from energybridge.agent.graph import build_energybridge_graph
from energybridge.control.fallback_controller import fallback_control_plan
from energybridge.control.mock_mpc import run_mock_mpc
from energybridge.control.safety_checker import validate_safety
from energybridge.llm.strategy_advisor import generate_strategy_options
from energybridge.agent.nodes import node_translate_grid
from energybridge.skills.preference_parser import merge_preferences_with_memory, parse_user_preference
from energybridge.skills.strategy_generator import generate_candidate_strategy
from energybridge.utils.config import load_llm_config


class AgentSimulator:
    """Prepare strategy options and invoke the EnergyBridge agent graph."""

    def __init__(self) -> None:
        self.app = build_energybridge_graph()
        self.strategy_llm_config = load_llm_config()

    def parse_preferences(self, user_input: str, memory_snapshot: dict) -> dict:
        parsed = parse_user_preference(user_input)
        return merge_preferences_with_memory(parsed, memory_snapshot)

    def build_fallback_strategy_options(self, base_strategy: dict) -> list[dict]:
        comfort_option = dict(base_strategy)
        comfort_option.update(
            {
                "title": "Comfort First",
                "mode": "comfort",
                "recommended_setpoint": max(24.0, float(base_strategy.get("recommended_setpoint", 25.0)) - 0.3),
                "pre_cooling": False,
                "expected_user_impact": "minimal",
                "rationale": ["Keep indoor comfort as the primary objective."],
                "source": "fallback",
            }
        )

        balanced_option = dict(base_strategy)
        balanced_option.update(
            {
                "title": "Balanced",
                "mode": base_strategy.get("mode", "balanced"),
                "rationale": base_strategy.get("rationale", ["Balance comfort and grid needs."]),
                "source": "fallback",
            }
        )

        support_option = dict(base_strategy)
        support_option.update(
            {
                "title": "Grid Support",
                "mode": "grid_support",
                "recommended_setpoint": min(27.0, float(base_strategy.get("recommended_setpoint", 25.0)) + 0.4),
                "pre_cooling": True,
                "expected_user_impact": "slight_warmer",
                "rationale": ["Increase setpoint slightly to support grid reduction goals."],
                "source": "fallback",
            }
        )
        return [comfort_option, balanced_option, support_option]

    def prepare_strategy_options(
        self,
        evaluation_user_id: str,
        turn_index: int,
        user_input: str,
        memory_snapshot: dict,
        scenario: dict,
    ) -> dict:
        user_preferences = self.parse_preferences(user_input, memory_snapshot)
        grid_demand = node_translate_grid({"vpp_context": scenario["vpp_context"]})["grid_demand"]
        base_strategy = generate_candidate_strategy(
            user_preferences=user_preferences,
            grid_demand=grid_demand,
            home_state=scenario["home_state"],
        )

        strategy_options = self.build_fallback_strategy_options(base_strategy)
        llm_metrics = {
            "used": False,
            "provider": self.strategy_llm_config.provider,
            "model": "not_used",
            "latency_seconds": 0.0,
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

        if self.strategy_llm_config.use_llm:
            try:
                strategy_options, llm_metrics = generate_strategy_options(
                    context={
                        "evaluation_user_id": evaluation_user_id,
                        "turn_index": turn_index,
                        "user_input": user_input,
                        "user_preferences": user_preferences,
                        "vpp_context": scenario.get("vpp_context", {}),
                        "grid_demand": grid_demand,
                        "home_state": scenario["home_state"],
                        "fallback_strategy": base_strategy,
                        "memory_snapshot": memory_snapshot,
                    },
                    fallback_strategy=base_strategy,
                )
            except Exception as exc:
                llm_metrics = {
                    "used": False,
                    "provider": self.strategy_llm_config.provider,
                    "model": "fallback_after_error",
                    "latency_seconds": 0.0,
                    "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "error": str(exc),
                }

        return {
            "user_preferences": user_preferences,
            "grid_demand": grid_demand,
            "base_strategy": base_strategy,
            "strategy_options": strategy_options,
            "llm_metrics": llm_metrics,
        }

    def project_control_outcome(
        self,
        selected_strategy: dict,
        user_preferences: dict,
        home_state: dict,
        grid_demand: dict,
    ) -> tuple[dict, dict]:
        control_plan = run_mock_mpc(
            candidate_strategy=selected_strategy,
            home_state=home_state,
            grid_demand=grid_demand,
        )
        safety_report = validate_safety(control_plan, user_preferences, home_state)
        if safety_report.get("safe", False):
            return control_plan, safety_report

        fallback_plan = fallback_control_plan(
            home_state=home_state,
            reason="; ".join(safety_report.get("violations", [])) or "safety_rejection",
        )
        fallback_safety = validate_safety(fallback_plan, user_preferences, home_state)
        fallback_safety["fallback_applied"] = True
        fallback_safety["original_violations"] = safety_report.get("violations", [])
        return fallback_plan, fallback_safety

    def invoke(
        self,
        *,
        evaluation_user_id: str,
        memory_path: str,
        log_dir: str,
        user_input: str,
        scenario: dict,
        prepared: dict,
        selected_strategy: dict,
        user_choice: dict,
        user_feedback: dict,
    ) -> dict:
        initial_state = {
            "evaluation_user_id": evaluation_user_id,
            "memory_path": memory_path,
            "log_dir": log_dir,
            "user_input": user_input,
            "grid_demand": prepared["grid_demand"],
            "grid_demand_source": scenario["grid_demand_source"],
            "vpp_context": scenario.get("vpp_context", {}),
            "home_state": deepcopy(scenario["home_state"]),
            "user_preferences": prepared["user_preferences"],
            "strategy_options": prepared["strategy_options"],
            "candidate_strategy": selected_strategy,
            "user_choice": user_choice,
            "user_feedback": user_feedback,
            "llm_metrics": prepared["llm_metrics"],
            "trajectory": [],
        }
        return self.app.invoke(initial_state)
