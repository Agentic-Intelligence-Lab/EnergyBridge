# RL EnergyPlus Baseline

This directory contains RL baselines for the EnergyBridge family benchmark:

- **`train_pref_v2.py` + `environment_pref_v2.py`** — **Production v2 baseline (8-dim, preference-aware)**. Used by the family benchmark inference path via `experiments/benchmark/baselines/rl_ppo_pref_v2.py`. Trained checkpoints exposed as `models/rl_ppo_pref_v2_{tianjin,germany}.zip`.
- **`train.py` + `environment.py`** — Legacy v1 baseline (3-dim, retained for reference). See "Legacy v1" section at the end.

## v2 (8-dim, production)

Preference-aware PPO baseline with expanded action space covering all
controllable appliances. The trained checkpoints are the ones used by
`family_runner` when running `rl_ppo_pref_v2` benchmarks.

### Requirements

- Conda env `energybridge` with `stable-baselines3`, `torch`, `gymnasium`, `pyenergyplus`
- EnergyPlus 24.1: `EPLUS_ROOT=/path/to/EnergyPlus-24-1-0`
- LLM credentials in `.env` for the final role-play scoring
- Multi-core server for parallel training (script uses `SubprocVecEnv`)

Run from the repo root.

### Training backend: dynamic model (train) vs EnergyPlus (test)

Training and benchmark evaluation are **split**:

- **Training** (`--backend dynamic`, default): the environment steps against
  the MPC dynamic model (`experiments/benchmark/baselines/mpc/dynamic_model`)
  instead of EnergyPlus. It is a synchronous, pure-Python 5R3C thermal +
  behavior-MDP model — no `pyenergyplus` calls, no threading, ~150-250 µs per
  step. `--city` is routed to a calibrated region via
  `dynamic_model_region_for_state`: `Tianjin` → the legacy Tianjin 5R3C
  parameters, `Germany` → the Berlin regional 5R3C assets
  (`dynamic_model/assets/regional_5r3c/berlin/`). This is the same dynamic
  model the `mpc_dynamic` baseline uses for planning, so it stays in sync
  with any future region/parameter updates on that side automatically
  (imported, not copied).
- **Benchmark evaluation** is unchanged: `family_runner` always drives the
  real EnergyPlus 24.1 family model, so reported scores are directly
  comparable to every other baseline regardless of which backend trained
  the checkpoint.
- **`--backend ep`** (legacy) trains directly against EnergyPlus instead,
  ~7-10x slower but useful as a reference/fallback path. Both backends share
  the same 8-dim action / 41-dim observation space, so checkpoints trained
  with one backend can be evaluated, resumed, or fine-tuned with the other.

Both cities use the same PPO hyperparameters (10M timesteps). Sim length and
region/IDF are auto-selected from `--city`.

**Tianjin** (3-day sim, dynamic model uses Tianjin 5R3C params; ~35-70 min on 32 parallel envs):

```bash
python -m baselines.rl_energyplus.train_pref_v2 \
    --backend dynamic --city Tianjin --persona all_appliances_full \
    --price-csv experiments/real_data/tianjin_tou_price_normalized.csv \
    --hours 3 --timesteps 10000000 --n-envs 32 --device cpu \
    --output benchmark_results/rl_dyn_v2_tianjin
```

**Germany** (7-day sim, dynamic model uses Berlin 5R3C params; ~35-70 min on 32 parallel envs):

```bash
python -m baselines.rl_energyplus.train_pref_v2 \
    --backend dynamic --city Germany --persona all_appliances_full \
    --start-date 2025-06-01 \
    --price-csv experiments/real_data/germany_2025_price.csv \
    --hours 3 --timesteps 10000000 --n-envs 32 --device cpu \
    --output benchmark_results/rl_dyn_v2_germany
```

For the legacy EnergyPlus-backed path, add `--backend ep --device cuda:0`
(or `cuda:1`), drop `--n-envs 32` to `96`, and expect ~4h per city
(EnergyPlus is CPU-bound per-env, not GPU-bound, but a GPU still speeds up
the PPO policy update).

The trainer writes periodic checkpoints under `<output>/` and a final
`ppo_energyplus_3day.zip`. Copy the finals to `models/`:

```bash
cp benchmark_results/rl_dyn_v2_tianjin/ppo_energyplus_3day.zip models/rl_ppo_pref_v2_tianjin.zip
cp benchmark_results/rl_dyn_v2_germany/ppo_energyplus_3day.zip models/rl_ppo_pref_v2_germany.zip
```

### Key hyperparameters

