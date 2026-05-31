"""Run the minimal deterministic EnergyBridge agent loop."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from energybridge.agent.graph import build_energybridge_graph, build_feedback_graph
from energybridge.agent.nodes import node_translate_grid
from energybridge.grid.vpp_1.adapter import extract_vpp_context_from_result, load_vpp1_dispatch
from energybridge.llm.strategy_advisor import generate_strategy_options
from energybridge.skills.preference_parser import parse_user_preference
from energybridge.skills.strategy_generator import generate_candidate_strategy
from energybridge.utils.config import load_llm_config


def prompt_with_default(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def get_demo_vpp_context(home_state: dict) -> tuple[dict, str]:
    print("=== VPP-1 Task Mode ===")
    print("1. invitation")
    print("2. emergency")
    selection = input("Choose VPP-1 mode [1]: ").strip() or "1"
    mode = "emergency" if selection == "2" else "invitation"

    vpp_result = load_vpp1_dispatch(mode=mode)
    vpp_context = extract_vpp_context_from_result(vpp_result)
    return vpp_context, f"vpp_1:{mode}"


def build_fallback_strategy_options(base_strategy: dict) -> list[dict]:
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


def choose_strategy(strategy_options: list[dict]) -> tuple[dict, dict]:
    print()
    print("=== Strategy Options ===")
    for index, option in enumerate(strategy_options, start=1):
        print(f"{index}. {option.get('title', f'Strategy {index}')}")
        print(
            "   "
            f"mode={option.get('mode')} | setpoint={option.get('recommended_setpoint')}C | "
            f"impact={option.get('expected_user_impact')} | source={option.get('source', 'unknown')}"
        )
        rationale = option.get("rationale", [])
        if rationale:
            print(f"   rationale: {'; '.join(rationale)}")

    while True:
        raw_choice = input(f"Choose a strategy [1-{len(strategy_options)}]: ").strip()
        if raw_choice.isdigit():
            selected_index = int(raw_choice)
            if 1 <= selected_index <= len(strategy_options):
                selected = dict(strategy_options[selected_index - 1])
                choice_record = {
                    "approved": True,
                    "selected_index": selected_index,
                    "selected_title": selected.get("title", f"Strategy {selected_index}"),
                }
                return selected, choice_record
        print("Invalid choice. Please enter a valid strategy number.")


def collect_user_feedback() -> dict:
    print()
    print("=== User Feedback ===")
    print("Rate your actual satisfaction after seeing the execution result.")

    while True:
        raw_score = input("Satisfaction score [1-5]: ").strip()
        if raw_score.isdigit() and 1 <= int(raw_score) <= 5:
            score = int(raw_score)
            break
        print("Invalid score. Please enter an integer from 1 to 5.")

    labels = {
        1: "very_dissatisfied",
        2: "dissatisfied",
        3: "neutral",
        4: "satisfied",
        5: "very_satisfied",
    }
    comment = input("Optional feedback comment: ").strip()
    return {
        "satisfaction_score": score,
        "satisfaction_label": labels[score],
        "comment": comment,
    }


def main() -> None:
    app = build_energybridge_graph()
    feedback_app = build_feedback_graph()

    home_state = {
        "indoor_temp": 25.8,
        "outdoor_temp": 33.0,
        "hvac_setpoint": 25.0,
        "hvac_power_kw": 2.2,
        "occupancy": True,
    }

    vpp_context, grid_demand_source = get_demo_vpp_context(home_state)
    grid_demand = node_translate_grid({"vpp_context": vpp_context})["grid_demand"]
    print("Translated VPP-1 signal:")
    print(json.dumps(grid_demand, ensure_ascii=False, indent=2))

    print()
    user_input = prompt_with_default(
        "Describe your preference",
        "我希望尽量舒服，但如果电网有需求，也可以短时间配合削峰。",
    )

    user_preferences = parse_user_preference(user_input)
    base_strategy = generate_candidate_strategy(
        user_preferences=user_preferences,
        grid_demand=grid_demand,
        home_state=home_state,
    )

    strategy_options = build_fallback_strategy_options(base_strategy)
    llm_config = load_llm_config()
    llm_metrics = {
        "used": False,
        "provider": llm_config.provider,
        "model": "not_used",
        "latency_seconds": 0.0,
        "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    if llm_config.use_llm:
        try:
            strategy_options, llm_metrics = generate_strategy_options(
                context={
                    "user_input": user_input,
                    "user_preferences": user_preferences,
                    "vpp_context": vpp_context,
                    "grid_demand": grid_demand,
                    "home_state": home_state,
                    "fallback_strategy": base_strategy,
                },
                fallback_strategy=base_strategy,
            )
        except Exception as exc:
            print()
            print(f"LLM strategy generation failed, falling back to deterministic options: {exc}")

    selected_strategy, user_choice = choose_strategy(strategy_options)

    initial_state = {
        "user_input": user_input,
        "grid_demand": grid_demand,
        "grid_demand_source": grid_demand_source,
        "vpp_context": vpp_context,
        "home_state": home_state,
        "user_preferences": user_preferences,
        "strategy_options": strategy_options,
        "candidate_strategy": selected_strategy,
        "user_choice": user_choice,
        "llm_metrics": llm_metrics,
        "trajectory": [],
    }

    result = app.invoke(initial_state)
    user_feedback = collect_user_feedback()
    feedback_result = feedback_app.invoke(
        {
            "memory": result.get("memory", {}),
            "memory_path": str(PROJECT_ROOT / "logs" / "memory.json"),
            "trajectory": result.get("trajectory", []),
            "user_feedback": user_feedback,
        }
    )

    print()
    print("=== Final Response ===")
    print(result.get("final_response", ""))
    print()

    print("=== Control Plan ===")
    print(json.dumps(result.get("control_plan", {}), ensure_ascii=False, indent=2))
    print()

    print("=== Safety Report ===")
    print(json.dumps(result.get("safety_report", {}), ensure_ascii=False, indent=2))
    print()

    print("=== Execution Result ===")
    print(json.dumps(result.get("execution_result", {}), ensure_ascii=False, indent=2))
    print()

    print("=== Metrics ===")
    print(json.dumps(result.get("metrics", {}), ensure_ascii=False, indent=2))
    print()

    print("=== Trajectory Steps ===")
    for idx, step in enumerate(result.get("trajectory", []), start=1):
        print(f"{idx}. {step.get('node')}")

    if result.get("trajectory_log_path"):
        print()
        print(f"Trajectory log saved to: {result['trajectory_log_path']}")

    print()
    print("=== User Feedback ===")
    print(json.dumps(user_feedback, ensure_ascii=False, indent=2))

    print()
    print("=== Feedback Update ===")
    print(json.dumps(feedback_result.get("memory", {}).get("latest_user_feedback", {}), ensure_ascii=False, indent=2))
    print(f"Memory updated at: {PROJECT_ROOT / 'logs' / 'memory.json'}")


if __name__ == "__main__":
    main()
