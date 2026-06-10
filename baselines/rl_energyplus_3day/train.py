"""Train and evaluate PPO directly on the three-day family EnergyPlus model."""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback, CheckpointCallback

from .environment import EnergyPlusFamilyEnv


class WallClockStopCallback(BaseCallback):
    def __init__(self, seconds: float):
        super().__init__()
        self.deadline = time.monotonic() + seconds

    def _on_step(self) -> bool:
        return time.monotonic() < self.deadline


def evaluate(model: PPO, output: Path, persona_id: str) -> dict:
    env = EnergyPlusFamilyEnv(output / "eval_ep", persona_id=persona_id)
    obs, _ = env.reset()
    terminated = False
    while not terminated:
        action, _ = model.predict(obs, deterministic=True)
        obs, _, terminated, _, _ = env.step(action)
    rows = env.rows
    appliance_results = env.final_appliance_results
    env.close()
    with (output / "evaluation_timeseries.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    occupied = [row for row in rows if 8 <= row["sim_hour"] % 24 < 22]
    vpp = [row for row in rows if row["vpp_active"]]
    vpp_actions = []
    for event_index in range(3):
        start, end = 18.0 + event_index * 24.0, 19.0 + event_index * 24.0
        event_rows = [row for row in rows if start <= row["sim_hour"] < end]
        vpp_actions.append({
            "event": event_index + 1,
            "cooling_setpoint_c": sum(row["cooling_setpoint_c"] for row in event_rows) / len(event_rows),
            "washer_started": any(row["washer_power_kw"] > 0.0 for row in event_rows),
            "ewh_heating": any(row["water_heater_power_kw"] > 0.0 for row in event_rows),
        })
    action_events = []
    for day in range(3):
        day_rows = [row for row in rows if day * 24 <= row["sim_hour"] < (day + 1) * 24]
        washer_requests = [row["sim_hour"] for row in day_rows if row["washer_start_request"] >= 0.5]
        ewh_requests = [row["sim_hour"] for row in day_rows if row["water_heater_preheat_request"] >= 0.5]
        washer_runs = [row["sim_hour"] for row in day_rows if row["washer_power_kw"] > 0.0]
        ewh_runs = [row["sim_hour"] for row in day_rows if row["water_heater_power_kw"] > 0.0]
        action_events.append({
            "day": day + 1,
            "washer_request_time": washer_requests[0] if washer_requests else None,
            "washer_actual_start_time": washer_runs[0] if washer_runs else None,
            "ewh_request_time": ewh_requests[0] if ewh_requests else None,
            "ewh_actual_start_time": ewh_runs[0] if ewh_runs else None,
        })
    summary = {
        "environment": "EnergyPlus 24.1 family_simple_3day.idf",
        "total_energy_kwh": sum(row["energy_kwh"] for row in rows),
        "vpp_window_energy_kwh": sum(row["energy_kwh"] for row in vpp),
        "mean_indoor_temperature_c": sum(row["indoor_temperature_c"] for row in occupied) / len(occupied),
        "comfort_ok_fraction": sum(23 <= row["indoor_temperature_c"] <= 26 for row in occupied) / len(occupied),
        "pmv_ok_fraction": sum(abs(row["pmv"]) <= 0.5 for row in occupied) / len(occupied),
        "mean_vpp_actions": {
            "cooling_setpoint_c": sum(row["cooling_setpoint_c"] for row in vpp) / len(vpp),
            "washer_start_request": sum(row["washer_start_request"] for row in vpp) / len(vpp),
            "water_heater_preheat_request": sum(row["water_heater_preheat_request"] for row in vpp) / len(vpp),
        },
        "mean_vpp_capacity": {
            "committable_kw": sum(row["capacity_committable_kw"] for row in vpp) / len(vpp),
            "recommended_bid_kw": sum(row["capacity_recommended_bid_kw"] for row in vpp) / len(vpp),
            "success_probability": sum(row["capacity_success_probability"] for row in vpp) / len(vpp),
        },
        "action_events": action_events,
        "vpp_actions": vpp_actions,
        "task_completion_rate": sum(
            bool(day.get("completed")) for day in appliance_results.get("washer", [])
        ) / max(1, len(appliance_results.get("washer", []))),
        "water_heater_ready_rate": sum(
            bool(day.get("preheat_used")) and float(day.get("energy_kwh", 0.0)) > 0.0
            for day in appliance_results.get("water_heater", [])
        ) / max(1, len(appliance_results.get("water_heater", []))),
    }
    summary.update(score_roleplay(rows, summary, persona_id))
    (output / "evaluation_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_summary(output, summary)
    return summary


def score_roleplay(rows: list[dict], summary: dict, persona_id: str) -> dict:
    import sys
    bench_dir = Path(__file__).resolve().parents[2] / "experiments" / "benchmark"
    if str(bench_dir) not in sys.path:
        sys.path.insert(0, str(bench_dir))
    from user_pref_scorer import score_user_preference

    persona_path = Path(__file__).resolve().parents[2] / "energybridge" / "roleplay" / "personas" / f"{persona_id}.json"
    persona = json.loads(persona_path.read_text(encoding="utf-8"))
    scores = []
    details = []
    for event_index in range(3):
        start, end = 18.0 + event_index * 24.0, 19.0 + event_index * 24.0
        event_rows = [row for row in rows if start <= row["sim_hour"] < end]
        mean = lambda key: sum(float(row[key]) for row in event_rows) / len(event_rows)
        result = score_user_preference(
            building="family", method="rl", mean_temp_c=mean("indoor_temperature_c"),
            pmv_ok_fraction=sum(abs(row["pmv"]) <= 0.5 for row in event_rows) / len(event_rows),
            energy_kwh_per_day=summary["total_energy_kwh"] / 3.0,
            agent_setpoint_c=mean("cooling_setpoint_c"), event_index=event_index + 1,
            user_preference_text=persona["llm_prompts"]["system_prompt"],
            agent_reason="PPO baseline controlled the same three-day EnergyPlus family model.",
            persona=persona,
        )
        if result.get("source") != "roleplay_llm":
            raise RuntimeError(f"Role-play LLM required, got {result.get('source')}")
        scores.append(float(result["score"]))
        details.append({"event": event_index + 1, **result})
    return {
        "user_pref_scores": scores,
        "user_satisfaction": sum(scores) / len(scores),
        "roleplay_event_scores": details,
    }


def write_summary(output: Path, summary: dict) -> None:
    actions = summary["mean_vpp_actions"]
    capacity = summary["mean_vpp_capacity"]
    event_lines = []
    for event in summary["action_events"]:
        fmt = lambda value: "未启动" if value is None else f"{int(value // 24) + 1}日 {value % 24:05.2f}h"
        event_lines.append(
            f"  Day{event['day']}设备动作 : washer请求={fmt(event['washer_request_time'])}  "
            f"实际启动={fmt(event['washer_actual_start_time'])}  "
            f"EWH请求={fmt(event['ewh_request_time'])}  实际加热={fmt(event['ewh_actual_start_time'])}"
        )
    vpp_action_lines = [
        f"  VPP{event['event']}实际动作 : 空调={event['cooling_setpoint_c']:.2f}°C  "
        f"washer={'运行' if event['washer_started'] else '关闭'}  "
        f"EWH={'加热' if event['ewh_heating'] else '关闭'}"
        for event in summary["vpp_actions"]
    ]
    lines = [
        "─" * 62,
        "  RL EnergyPlus Baseline 关键 Metrics 汇总",
        "─" * 62,
        f"  VPP时段用电量 : {summary['vpp_window_energy_kwh']:.3f} kWh",
        f"  总能耗        : {summary['total_energy_kwh']:.2f} kWh (3天)",
        f"  满意度均分    : {summary['user_satisfaction']:.1f}/5",
        "  逐事件评分    : " + "  ".join(
            f"VPP{i+1}:{score:.0f}" for i, score in enumerate(summary["user_pref_scores"])
        ),
        f"  区域均温      : {summary['mean_indoor_temperature_c']:.2f} °C",
        f"  PMV达标率     : {summary['pmv_ok_fraction']*100:.1f}%",
        f"  舒适区达标率  : {summary['comfort_ok_fraction']*100:.1f}% (23-26°C)",
        f"  洗衣任务完成率: {summary['task_completion_rate']*100:.0f}%",
        f"  热水器就绪率  : {summary['water_heater_ready_rate']*100:.0f}%",
        f"  VPP空调设定点 : {actions['cooling_setpoint_c']:.2f}°C",
        *vpp_action_lines,
        *event_lines,
        f"  VPP平均容量   : 可承诺{capacity['committable_kw']:.3f}kW  "
        f"建议上报{capacity['recommended_bid_kw']:.3f}kW  成功率{capacity['success_probability']*100:.1f}%",
        "  Token消耗     : N/A (RL推理不调用LLM；仅最终role-play评分调用LLM)",
        "─" * 62,
    ]
    (output / "run_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=4.0)
    parser.add_argument("--timesteps", type=int, default=10_000_000)
    parser.add_argument("--output", type=Path, default=Path("benchmark_results/rl_energyplus_3day_formal"))
    parser.add_argument("--device", default="auto")
    parser.add_argument("--persona", default="atom_comfort_sensitive")
    parser.add_argument("--resume", type=Path, default=None,
                        help="Continue training from a saved PPO model or checkpoint ZIP.")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    env = EnergyPlusFamilyEnv(args.output / "train_ep", persona_id=args.persona)
    if args.resume:
        model = PPO.load(args.resume, env=env, device=args.device, print_system_info=False)
        print(f"Resuming PPO training from {args.resume} at {model.num_timesteps} timesteps")
    else:
        model = PPO(
            "MlpPolicy", env, verbose=1, device=args.device, n_steps=432,
            batch_size=144, learning_rate=3e-4, gamma=0.995,
            policy_kwargs={"net_arch": [256, 256]},
        )
    callbacks = [
        WallClockStopCallback(args.hours * 3600),
        CheckpointCallback(save_freq=4320, save_path=str(args.output / "checkpoints")),
    ]
    started = time.monotonic()
    model.learn(
        total_timesteps=args.timesteps, callback=callbacks, progress_bar=False,
        reset_num_timesteps=not bool(args.resume),
    )
    elapsed = time.monotonic() - started
    model.save(args.output / "ppo_energyplus_3day")
    env.close()
    summary = evaluate(model, args.output, args.persona)
    summary.update({"training_elapsed_seconds": elapsed, "training_timesteps": model.num_timesteps})
    (args.output / "formal_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
