"""Run a short, faithful PPO smoke test on the reference Sinergym environment."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np


class WallClockStopCallback:
    """Build an SB3 callback that stops training after a wall-clock budget."""

    @staticmethod
    def create(seconds: float):
        from stable_baselines3.common.callbacks import BaseCallback

        class _WallClockStopCallback(BaseCallback):
            def __init__(self) -> None:
                super().__init__()
                self.started = 0.0

            def _on_training_start(self) -> None:
                self.started = time.monotonic()

            def _on_step(self) -> bool:
                return time.monotonic() - self.started < seconds

        return _WallClockStopCallback()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=288)
    parser.add_argument("--minutes", type=float, default=None)
    parser.add_argument(
        "--reference-examples",
        type=Path,
        default=Path(
            os.getenv(
                "ENERGYBRIDGE_REFERENCE_EXAMPLES_ROOT",
                "../reference/examples",
            )
        ),
    )
    parser.add_argument(
        "--energyplus",
        type=Path,
        default=Path(os.getenv("EPLUS_PATH", "/opt/EnergyPlus-25-1-0")),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark_results/reference_sinergym_ep25_smoke"),
    )
    args = parser.parse_args()

    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["EPLUS_PATH"] = str(args.energyplus.resolve())
    sys.path.insert(0, str(args.energyplus.resolve()))
    sys.path.insert(0, str(args.reference_examples.resolve()))

    import gymnasium as gym
    import sinergym.config.modeling as modeling
    from stable_baselines3 import PPO
    from stable_baselines3.common.monitor import Monitor

    from Family_Env.scripts.register_tianjin_family_env import register_env

    modeling.CWD = str(args.output / "sinergym")
    Path(modeling.CWD).mkdir(parents=True, exist_ok=True)
    env_id = register_env(force=True)
    env = Monitor(gym.make(env_id))
    unwrapped = env.unwrapped

    model = PPO(
        "MlpPolicy",
        env,
        verbose=1,
        seed=20260601,
        n_steps=144,
        batch_size=48,
        learning_rate=3e-4,
        device="cpu",
    )
    started = time.monotonic()
    callback = WallClockStopCallback.create(args.minutes * 60.0) if args.minutes else None
    model.learn(total_timesteps=args.timesteps, callback=callback, progress_bar=False)
    training_elapsed_seconds = time.monotonic() - started
    model_path = args.output / "ppo_reference_sinergym_ep25"
    model.save(model_path)
    env.close()

    eval_env = gym.make(env_id)
    obs, _ = eval_env.reset(seed=20260601)
    total_reward = 0.0
    rewards: list[float] = []
    actions: list[list[float]] = []
    terminated = truncated = False
    while not (terminated or truncated):
        action, _ = model.predict(obs, deterministic=True)
        obs, reward, terminated, truncated, _ = eval_env.step(action)
        total_reward += float(reward)
        rewards.append(float(reward))
        actions.append(np.asarray(action, dtype=float).tolist())
    eval_env.close()

    summary = {
        "environment": env_id,
        "energyplus_version": "25.1.0-68a4a7c774",
        "reference_target_version": "25.1.0-1c11a3d85f",
        "requested_training_timesteps": args.timesteps,
        "training_timesteps": model.num_timesteps,
        "training_elapsed_seconds": training_elapsed_seconds,
        "episode_steps": len(rewards),
        "action_space": str(unwrapped.action_space),
        "action_variables": list(unwrapped.action_variables),
        "observation_space": str(unwrapped.observation_space),
        "observation_variables": list(unwrapped.observation_variables),
        "reward": "sinergym.utils.rewards.LinearReward",
        "evaluation_total_reward": total_reward,
        "evaluation_mean_step_reward": float(np.mean(rewards)),
        "evaluation_mean_action": np.mean(np.asarray(actions), axis=0).tolist(),
        "model_path": str(model_path.with_suffix(".zip")),
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