| Param | Value | Notes |
|---|---|---|
| `n_steps` | 432 | Matches 3-day episode (72h × 6 steps/h) |
| `batch_size` | `min(n_envs × 432, 2048)` | Scales with parallelism |
| `learning_rate` | 3e-4 | Standard PPO |
| `gamma` | 0.995 | Long-horizon appliance scheduling |
| `policy_kwargs.net_arch` | `[256, 256]` | MLP |
| `--timesteps` | 10M | Convergence based on terminal_bonus plateau |
| `--n-envs` | 32 (dynamic) / 96 (ep) | Parallel env instances via `SubprocVecEnv`; dynamic model is lightweight so fewer workers still saturate the CPU |
| `--backend` | `dynamic` (default) / `ep` | See "Training backend" below |

### Action space (8 dims, `_decode_action_v2`)

| Dim | Physical | Decode range |
|---|---|---|
| 0 | AC cooling setpoint | [22, 28]°C |
| 1 | Washer start | [8.0, 19.0]h |
| 2 | Dishwasher start | [19.0, 21.5]h (overnight-first) |
| 3 | WH preheat start | [7.0, 17.0]h |
| 4 | WH target temp | [45, 75]°C |
| 5 | EV charge start | [18.5, 20.0]h (avoid last-day sim cutoff) |
| 6 | EV charge end | [4.0, 7.5]h |
| 7 | Dryer start | [8.0, 19.5]h |

Ranges are tuned to match the training persona `all_appliances_full` valid
schedule windows, so PPO output is always physically valid without extra clip.

### Observation space (41 dims, `OBSERVATION_NAMES_V2`)

Time features (4) + thermal + VPP context + shiftable-appliance states (washer,
dishwasher) + WH state + EV state + price features + preference proxy.
Deliberately excludes user-preference text and historical feedback (Agent-only
signals). See `environment_pref_v2.py:63-92` for the exact schema.

### Reward weights (`REWARD_WEIGHTS_V2`)

| Component | Weight | Purpose |
|---|---|---|
| `energy_base` | 0.3 | Per-step kWh penalty |
| `price_mult` | 0.2 | TOU price uplift on energy penalty |
| `vpp_mult` | 2.0 | Extra multiplier during VPP window |
| `comfort_mult` | 8.0 | Occupied comfort violation |
| `terminal_washer/dishwasher/dryer` | 200 each | Successful schedule within allowed window |
| `terminal_wh` | 100 | Ready at bath time |
| `terminal_ev` | 300 | Target SOC reached |
| `terminal_vpp_avoid` | 100 | Completed but not run during VPP |
| `terminal_vpp_energy` | 80 | Low VPP-window energy |

### Training persona

Located at `energybridge/roleplay/personas/all_appliances_full.json`. All
appliances `present=true`, `dr_adjustable=true`. Windows tightened to cover
strictest household scenarios so PPO's learned policy generalizes to all 15
benchmark scenarios (10 single-user personas + 5 multi-user households).

### Inference / benchmark

The trained checkpoints are consumed by
`experiments/benchmark/baselines/rl_ppo_pref_v2.py` (the adapter). To run
benchmarks:

```bash
export ENERGYBRIDGE_RL_PREF_V2_MODEL=/path/to/models/rl_ppo_pref_v2_tianjin.zip

python experiments/benchmark/run_baseline_matrix.py \
    --personas atom_comfort_sensitive [...other personas...] \
    --methods rl_ppo_pref_v2 --city Tianjin --days 3 \
    --price-csv experiments/real_data/tianjin_tou_price_normalized.csv \
    --date my_rl_run --workers 4
```

For multi-user households:

```bash
python experiments/benchmark/run_household_matrix.py \
    --methods rl_ppo_pref_v2 --city Tianjin --days 3 \
    --price-csv experiments/real_data/tianjin_tou_price_normalized.csv \
    --date my_rl_run_hh
```

### Verification

30/30 PASS across (Tianjin, Germany) × (10 personas, 5 households), evaluated
on the real EnergyPlus benchmark using checkpoints **trained with
`--backend dynamic`** (Tianjin → tianjin params, Germany → Berlin regional
params):
- `physical_appliance_task_completion_rate = 1.0` all scenarios
- `ev_target_reached_rate = 1.0`
- `output_uncovered_appliance_services = []`

---

## Legacy v1 (3-dim)

The original 3-dim PPO baseline (`train.py`, `environment.py`) is retained
for reference. It is **not** used by the current benchmark. See the sections
below for its documentation.

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
python -m baselines.rl_energyplus.train \
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
python -m baselines.rl_energyplus.train \
  --hours 4 \
  --device cuda \
  --persona atom_comfort_sensitive \
  --output benchmark_results/atom_comfort_sensitive_rl_energyplus_4h
```

The trainer writes periodic checkpoints and always saves the final reusable
model as `ppo_energyplus_3day.zip`.

Continue training from either the final model or a checkpoint:

```bash
python -m baselines.rl_energyplus.train \
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
