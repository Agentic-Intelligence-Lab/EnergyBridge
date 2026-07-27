# Reference Sinergym RL Baseline

This baseline reproduces the unmodified base Sinergym environment from an
external `Family_Env` checkout.

- Sinergym: 3.12.0
- EnergyPlus runtime: 25.1.0 standard Ubuntu 22.04 build
- Reference target: 25.1.0-1c11a3d85f Ubuntu 24.04 special build
- Episode: three days, 432 ten-minute steps
- Action space: heating setpoint `[15, 22]` and cooling setpoint `[24, 30]`
- Observation space: 28 EnergyPlus/time variables
- Reward: the reference Sinergym `LinearReward`

Set `EPLUS_PATH` to a compatible EnergyPlus 25.1.0 installation and
`ENERGYBRIDGE_REFERENCE_EXAMPLES_ROOT` to the external examples checkout.

Run a short PPO smoke test:

```bash
EPLUS_PATH=/opt/EnergyPlus-25-1-0 \
python \
  baselines/rl_sinergym_reference/run_smoke.py \
  --timesteps 288 \
  --output benchmark_results/reference_sinergym_ep25_smoke
```
