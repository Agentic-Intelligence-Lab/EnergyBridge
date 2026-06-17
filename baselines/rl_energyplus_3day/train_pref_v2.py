"""Train PPO v2 with expanded action, price, and preference-aware observation.

Usage:
  python -m baselines.rl_energyplus_3day.train_pref_v2 \
    --hours 0.1 --timesteps 2000 \
    --persona basic_role_a_commuter_price_cooperative \
    --city Germany --start-date 2025-06-01 \
    --price-csv experiments/real_data/germany_2025_price.csv \
    --output benchmark_results/rl_ppo_pref_v2_smoke
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback, CheckpointCallback

from .environment_pref_v2 import EnergyPlusFamilyEnvV2


class WallClockStopCallback(BaseCallback):
    def __init__(self, seconds: float):
        super().__init__()
        self.deadline = time.monotonic() + seconds

    def _on_step(self) -> bool:
        return time.monotonic() < self.deadline


def evaluate(model: PPO, output: Path, persona_id: str,
             city: str, start_date: str, price_csv: str) -> dict:
    env = EnergyPlusFamilyEnvV2(
        output / "eval_ep", persona_id=persona_id,
        city=city, start_date=start_date, price_csv=price_csv,
    )
    obs, _ = env.reset()
    terminated = False
    while not terminated:
        action, _ = model.predict(obs, deterministic=True)
        obs, _, terminated, _, _ = env.step(action)
    rows = env.rows
    env.close()

    with (output / "evaluation_timeseries.csv").open("w", newline="", encoding="utf-8") as handle:
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    occupied = [r for r in rows if 8 <= r["sim_hour"] % 24 < 22]
    vpp = [r for r in rows if r["vpp_active"]]
    occupied_rows = occupied if occupied else rows
    vpp_rows = vpp if vpp else rows

    vpp_actions = []
    for ei in range(3):
        start, end = 18.0 + ei * 24.0, 19.0 + ei * 24.0
        er = [r for r in rows if start <= r["sim_hour"] < end]
        if er:
            vpp_actions.append({
                "event": ei + 1,
                "cooling_setpoint_c": sum(r["cooling_setpoint_c"] for r in er) / len(er),
                "washer_started": any(r.get("washer_start_request", 0) >= 8.0 for r in er),
                "dishwasher_started": any(r.get("dishwasher_start_request", 0) >= 9.0 for r in er),
                "ewh_heating": any(r.get("water_heater_preheat_request", 0) >= 0.5 for r in er),
            })

    summary = {
        "environment": "EnergyPlus family_simple_3day v2",
        "city": city, "start_date": start_date, "price_csv": price_csv,
        "persona": persona_id,
        "total_energy_kwh": sum(r["energy_kwh"] for r in rows),
        "vpp_window_energy_kwh": sum(r["energy_kwh"] for r in vpp_rows),
        "mean_indoor_temperature_c": sum(r["indoor_temperature_c"] for r in occupied_rows) / len(occupied_rows),
        "comfort_ok_fraction": sum(23 <= r["indoor_temperature_c"] <= 26 for r in occupied_rows) / len(occupied_rows),
        "pmv_ok_fraction": sum(abs(r.get("pmv", 0)) <= 0.5 for r in occupied_rows) / len(occupied_rows),
        "mean_vpp_actions": {
            "cooling_setpoint_c": sum(r["cooling_setpoint_c"] for r in vpp_rows) / len(vpp_rows),
        } if vpp_rows else {},
        "vpp_actions": vpp_actions,
        "total_reward": sum(r["reward"] for r in rows),
        "mean_reward_per_step": sum(r["reward"] for r in rows) / max(1, len(rows)),
    }
    summary.update(_score_roleplay(rows, summary, persona_id))
    (output / "evaluation_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_summary(output, summary)
    return summary


def _score_roleplay(rows: list[dict], summary: dict, persona_id: str) -> dict:
    import sys
    bench_dir = Path(__file__).resolve().parents[2] / "experiments" / "benchmark"
    if str(bench_dir) not in sys.path:
        sys.path.insert(0, str(bench_dir))
    from user_pref_scorer import score_user_preference

    persona_path = Path(__file__).resolve().parents[2] / "energybridge" / "roleplay" / "personas" / f"{persona_id}.json"
    persona = json.loads(persona_path.read_text(encoding="utf-8"))
    scores = []
    for ei in range(3):
        start, end = 18.0 + ei * 24.0, 19.0 + ei * 24.0
        er = [r for r in rows if start <= r["sim_hour"] < end]
        if not er:
            scores.append(0)
            continue
        mean = lambda key: sum(float(r.get(key, 0)) for r in er) / len(er)
        result = score_user_preference(
            building="family", method="rl",
            mean_temp_c=mean("indoor_temperature_c"),
            pmv_ok_fraction=sum(abs(r.get("pmv", 0)) <= 0.5 for r in er) / len(er),
            energy_kwh_per_day=summary["total_energy_kwh"] / 3.0,
            agent_setpoint_c=mean("cooling_setpoint_c"),
            event_index=ei + 1,
            user_preference_text=persona["llm_prompts"]["system_prompt"],
            agent_reason="PPO v2 baseline controlled the three-day EnergyPlus family model.",
            persona=persona,
        )
        if result.get("source") != "roleplay_llm":
            raise RuntimeError(f"Role-play LLM required, got {result.get('source')}")
        scores.append(float(result["score"]))
    return {"user_pref_scores": scores, "user_satisfaction": sum(scores) / len(scores)}


def _write_summary(output: Path, summary: dict) -> None:
    lines = [
        "─" * 62,
        "  RL PPO Pref-v2 Key Metrics Summary",
        "─" * 62,
        f"  City              : {summary.get('city', '?')}",
        f"  Start date        : {summary.get('start_date', '?')}",
        f"  Price CSV         : {summary.get('price_csv', 'N/A') or 'N/A'}",
        f"  Persona           : {summary.get('persona', '?')}",
        f"  VPP-window energy : {summary['vpp_window_energy_kwh']:.3f} kWh",
        f"  Total energy      : {summary['total_energy_kwh']:.2f} kWh (3 days)",
        f"  Mean satisfaction : {summary.get('user_satisfaction', 0):.1f}/5",
        "  Per-event scores  : " + "  ".join(
            f"VPP{i+1}:{s:.0f}" for i, s in enumerate(summary.get("user_pref_scores", []))
        ),
        f"  Mean indoor temp  : {summary['mean_indoor_temperature_c']:.2f} °C",
        f"  PMV pass rate     : {summary['pmv_ok_fraction']*100:.1f}%",
        f"  Comfort-zone pass : {summary['comfort_ok_fraction']*100:.1f}% (23-26°C)",
        f"  Total reward      : {summary.get('total_reward', 0):.1f}",
        f"  Mean reward/step  : {summary.get('mean_reward_per_step', 0):.4f}",
        "  Token usage       : N/A (RL inference does not call LLM)",
        "─" * 62,
    ]
    (output / "run_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=4.0)
    parser.add_argument("--timesteps", type=int, default=100_000)
    parser.add_argument("--output", type=Path, default=Path("benchmark_results/rl_ppo_pref_v2_formal"))
    parser.add_argument("--device", default="auto")
    parser.add_argument("--persona", default="basic_role_a_commuter_price_cooperative")
    parser.add_argument("--city", default="Tianjin")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--price-csv", default="")
    parser.add_argument("--resume", type=Path, default=None)
    parser.add_argument("--reward-mode", default="default", choices=["default", "mpc_aligned_v1"],
                        help="Reward mode: default=legacy, mpc_aligned_v1=MPC-aligned shared cost")
    parser.add_argument("--checkpoint-every", type=int, default=50000,
                        help="Save checkpoint every N timesteps. 0 to disable.")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    print(f"Training RL PPO Pref-v2: persona={args.persona} city={args.city} "
          f"start={args.start_date or 'default'} price={args.price_csv or 'N/A'} "
          f"reward_mode={args.reward_mode}")
    print(f"  output={args.output}  device={args.device}  hours={args.hours}  timesteps={args.timesteps}")

    env_kwargs = dict(
        persona_id=args.persona, city=args.city,
        start_date=args.start_date, price_csv=args.price_csv,
        reward_mode=args.reward_mode,
    )
    env = EnergyPlusFamilyEnvV2(args.output / "train_ep", **env_kwargs)

    if args.resume:
        model = PPO.load(args.resume, env=env, device=args.device, print_system_info=False)
        print(f"Resuming from {args.resume} at {model.num_timesteps} timesteps")
    else:
        model = PPO(
            "MlpPolicy", env, verbose=1, device=args.device,
            n_steps=432, batch_size=144, learning_rate=3e-4, gamma=0.995,
            policy_kwargs={"net_arch": [256, 256]},
        )

    # Custom checkpoint saver with named files
    class _NamedCheckpointCallback(BaseCallback):
        def __init__(self, interval: int, save_dir: Path):
            super().__init__()
            self.interval = interval
            self.save_dir = save_dir
            self._last_saved = 0

        def _on_step(self) -> bool:
            current = self.model.num_timesteps
            if current - self._last_saved >= self.interval:
                step_str = f"{current:06d}"
                path = self.save_dir / f"ppo_energyplus_3day_step_{step_str}.zip"
                self.model.save(path)
                self._last_saved = current
                print(f"  [Checkpoint] saved {path}")
            return True

    callbacks = [
        WallClockStopCallback(args.hours * 3600),
        CheckpointCallback(save_freq=4320, save_path=str(args.output / "checkpoints")),
    ]
    if args.checkpoint_every > 0:
        callbacks.append(_NamedCheckpointCallback(args.checkpoint_every, args.output))

    started = time.monotonic()
    model.learn(
        total_timesteps=args.timesteps, callback=callbacks, progress_bar=False,
        reset_num_timesteps=not bool(args.resume),
    )
    elapsed = time.monotonic() - started
    model.save(args.output / "ppo_energyplus_3day")
    env.close()

    summary = evaluate(model, args.output, args.persona, args.city, args.start_date, args.price_csv)
    summary.update({"training_elapsed_seconds": elapsed, "training_timesteps": model.num_timesteps})
    (args.output / "formal_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
