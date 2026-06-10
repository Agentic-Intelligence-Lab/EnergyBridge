# RL Typical Human Baseline

This directory merges the reference Typical Human baseline/random/PPO smoke
experiment as an independent baseline. It intentionally uses the reference
lightweight Gymnasium simulator and does not launch EnergyPlus.

The reference full simulator contract is Sinergym 3.12 with EnergyPlus 25.1.
EnergyBridge's production benchmark remains on EnergyPlus 24.1. A runnable
standard EnergyPlus 25.1 installation is available for the independent
reference reproduction, but this lightweight environment does not use it.

Run:

```bash
python -m baselines.rl_typical_human.run_experiment --ppo-timesteps 1536
```

For a quick pipeline check:

```bash
python -m baselines.rl_typical_human.run_experiment --ppo-timesteps 64
```

For the formal PPO entry point:

```bash
python -m baselines.rl_typical_human.run_experiment --formal
```

For a comparison scored by the same role-play persona scorer as the agent:

```bash
python -m baselines.rl_typical_human.run_experiment \
  --ppo-timesteps 64 --persona atom_comfort_sensitive
```

`--formal` defaults to 100,000 training steps and writes
`outputs/ppo_formal_model.zip`, `ppo_formal_summary.json`, and
`ppo_formal_run_summary.txt`. A short formal run proves that the training
pipeline works, but it is not evidence of convergence. The current environment
still needs reward tuning and preferably binary-action handling or action masks
before the policy can reliably satisfy hot-water, EV, and task deadlines.

## RL contract

Observation space: 26 continuous values covering cyclic time, weekday, tariff,
occupancy, EV presence/SOC, water and indoor/outdoor temperatures, recent
household/device power, comfort violation, VPP-active state, VPP time remaining,
reference capacity quantification (`committable_kw`, `recommended_bid_kw`,
`success_probability`), and state/progress pairs for dishwasher, washer, and
dryer.

Action space: 7 continuous values: heating setpoint, cooling setpoint, EV charge
request, water-heater request, and start requests for the three task appliances.

Reward is the negative sum of electricity cost, thermal comfort violation,
hot-water violation, EV deadline violation, task deadline violation, and an
additional household-energy penalty during the first three 18:00-19:00 VPP
events.

Every evaluated policy writes an EnergyBridge-style metrics summary. Passing
`--persona` scores the PPO evaluation with the same role-play scorer and persona
JSON used by the agent benchmark. Role-play scoring is strict: a remote LLM
failure fails the scored run rather than silently accepting a rule-based score.
The summary is generated automatically after every evaluation and includes
named mean actions and reference capacity assessments for each VPP event. PMV
remains `N/A` because this lightweight RL simulator has no PMV model; token use
is always `N/A` because RL inference does not call an LLM.
