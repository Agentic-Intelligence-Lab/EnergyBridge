# Reference Sinergym RL Baseline

This baseline reproduces the unmodified base Sinergym environment from
`/home/hku_user/work/reference/examples/Family_Env`.

- Sinergym: 3.12.0
- EnergyPlus runtime: 25.1.0 standard Ubuntu 22.04 build
- Reference target: 25.1.0-1c11a3d85f Ubuntu 24.04 special build
- Episode: three days, 432 ten-minute steps
- Action space: heating setpoint `[15, 22]` and cooling setpoint `[24, 30]`
- Observation space: 28 EnergyPlus/time variables
- Reward: the reference Sinergym `LinearReward`

The installed special reference build is preserved at
`/home/hku_user/EnergyPlus-25-1-0`, but it requires Ubuntu 24.04 glibc. The
standard build at `/home/hku_user/EnergyPlus-25-1-0-standard` runs the same
reference epJSON successfully on this Ubuntu 22.04 host.

Run a short PPO smoke test:

```bash
EPLUS_PATH=/home/hku_user/EnergyPlus-25-1-0-standard \
/home/hku_user/miniconda3/envs/sinergym_ep24/bin/python \
  baselines/rl_sinergym_reference/run_smoke.py \
  --timesteps 288 \
  --output benchmark_results/reference_sinergym_ep25_smoke
```
