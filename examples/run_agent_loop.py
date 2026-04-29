"""Run the minimal deterministic EnergyBridge agent loop."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from energybridge.agent.graph import build_energybridge_graph
from energybridge.grid.vpp_1.adapter import adapt_vpp1_signal
from energybridge.grid.vpp_1.mock_signal import get_mock_vpp1_raw_signal
from energybridge.llm.strategy_advisor import generate_strategy_options
from energybridge.skills.grid_signal_translator import translate_grid_signal
from energybridge.skills.preference_parser import parse_user_preference
from energybridge.skills.strategy_generator import generate_candidate_strategy
from energybridge.utils.config import load_llm_config


def prompt_with_default(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def get_demo_grid_signal() -> tuple[dict, str]:
    print("=== Grid Signal Source ===")
    print("1. Use mock VPP-1 signal")
    print("2. Enter a custom simple grid signal")
    selection = input("Choose source [1]: ").strip() or "1"

    if selection == "2":
        signal_type = prompt_with_default("Signal type", "DR_EVENT")
        start_time = prompt_with_default("Start time", "18:00")
        end_time = prompt_with_default("End time", "19:00")
        target_reduction_kw = float(prompt_with_default("Target reduction kW", "0.5"))
        price_level = prompt_with_default("Price level", "high")
        return (
            {
                "type": signal_type,
                "start_time": start_time,
                "end_time": end_time,
                "target_reduction_kw": target_reduction_kw,
                "price_level": price_level,
            },
            "custom_cli",
        )

    raw_signal = get_mock_vpp1_raw_signal()
    return adapt_vpp1_signal(raw_signal), "mock_vpp_1"


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


def main() -> None:
    app = build_energybridge_graph()

    grid_signal, grid_signal_source = get_demo_grid_signal()
    print()
    print("Detected grid signal:")
    print(json.dumps(grid_signal, ensure_ascii=False, indent=2))

    print()
    user_input = prompt_with_default(
        "Describe your preference",
        "我希望尽量舒服，但如果电网有需求，也可以短时间配合削峰。",
    )

    home_state = {
        "indoor_temp": 25.8,
        "outdoor_temp": 33.0,
        "hvac_setpoint": 25.0,
        "hvac_power_kw": 2.2,
        "occupancy": True,
    }

    user_preferences = parse_user_preference(user_input)
    translated_grid_signal = translate_grid_signal(grid_signal)
    base_strategy = generate_candidate_strategy(
        user_preferences=user_preferences,
        translated_grid_signal=translated_grid_signal,
        home_state=home_state,
    )

    strategy_options = build_fallback_strategy_options(base_strategy)
    llm_config = load_llm_config()
    if llm_config.use_llm:
        try:
            strategy_options = generate_strategy_options(
                context={
                    "user_input": user_input,
                    "user_preferences": user_preferences,
                    "grid_signal": grid_signal,
                    "translated_grid_signal": translated_grid_signal,
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
        "grid_signal": grid_signal,
        "grid_signal_source": grid_signal_source,
        "home_state": home_state,
        "user_preferences": user_preferences,
        "translated_grid_signal": translated_grid_signal,
        "strategy_options": strategy_options,
        "candidate_strategy": selected_strategy,
        "user_choice": user_choice,
        "trajectory": [],
    }

    result = app.invoke(initial_state)

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

    print("=== Trajectory Steps ===")
    for idx, step in enumerate(result.get("trajectory", []), start=1):
        print(f"{idx}. {step.get('node')}")

    if result.get("trajectory_log_path"):
        print()
        print(f"Trajectory log saved to: {result['trajectory_log_path']}")


if __name__ == "__main__":
    main()
