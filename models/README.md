# RL PPO Pref-v2 Trained Models

## Checkpoints

| File | Training Config | Steps |
|---|---|---|
| `rl_ppo_pref_v2_tianjin.zip` | Tianjin weather, no price data | ~10M steps |
| `rl_ppo_pref_v2_germany.zip` | Germany weather + real day-ahead price | ~10M steps |

Both trained on `all_appliances_full` persona (washer + dishwasher + dryer + water_heater + EV),
96 parallel EnergyPlus envs, 41-dim observation space and 8-dim action space.

---

## Load Checkpoint

```python
from stable_baselines3 import PPO

model = PPO.load("models/rl_ppo_pref_v2_tianjin.zip", device="cpu")
```

---

## Reproduce Benchmark

```bash
# Single-user Tianjin (use Tianjin-trained model)
ENERGYBRIDGE_RL_PREF_V2_MODEL=models/rl_ppo_pref_v2_tianjin.zip \
python experiments/benchmark/run_baseline_matrix.py \
  --methods rl_ppo_pref_v2 --days 7 --city Tianjin --workers 5

# Single-user Germany (use Germany-trained model with real price data)
ENERGYBRIDGE_RL_PREF_V2_MODEL=models/rl_ppo_pref_v2_germany.zip \
python experiments/benchmark/run_baseline_matrix.py \
  --methods rl_ppo_pref_v2 --days 7 --city Germany \
  --start-date 2025-06-01 --workers 5 \
  --price-csv experiments/real_data/germany_2025_price.csv

# Multi-user household Tianjin
ENERGYBRIDGE_RL_PREF_V2_MODEL=models/rl_ppo_pref_v2_tianjin.zip \
python experiments/benchmark/run_household_matrix.py \
  --methods rl_ppo_pref_v2 --days 7 --city Tianjin --workers 5

# Multi-user household Germany
ENERGYBRIDGE_RL_PREF_V2_MODEL=models/rl_ppo_pref_v2_germany.zip \
python experiments/benchmark/run_household_matrix.py \
  --methods rl_ppo_pref_v2 --days 7 --city Germany \
  --start-date 2025-06-01 --workers 5 \
  --price-csv experiments/real_data/germany_2025_price.csv
```

---

## Retrain From Scratch

### Tianjin (no price data)

```bash
python -m baselines.rl_energyplus.train_pref_v2 \
  --persona all_appliances_full \
  --city Tianjin \
  --n-envs 96 \
  --hours 10 \
  --timesteps 10000000 \
  --output benchmark_results/rl_ppo_pref_v2_tianjin_retrain
```

### Germany (with real day-ahead price)

```bash
python -m baselines.rl_energyplus.train_pref_v2 \
  --persona all_appliances_full \
  --city Germany \
  --start-date 2025-06-01 \
  --price-csv experiments/real_data/germany_2025_price.csv \
  --n-envs 96 \
  --hours 10 \
  --timesteps 10000000 \
  --output benchmark_results/rl_ppo_pref_v2_germany_retrain
```

After training, copy the output model to `models/` and run benchmark as above.

---

## Checkpoint Action Space (8-dim)

| dim | meaning | range |
|---|---|---|
| 0 | AC setpoint | [22, 28] °C |
| 1 | Washer start hour | [8, 19] h |
| 2 | Dishwasher start hour | [19, 21.5] h |
| 3 | WH preheat start hour | [7, 17] h |
| 4 | WH target temperature | [45, 75] °C |
| 5 | EV charge start hour | [18.5, 20] h |
| 6 | EV charge end hour | [4, 7.5] h |
| 7 | Dryer start hour | [8, 19.5] h |
