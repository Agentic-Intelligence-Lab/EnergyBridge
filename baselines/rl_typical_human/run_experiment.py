"""Run a June 1-7 Typical_Human baseline and PPO smoke experiment."""

from __future__ import annotations

import csv
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from baselines.rl_typical_human.environment import TypicalHumanEnv
from baselines.rl_typical_human.schedule import DEFAULT_SEED, generate_typical_week, write_schedule_outputs

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
os.environ.setdefault("MPLCONFIGDIR", str(OUTPUT_DIR / ".matplotlib"))
ACTION_NAMES = [
    "heating_setpoint_c", "cooling_setpoint_c", "ev_charge_request",
    "water_heater_request", "dishwasher_start_request",
    "washer_start_request", "dryer_start_request",
]


def baseline_policy(env: TypicalHumanEnv, obs: np.ndarray) -> np.ndarray:
    now = env.now
    action = np.array([20.0, 26.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
    if env._ev_at_home() and env.ev_soc < env.ev_target_soc - 1e-4:
        action[2] = 1.0
    if env.ewh_temp_c < env.ewh_setpoint_c - 0.5 or now.hour in [5, 6, 7, 20, 21, 22]:
        action[3] = 1.0
    for idx, device in [(4, "dishwasher"), (5, "clothes_washer"), (6, "clothes_dryer")]:
        for task in env.tasks:
            if task.device == device and task.state == "waiting" and task.earliest_start <= now:
                action[idx] = 1.0
                break
    return action


def random_policy(env: TypicalHumanEnv, obs: np.ndarray) -> np.ndarray:
    return env.action_space.sample()


def run_policy(name: str, policy: Callable[[TypicalHumanEnv, np.ndarray], np.ndarray], seed: int = DEFAULT_SEED) -> Dict[str, Any]:
    env = TypicalHumanEnv(seed=seed)
    obs, info = env.reset(seed=seed)
    rows: List[Dict[str, Any]] = []
    terminated = False
    total_reward = 0.0
    while not terminated:
        action = policy(env, obs)
        obs, reward, terminated, truncated, info = env.step(action)
        row = dict(info["typical_human"])
        row.update({f"action_{name}": float(value) for name, value in zip(ACTION_NAMES, action)})
        rows.append(row)
        total_reward += reward
        if truncated:
            break
    return write_results(name, rows, total_reward)


def write_results(name: str, rows: List[Dict[str, Any]], total_reward: float) -> Dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / f"{name}_timeseries.csv"
    summary_path = OUTPUT_DIR / f"{name}_summary.json"
    fieldnames: List[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    summary = summarize_rows(name, rows, total_reward, str(csv_path))
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def summarize_rows(name: str, rows: List[Dict[str, Any]], total_reward: float, csv_path: str) -> Dict[str, Any]:
    def total(key: str) -> float:
        return float(sum(float(row.get(key, 0.0)) for row in rows))

    def maxv(key: str) -> float:
        return float(max(float(row.get(key, 0.0)) for row in rows)) if rows else 0.0

    final = rows[-1] if rows else {}
    vpp_rows = [row for row in rows if int(row.get("vpp_active", 0)) == 1]
    vpp_by_event: Dict[str, float] = {}
    for row in vpp_rows:
        event_id = datetime.fromisoformat(str(row["timestamp"])).date().isoformat()
        vpp_by_event[event_id] = vpp_by_event.get(event_id, 0.0) + float(row.get("household_energy_kwh", 0.0))
    vpp_actuals = list(vpp_by_event.values())
    vpp_targets = [2.0] * len(vpp_actuals)
    target_total = sum(vpp_targets)
    actual_total = sum(vpp_actuals)
    task_total = sum(int(final.get(f"{key}_tasks_total", 0)) for key in ("dishwasher", "washer", "dryer"))
    task_finished = sum(int(final.get(f"{key}_tasks_finished", 0)) for key in ("dishwasher", "washer", "dryer"))
    task_vpp_pairs = {
        (datetime.fromisoformat(str(row["timestamp"])).date().isoformat(), device)
        for row in vpp_rows
        for device, key in (
            ("dishwasher", "dishwasher_power_kw"),
            ("washer", "clothes_washer_power_kw"),
            ("dryer", "clothes_dryer_power_kw"),
        )
        if float(row.get(key, 0.0)) > 0.0
    }
    shifted_finished = max(0, task_finished - len(task_vpp_pairs))
    vpp_event_details = []
    for event_date in sorted(vpp_by_event):
        event_rows = [
            row for row in vpp_rows
            if datetime.fromisoformat(str(row["timestamp"])).date().isoformat() == event_date
        ]
        mean = lambda key: sum(float(row.get(key, 0.0)) for row in event_rows) / max(1, len(event_rows))
        vpp_event_details.append({
            "date": event_date,
            "capacity_committable_kw": max(float(row.get("capacity_committable_kw", 0.0)) for row in event_rows),
            "capacity_recommended_bid_kw": max(float(row.get("capacity_recommended_bid_kw", 0.0)) for row in event_rows),
            "capacity_success_probability": max(float(row.get("capacity_success_probability", 0.0)) for row in event_rows),
            "capacity_constraints": sorted({
                str(row.get("capacity_constraints", "")) for row in event_rows if row.get("capacity_constraints")
            }),
            "mean_actions": {name: mean(f"action_{name}") for name in ACTION_NAMES},
        })
    comfort_rows = [row for row in rows if int(row.get("occupied", 0)) == 1]
    comfort_ok = [row for row in comfort_rows if 23.0 <= float(row.get("indoor_temperature_c", 0.0)) <= 26.0]
    unmet_cooling_h = sum(
        10.0 / 60.0 for row in comfort_rows if float(row.get("indoor_temperature_c", 0.0)) > 26.0
    )
    summary = {
        "name": name,
        "validation_ok": len(rows) == 1008,
        "rows": len(rows),
        "time_step_minutes": 10,
        "total_reward": float(total_reward),
        "total_cost_yuan": float(final.get("total_cost_yuan", 0.0)),
        "total_energy_kwh": total("household_energy_kwh"),
        "peak_power_kw": maxv("household_power_kw"),
        "energy_kwh_per_day": total("household_energy_kwh") / 7.0,
        "vpp_window_energy_kwh": actual_total,
        "vpp_event_energy_kwh": vpp_actuals,
        "vpp_demand_targets_kwh": vpp_targets,
        "vpp_demand_achievement_ratio": actual_total / target_total if target_total else 0.0,
        "vpp_event_details": vpp_event_details,
        "task_completion_rate": task_finished / task_total if task_total else 1.0,
        "task_shift_success_rate": shifted_finished / task_total if task_total else 1.0,
        "task_vpp_avoidance_rate": shifted_finished / task_finished if task_finished else 0.0,
        "mean_indoor_temperature_c": (
            sum(float(row.get("indoor_temperature_c", 0.0)) for row in rows) / len(rows) if rows else 0.0
        ),
        "comfort_ok_fraction": len(comfort_ok) / len(comfort_rows) if comfort_rows else 0.0,
        "unmet_cooling_hours": unmet_cooling_h,
        "comfort_violation_degree_steps": total("comfort_violation_c"),
        "hot_water_violation_degree_steps": total("hot_water_violation_c"),
        "ev_final_soc": float(final.get("ev_soc", 0.0)),
        "ev_deadline_violations": int(total("ev_deadline_violation")),
        "task_deadline_violations": int(total("task_deadline_violations")),
        "ev_target_reached_rate": max(0.0, 1.0 - int(total("ev_deadline_violation")) / 7.0),
        "water_heater_ready_rate": 1.0 if total("hot_water_violation_c") == 0 else 0.0,
        "dishwasher_tasks_finished": int(final.get("dishwasher_tasks_finished", 0)),
        "dishwasher_tasks_total": int(final.get("dishwasher_tasks_total", 0)),
        "washer_tasks_finished": int(final.get("washer_tasks_finished", 0)),
        "washer_tasks_total": int(final.get("washer_tasks_total", 0)),
        "dryer_tasks_finished": int(final.get("dryer_tasks_finished", 0)),
        "dryer_tasks_total": int(final.get("dryer_tasks_total", 0)),
        "csv_path": csv_path,
        "user_satisfaction": None,
        "pmv_ok_fraction": None,
        "llm_tokens": None,
    }
    write_metrics_summary(name, summary)
    return summary


def write_metrics_summary(name: str, summary: Dict[str, Any]) -> None:
    actuals = summary["vpp_event_energy_kwh"]
    targets = summary["vpp_demand_targets_kwh"]
    events = "  ".join(
        f"VPP{i+1}:{actual:.3f}/{target:.2f}" for i, (actual, target) in enumerate(zip(actuals, targets))
    )
    detail_lines = []
    for i, detail in enumerate(summary.get("vpp_event_details", []), start=1):
        actions = detail["mean_actions"]
        detail_lines += [
            f"      VPP{i}容量      : 可承诺{detail['capacity_committable_kw']:.3f}kW  "
            f"建议上报{detail['capacity_recommended_bid_kw']:.3f}kW  "
            f"成功率{detail['capacity_success_probability']*100:.1f}%",
            f"      VPP{i}平均动作  : heating={actions['heating_setpoint_c']:.2f}°C  "
            f"cooling={actions['cooling_setpoint_c']:.2f}°C  "
            f"EV={actions['ev_charge_request']:.2f}  EWH={actions['water_heater_request']:.2f}  "
            f"DW={actions['dishwasher_start_request']:.2f}  "
            f"washer={actions['washer_start_request']:.2f}  dryer={actions['dryer_start_request']:.2f}",
        ]
    lines = [
        "─" * 62,
        f"  RL Baseline 关键 Metrics 汇总 ({name})",
        "─" * 62,
        "  ▸ VPP削峰",
        f"      VPP时段用电量: {summary['vpp_window_energy_kwh']:.3f} kWh (3个事件×1h合计)",
        f"      需求达成比率 : {summary['vpp_demand_achievement_ratio']:.3f}  [{events}]",
        f"      任务完成率   : {summary['task_completion_rate']*100:.0f}%",
        f"      平移成功率   : {summary['task_shift_success_rate']*100:.0f}%",
        f"      错峰率       : {summary['task_vpp_avoidance_rate']*100:.0f}%",
        *detail_lines,
        "  ▸ 用电量",
        f"      总能耗       : {summary['total_energy_kwh']:.2f} kWh (7天)",
        f"      日均          : {summary['energy_kwh_per_day']:.2f} kWh/天",
        "  ▸ 用户舒适度",
        (
            f"      满意度均分   : {summary['user_satisfaction']:.1f}/5"
            if summary.get("user_satisfaction") is not None
            else "      满意度均分   : N/A (未启用roleplay评分)"
        ),
        "      逐事件       : " + (
            "  ".join(f"VPP{i+1}:{score}" for i, score in enumerate(summary.get("user_pref_scores", [])))
            or "N/A"
        ),
        f"      区域均温     : {summary['mean_indoor_temperature_c']:.2f} °C",
        "      PMV达标率    : N/A (轻量RL环境无PMV模型)",
        f"      舒适区达标率 : {summary['comfort_ok_fraction']*100:.1f}% (23-26°C)",
        f"      未满足制冷   : {summary['unmet_cooling_hours']:.1f} h",
        "  ▸ 电器目标达成",
        f"      EV充电达标   : {summary['ev_target_reached_rate']*100:.0f}%",
        f"      热水器就绪   : {summary['water_heater_ready_rate']*100:.0f}%",
        "  ▸ Token消耗",
        "      N/A (RL推理不调用LLM)",
        "─" * 62,
    ]
    (OUTPUT_DIR / f"{name}_run_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_ppo(seed: int = DEFAULT_SEED, total_timesteps: int = 1536,
            run_name: str = "ppo_smoke", persona: dict | None = None) -> Dict[str, Any]:
    from stable_baselines3 import PPO

    env = TypicalHumanEnv(seed=seed)
    model = PPO(
        "MlpPolicy",
        env,
        verbose=0,
        seed=seed,
        n_steps=256,
        batch_size=64,
        learning_rate=3e-4,
        device="auto",
    )
    model.learn(total_timesteps=total_timesteps, progress_bar=False)
    model_path = OUTPUT_DIR / f"{run_name}_model.zip"
    model.save(model_path)

    eval_env = TypicalHumanEnv(seed=seed)
    obs, info = eval_env.reset(seed=seed)
    rows: List[Dict[str, Any]] = []
    total_reward = 0.0
    terminated = False
    while not terminated:
        action, _ = model.predict(obs, deterministic=True)
        obs, reward, terminated, truncated, info = eval_env.step(action)
        row = dict(info["typical_human"])
        row.update({
            f"action_{name}": float(value)
            for name, value in zip(ACTION_NAMES, np.asarray(action).reshape(-1))
        })
        rows.append(row)
        total_reward += reward
        if truncated:
            break
    summary = write_results(run_name, rows, total_reward)
    if persona is not None:
        summary.update(score_rl_roleplay(rows, summary, persona))
        write_metrics_summary(run_name, summary)
    summary["model_path"] = str(model_path)
    summary["training_total_timesteps"] = total_timesteps
    (OUTPUT_DIR / f"{run_name}_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def score_rl_roleplay(rows: List[Dict[str, Any]], summary: Dict[str, Any], persona: dict) -> Dict[str, Any]:
    """Use the same roleplay scorer as the agent benchmark for RL event outcomes."""
    bench_dir = PROJECT_ROOT / "experiments" / "benchmark"
    if str(bench_dir) not in sys.path:
        sys.path.insert(0, str(bench_dir))
    from user_pref_scorer import score_user_preference

    scores: List[float] = []
    details: List[Dict[str, Any]] = []
    for event_index, event_date in enumerate(sorted({
        datetime.fromisoformat(str(row["timestamp"])).date().isoformat()
        for row in rows if int(row.get("vpp_active", 0)) == 1
    }), start=1):
        event_rows = [
            row for row in rows
            if int(row.get("vpp_active", 0)) == 1
            and datetime.fromisoformat(str(row["timestamp"])).date().isoformat() == event_date
        ]
        mean_temp = sum(float(row["indoor_temperature_c"]) for row in event_rows) / max(1, len(event_rows))
        comfort_ok = sum(
            1 for row in event_rows if 23.0 <= float(row["indoor_temperature_c"]) <= 26.0
        ) / max(1, len(event_rows))
        mean_setpoint = sum(float(row["cooling_setpoint_c"]) for row in event_rows) / max(1, len(event_rows))
        result = score_user_preference(
            building="family",
            method="rl",
            mean_temp_c=mean_temp,
            pmv_ok_fraction=comfort_ok,
            energy_kwh_per_day=float(summary["energy_kwh_per_day"]),
            agent_setpoint_c=mean_setpoint,
            event_index=event_index,
            user_preference_text=persona.get("llm_prompts", {}).get("system_prompt", ""),
            agent_reason="PPO baseline policy selected actions from the shared household state.",
            persona=persona,
        )
        if result.get("source") != "roleplay_llm":
            raise RuntimeError(
                f"Role-play LLM scoring is required; event {event_index} returned {result.get('source')}"
            )
        score = float(result.get("score", 0.0) or 0.0)
        scores.append(score)
        details.append({"event": event_index, **result})
    return {
        "user_pref_scores": scores,
        "user_satisfaction": sum(scores) / len(scores) if scores else None,
        "roleplay_event_scores": details,
    }


def main() -> None:
    global OUTPUT_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--ppo-timesteps", type=int, default=1536)
    parser.add_argument("--formal", action="store_true",
                        help="Train a formal PPO model; defaults to 100,000 steps unless --ppo-timesteps is set.")
    parser.add_argument("--persona", default=None,
                        help="Persona ID or JSON path used by the shared roleplay scorer.")
    parser.add_argument("--output", default=None,
                        help="Override output directory for this comparison run.")
    args = parser.parse_args()
    if args.output:
        OUTPUT_DIR = Path(args.output)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    schedules = generate_typical_week(DEFAULT_SEED)
    schedule_outputs = write_schedule_outputs(schedules, OUTPUT_DIR, DEFAULT_SEED)
    baseline_summary = run_policy("baseline", baseline_policy, DEFAULT_SEED)
    random_summary = run_policy("random_policy", random_policy, DEFAULT_SEED)
    timesteps = args.ppo_timesteps
    if args.formal and timesteps == 1536:
        timesteps = 100_000
    run_name = "ppo_formal" if args.formal else "ppo_smoke"
    persona = load_persona(args.persona) if args.persona else None
    ppo_summary = run_ppo(DEFAULT_SEED, total_timesteps=timesteps, run_name=run_name, persona=persona)
    final = {
        "validation_ok": baseline_summary["validation_ok"] and ppo_summary["validation_ok"],
        "seed": DEFAULT_SEED,
        "schedule_outputs": schedule_outputs,
        "baseline": baseline_summary,
        "random_policy": random_summary,
        run_name: ppo_summary,
    }
    final_path = OUTPUT_DIR / "typical_human_experiment_summary.json"
    final_path.write_text(json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(final, indent=2, ensure_ascii=False))


def load_persona(persona_arg: str) -> dict:
    path = Path(persona_arg)
    if not path.exists():
        path = PROJECT_ROOT / "energybridge" / "roleplay" / "personas" / f"{persona_arg}.json"
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
