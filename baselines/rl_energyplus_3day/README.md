# RL EnergyPlus Three-Day Baseline

This is the main RL baseline for comparison with the EnergyBridge agent. It
trains PPO directly against the same EnergyPlus 24.1 three-day family model,
Tianjin weather, appliance configuration, and daily 18:00-19:00 VPP windows
used by the agent benchmark.

Use the other RL directories only for their narrower purposes:

- `baselines/rl_energyplus_3day`: comparable three-day EnergyPlus baseline.
- `baselines/rl_sinergym_reference`: faithful reference Sinergym + EnergyPlus
  25.1 reproduction path.
- `baselines/rl_typical_human`: fast seven-day lightweight simulator for
  pipeline and reward experiments; it is not directly comparable to the agent.

## Requirements

- Conda environment: `energybridge`
- EnergyPlus 24.1: `/home/hku_user/EnergyPlus-24-1-0`, or override it with
  `EPLUS_ROOT`
- Python packages: `gymnasium`, `numpy`, `stable-baselines3`, `torch`
- Remote LLM credentials in `.env` for the mandatory final role-play scoring

Run commands from the repository root:

```bash
cd /home/hku_user/work/EnergyBridge
conda activate energybridge
```

## Quick validation

A short run validates EnergyPlus startup, PPO training, model saving, metrics
generation, and role-play scoring. The current Python API callback produces
about 465 transitions per three-day episode. PPO's configured `n_steps=432` is
a rollout-buffer size, not the exact episode length.

```bash
python -m baselines.rl_energyplus_3day.train \
  --hours 0.03 \
  --timesteps 432 \
  --persona atom_comfort_sensitive \
  --output benchmark_results/rl_energyplus_smoke
```

This is only a pipeline check. It is not evidence that PPO has learned a useful
policy.

## Formal training and continuation

Start a four-hour GPU-enabled run:

```bash
python -m baselines.rl_energyplus_3day.train \
  --hours 4 \
  --device cuda \
  --persona atom_comfort_sensitive \
  --output benchmark_results/atom_comfort_sensitive_rl_energyplus_4h
```

The trainer writes periodic checkpoints and always saves the final reusable
model as `ppo_energyplus_3day.zip`.

Continue training from either the final model or a checkpoint:

```bash
python -m baselines.rl_energyplus_3day.train \
  --resume benchmark_results/atom_comfort_sensitive_rl_energyplus_4h/ppo_energyplus_3day.zip \
  --hours 4 \
  --device cuda \
  --persona atom_comfort_sensitive \
  --output benchmark_results/atom_comfort_sensitive_rl_energyplus_8h
```

`--hours` is a wall-clock limit. `--timesteps` is an upper bound and defaults
high enough that the wall-clock callback normally stops formal training first.

## Environment contract

One episode covers the same 72 hours as the agent benchmark.

Observation space: 44 continuous values designed to match the context exposed
to the Agent LLM as closely as a numeric PPO observation can:

1. Hour-of-day and remaining simulation time.
2. Indoor and outdoor temperature.
3. VPP-active state, analogous VPP target, committable capacity, recommended
   bid, and per-device capacity-constraint flags. Capacity context is zeroed
   outside VPP windows because the Agent only receives it during VPP events.
4. Presence, task state, scheduled time, and allowed window for washer,
   dishwasher, and dryer.
5. Water-heater presence, preheat state/window, and bath-required time.
6. EV presence, SOC, target SOC, at-home state, charging mode/window, and
   arrival time.
7. Refrigerator presence and configured uncontrollable power.

Optional or currently unscheduled appliance times use `-1` as a numeric
sentinel.

Facility power, current cooling setpoint, capacity success probability, and
engineered comfort-distance features are deliberately excluded because the
Agent prompt does not expose them. The Agent receives user-preference and
historical-feedback text; these cannot be represented losslessly as fixed
numeric PPO features, so they remain implicit in the selected persona and
reward rather than being replaced with arbitrary encodings.

The 44-value schema replaces the earlier 14-value RL observation. Models
trained with the old schema are not compatible and must be retrained rather
than passed through `--resume`.

Action space: three normalized PPO outputs in `[-1, 1]`:

| PPO action | Physical action |
|---|---|
| `action[0]` | Cooling setpoint mapped to `22-28°C` |
| `action[1]` | Washer start request mapped to `0-1`; starts at `>=0.5` |
| `action[2]` | Water-heater preheat request mapped to `0-1`; starts at `>=0.5` outside VPP |

Reward at each step is the negative sum of:

- Energy consumption in kWh.
- Occupied thermal comfort violation outside `23-26°C`, weighted by `5`.
- Occupied persona comfort violation outside `24.5-25.5°C`, weighted by `2`.
- Additional VPP-window energy consumption, weighted by `5`.

At episode end, PPO receives up to `+50` for washer completion and `+20` for
water-heater completion. These weights are current tuning choices, not values
copied from the reference baseline.

## Outputs

Each training directory contains:

| File | Purpose |
|---|---|
| `ppo_energyplus_3day.zip` | Final reusable PPO model |
| `checkpoints/*.zip` | Periodic continuation checkpoints |
| `evaluation_timeseries.csv` | Deterministic evaluation trajectory |
| `evaluation_summary.json` | Machine-readable evaluation metrics |
| `formal_summary.json` | Evaluation metrics plus training time and steps |
| `run_summary.txt` | Human-readable comparison summary |

The evaluation summary includes EnergyPlus energy and comfort metrics, readable
washer/EWH request and actual-start times, VPP actions, current-state capacity,
and mandatory role-play LLM scores. RL inference itself consumes no LLM tokens;
only the final role-play scoring calls the remote LLM.

## Known issues and tuning priorities

- The current reward strongly favors low setpoints and low VPP energy. A
  successor should inspect reward-component curves and tune weights rather than
  judging convergence from total reward alone.
- Washer and EWH commands are continuous PPO outputs converted to binary
  requests at `0.5`. Repeated requests are possible. Binary or hybrid actions,
  action masking, or explicit cooldown features may train more reliably.
- The EWH model exposes schedule/use state rather than a physical tank
  temperature. Its completion metric is therefore a proxy, not a hot-water
  comfort measurement.
- Capacity in the observation is state-dependent remaining flexibility after
  previous policy actions. For a fair cross-method capacity comparison, also
  report the common reference A3 counterfactual capacity from an identical
  prediction window.
- The three-day episode is expensive and supplies few VPP transitions. Reward
  normalization, more randomized episodes, curriculum training, and multiple
  seeds should be tested before treating a trained policy as a strong baseline.
- Role-play scoring requires network access and may add variance. Keep the same
  persona JSON and record all event scores when comparing methods.
- Always inspect `evaluation_timeseries.csv` to verify physical actions. Mean
  actions alone can hide whether a device ever crossed the `0.5` start
  threshold.

## Suggested handoff workflow

1. Run one 432-step smoke test and inspect all generated files.
2. Plot each reward component, physical action, temperature, and VPP energy.
3. Tune with at least three seeds and save every model/checkpoint.
4. Compare deterministic evaluations using the same persona and three-day
   scenario as the agent.
5. Report both policy-dependent current-state capacity and common reference A3
   capacity, clearly labeled.
