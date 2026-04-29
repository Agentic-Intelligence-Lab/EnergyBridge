"""High-level simulation runners.

This module wires together the four simulation objects requested by the course
structure: User, Agent, Grid, and Home. It also writes per-user artifacts and
batch-level JSON/CSV reports for role-play evaluations.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from energybridge.evaluation.metrics import aggregate_roleplay_summaries, learning_score
from energybridge.memory.store import load_memory
from energybridge.simulation.agent import AgentSimulator
from energybridge.simulation.grid import GridSimulator
from energybridge.simulation.home import HomeSimulator
from energybridge.simulation.user import SimulatedUser


def _diff_dict(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for key in sorted(set(before.keys()) | set(after.keys())):
        before_value = before.get(key)
        after_value = after.get(key)
        if before_value == after_value:
            continue
        if isinstance(before_value, dict) and isinstance(after_value, dict):
            nested = _diff_dict(before_value, after_value)
            if nested:
                diff[key] = nested
            continue
        diff[key] = {"before": before_value, "after": after_value}
    return diff


def _memory_change_summary(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "stable_preferences": _diff_dict(before.get("stable_preferences", {}), after.get("stable_preferences", {})),
        "contextual_preferences": _diff_dict(before.get("contextual_preferences", {}), after.get("contextual_preferences", {})),
        "episodic_log_count": {
            "before": len(before.get("episodic_logs", [])),
            "after": len(after.get("episodic_logs", [])),
        },
    }


def _safe_int(value: Any, default: int = 1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def run_roleplay_simulation(
    turns: int = 5,
    output_root: str = "logs/evaluations",
    evaluation_user_id: str | None = None,
) -> dict[str, Any]:
    """Run one simulated user through multiple EnergyBridge turns."""

    user = SimulatedUser()
    agent = AgentSimulator()
    grid = GridSimulator()
    home = HomeSimulator()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    user_id = evaluation_user_id or f"{user.persona_id}_{timestamp}"
    user_dir = Path(output_root) / user_id
    trajectories_dir = user_dir / "trajectories"
    memory_path = user_dir / "memory.json"
    user_dir.mkdir(parents=True, exist_ok=True)
    trajectories_dir.mkdir(parents=True, exist_ok=True)

    (user_dir / "persona.json").write_text(
        json.dumps(
            {
                "evaluation_user_id": user_id,
                "persona": user.persona,
                "persona_trace": user.persona_trace,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    turn_logs: list[dict[str, Any]] = []
    history_summary: list[dict[str, Any]] = []

    for turn_index in range(1, turns + 1):
        scenario = grid.get_scenario(turn_index=turn_index, home_state=home.snapshot())
        memory_before = load_memory(str(memory_path))

        user_input_trace = user.generate_input(
            turn_index=turn_index,
            scenario=scenario,
            memory_snapshot=memory_before,
            history_summary=history_summary,
        )
        user_input = str(user_input_trace["data"].get("user_input", "")).strip()

        prepared = agent.prepare_strategy_options(
            evaluation_user_id=user_id,
            turn_index=turn_index,
            user_input=user_input,
            memory_snapshot=memory_before,
            scenario=scenario,
        )

        choice_trace = user.choose_strategy(
            turn_index=turn_index,
            scenario=scenario,
            strategy_options=prepared["strategy_options"],
        )
        selected_index = _safe_int(choice_trace["data"].get("selected_index"), default=1)
        selected_index = max(1, min(selected_index, len(prepared["strategy_options"])))
        selected_strategy = dict(prepared["strategy_options"][selected_index - 1])
        user_choice = {
            "approved": bool(choice_trace["data"].get("approved", True)),
            "selected_index": selected_index,
            "selected_title": selected_strategy.get("title", f"Strategy {selected_index}"),
            "selection_reason": choice_trace["data"].get("reason", ""),
        }

        projected_control_plan, projected_safety_report = agent.project_control_outcome(
            selected_strategy=selected_strategy,
            user_preferences=prepared["user_preferences"],
            home_state=scenario["home_state"],
            translated_grid_signal=prepared["translated_grid_signal"],
        )
        feedback_trace = user.give_feedback(
            turn_index=turn_index,
            selected_strategy=selected_strategy,
            projected_control_plan=projected_control_plan,
            projected_safety_report=projected_safety_report,
        )
        user_feedback = {
            "satisfaction_score": _safe_int(feedback_trace["data"].get("satisfaction_score"), default=3),
            "satisfaction_label": str(feedback_trace["data"].get("satisfaction_label", "neutral")),
            "comment": str(feedback_trace["data"].get("comment", "")),
        }

        result = agent.invoke(
            evaluation_user_id=user_id,
            memory_path=str(memory_path),
            log_dir=str(trajectories_dir),
            user_input=user_input,
            scenario=scenario,
            prepared=prepared,
            selected_strategy=selected_strategy,
            user_choice=user_choice,
            user_feedback=user_feedback,
        )
        memory_after = load_memory(str(memory_path))

        turn_log = {
            "evaluation_user_id": user_id,
            "turn_index": turn_index,
            "vpp_mode": scenario["vpp_mode"],
            "scenario": scenario,
            "interaction": {
                "user_input": user_input,
                "strategy_options": prepared["strategy_options"],
                "selected_strategy": selected_strategy,
                "user_choice": user_choice,
                "user_feedback": user_feedback,
                "final_response": result.get("final_response", ""),
            },
            "roleplay_traces": {
                "user_input": user_input_trace,
                "choice": choice_trace,
                "feedback": feedback_trace,
            },
            "system_result": {
                "control_plan": result.get("control_plan", {}),
                "safety_report": result.get("safety_report", {}),
                "execution_result": result.get("execution_result", {}),
                "metrics": result.get("metrics", {}),
                "trajectory_log_path": result.get("trajectory_log_path", ""),
            },
            "memory_before": memory_before,
            "memory_after": memory_after,
            "memory_change": _memory_change_summary(memory_before, memory_after),
        }
        (user_dir / f"turn_{turn_index:02d}.json").write_text(
            json.dumps(turn_log, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        turn_logs.append(turn_log)

        history_summary.append(
            {
                "turn_index": turn_index,
                "user_input": user_input,
                "selected_strategy": selected_strategy.get("title", ""),
                "satisfaction_score": user_feedback.get("satisfaction_score"),
                "memory_stable_preferences": memory_after.get("stable_preferences", {}),
            }
        )
        home.apply_control_result(result)

    final_memory = load_memory(str(memory_path))
    summary = {
        "evaluation_user_id": user_id,
        "turn_count": turns,
        "persona": user.persona,
        "final_memory": final_memory,
        "learning_summary": learning_score(
            user.persona.get("stable_preferences", {}),
            final_memory.get("stable_preferences", {}),
        ),
        "turn_overview": [
            {
                "turn_index": log["turn_index"],
                "selected_strategy": log["interaction"]["selected_strategy"].get("title", ""),
                "satisfaction_score": log["interaction"]["user_feedback"].get("satisfaction_score"),
                "meets_vpp_requirement": log["system_result"]["metrics"].get("meets_vpp_requirement"),
                "api_latency_seconds": log["system_result"]["metrics"].get("api_latency_seconds"),
            }
            for log in turn_logs
        ],
    }
    (user_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"evaluation_user_id": user_id, "user_dir": str(user_dir), "summary": summary}


def _write_batch_reports(batch_dir: Path, user_results: list[dict[str, Any]], aggregate: dict[str, Any]) -> None:
    summary_rows: list[dict[str, Any]] = []
    turn_rows: list[dict[str, Any]] = []

    for result in user_results:
        summary = result["summary"]
        learning = summary.get("learning_summary", {})
        turn_overview = summary.get("turn_overview", [])
        scores = [row.get("satisfaction_score") for row in turn_overview if row.get("satisfaction_score") is not None]
        meets = [bool(row.get("meets_vpp_requirement")) for row in turn_overview]
        latencies = [row.get("api_latency_seconds") for row in turn_overview if row.get("api_latency_seconds") is not None]

        summary_rows.append(
            {
                "evaluation_user_id": summary.get("evaluation_user_id"),
                "persona_id": summary.get("persona", {}).get("persona_id"),
                "turn_count": summary.get("turn_count"),
                "preference_learning_score": learning.get("preference_learning_score"),
                "avg_satisfaction_score": round(sum(scores) / len(scores), 3) if scores else None,
                "vpp_meet_rate": round(sum(meets) / len(meets), 3) if meets else None,
                "avg_api_latency_seconds": round(sum(latencies) / len(latencies), 3) if latencies else None,
                "artifact_dir": result.get("user_dir"),
            }
        )
        for row in turn_overview:
            turn_rows.append({"evaluation_user_id": summary.get("evaluation_user_id"), **row})

    (batch_dir / "batch_summary.json").write_text(
        json.dumps({"aggregate": aggregate, "users": summary_rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if summary_rows:
        with (batch_dir / "batch_summary.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
            writer.writeheader()
            writer.writerows(summary_rows)

    if turn_rows:
        with (batch_dir / "batch_turns.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(turn_rows[0].keys()))
            writer.writeheader()
            writer.writerows(turn_rows)


def run_batch_roleplay_simulation(
    user_count: int = 10,
    turns: int = 5,
    output_root: str = "logs/evaluations",
) -> dict[str, Any]:
    """Run a batch of simulated users and write formal JSON/CSV reports."""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_id = f"batch_{timestamp}_{user_count}users_{turns}turns"
    batch_dir = Path(output_root) / batch_id
    users_dir = batch_dir / "users"
    users_dir.mkdir(parents=True, exist_ok=True)

    user_results = [
        run_roleplay_simulation(
            turns=turns,
            output_root=str(users_dir),
            evaluation_user_id=f"{batch_id}_user_{index:03d}",
        )
        for index in range(1, user_count + 1)
    ]
    aggregate = aggregate_roleplay_summaries([result["summary"] for result in user_results])
    batch_result = {
        "batch_id": batch_id,
        "batch_dir": str(batch_dir),
        "user_count": user_count,
        "turns_per_user": turns,
        "aggregate": aggregate,
        "user_results": user_results,
    }
    (batch_dir / "batch_result.json").write_text(json.dumps(batch_result, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_batch_reports(batch_dir, user_results, aggregate)
    return batch_result
