# EnergyBridge

EnergyBridge is a home-grid coordination benchmark for comparing an LLM
home-energy Agent against MPC baselines under persona preferences, calendars,
VPP demand-response events, EnergyPlus co-simulation, and role-play scoring.

The current main benchmark is the **family-home VPP evaluation**. The default
comparison is Tianjin for 3 days; a Germany real-data variant runs from
2025-06-01 for 7 days with real weather. Day-ahead prices are an optional
advanced input for any city. All paths use persona users, 7-day calendars,
capacity quantification, EnergyPlus execution, and post-event role-play scoring.

---

## Quick Start

```bash
cd /home/hku_user/work/EnergyBridge
conda activate energybridge
pip install -r requirements.txt
```

Configure the OpenAI-compatible LLM backend:

```bash
cp .env.example .env
```

`.env` example:

```ini
LLM_MODEL=gpt-4o-mini
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-xxxx
# Optional: rotate multiple keys automatically on failures
LLM_API_KEY_POOL=sk-key1,sk-key2,sk-key3
```

Required runtime:

| Requirement | Current default |
|-------------|-----------------|
| Python | 3.10+ |
| Conda env | `energybridge` |
| EnergyPlus | 24.1.0 |
| Default EnergyPlus path | `/home/hku_user/EnergyPlus-24-1-0` |

If EnergyPlus is installed elsewhere, set:

```bash
export EPLUS_ROOT=/path/to/EnergyPlus-24-1-0
```

---

## Web Dashboard

Use this first when you want to run or inspect benchmarks interactively.

```bash
cd /home/hku_user/work/EnergyBridge
conda activate energybridge
python experiments/benchmark/web_dashboard.py --host 0.0.0.0 --port 8787
```

Open locally on the server:

```text
http://127.0.0.1:8787
```

If the dashboard is running on a remote server, forward the port from your
local machine:

```bash
ssh -o ExitOnForwardFailure=yes -fN -L 8798:127.0.0.1:8787 hku_user@100.116.9.76
open http://127.0.0.1:8798
```

Dashboard workflow:

1. Select user category: `Role-play LLM` or `Human`.
2. Select user type/name.
3. Select method: `agent`, `mpc_dynamic`, or `mpc_ep`.
4. Start the run and watch live logs, progressive event cards, appliance
   schedules, user scores, and the final `run_summary.txt`.
5. Open historical results from the collapsible sidebar.

The dashboard uses Python's standard library HTTP server. No extra package is
needed beyond `requirements.txt`.

---

## Main Benchmark Commands

### Run One Persona And One Method

Run from the repository root:

```bash
cd /home/hku_user/work/EnergyBridge
conda activate energybridge
```

Agent:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Tianjin --method agent
```

MPC with collaborator dynamic model:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Tianjin --method mpc_dynamic --mpc-horizon 6
```

MPC with EnergyPlus replay predictor:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Tianjin --method mpc_ep --mpc-horizon 6
```

Human-in-the-loop user instead of role-play LLM:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Tianjin --method agent --user-mode human --human-name alice
```

### Germany Real-Data Variant

Germany uses real weather and a 7-day date range:

```text
weather: experiments/real_data/germany_2025_weather.csv
EPW    : experiments/weather/epw/DEU_Germany_2025_real.epw
start  : 2025-06-01
days   : 7
```

The daily planning decision is at **00:00** for all cities. Day-ahead price is
not a separate Agent or city mode. It is enabled only when `--price-csv` is
provided. If omitted, the benchmark falls back to the normal policy and price
metrics are reported as `NaN`.

Run Germany Agent:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Germany --method agent
```

Enable day-ahead price optimization for Germany:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Germany --method agent \
  --price-csv experiments/real_data/germany_2025_price.csv
```

The same price-aware path works for Tianjin or any other city if a compatible
price CSV is supplied:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Tianjin --method agent \
  --price-csv /path/to/tianjin_day_ahead_price.csv
```

Regenerate the EPW from the real-weather CSV:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Germany --method agent --regenerate-epw
```

Run Germany MPC baselines:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Germany --method mpc_dynamic --mpc-horizon 6

python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Germany --method mpc_ep --mpc-horizon 6
```

Override the default date range if needed:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Germany --method agent --days 7 --start-date 2025-06-01
```

If no price CSV is provided, the run still works and the price metrics are
reported as `NaN`.

VPP windows are parameterized. The default is one event per day from 18:00 to
19:00. Change the start time or duration with:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Tianjin --method agent \
  --vpp-start-hour 17 --vpp-duration-hours 2
```

Current VPP windows must stay within a single simulation day
(`start + duration <= 24`). Cross-midnight VPP events need a separate absolute
time-window pass.

### Run The 10-Persona Matrix

This is the main comparable experiment:

```bash
python experiments/benchmark/run_baseline_matrix.py \
  --city Tianjin --mpc-horizon 6
```

Germany 7-day matrix:

```bash
python experiments/benchmark/run_baseline_matrix.py \
  --city Germany --days 7 --start-date 2025-06-01 --mpc-horizon 6
```

Germany 7-day matrix with day-ahead price enabled:

```bash
python experiments/benchmark/run_baseline_matrix.py \
  --city Germany --days 7 --start-date 2025-06-01 --mpc-horizon 6 \
  --price-csv experiments/real_data/germany_2025_price.csv
```

Default matrix:

```text
10 approved personas x 3 methods = 30 jobs
methods: agent, mpc_dynamic, mpc_ep
duration: 3 days
calendar: enabled
capacity quantification: enabled
role-play scoring: enabled
```

Useful controls:

```bash
# Preview commands without running
python experiments/benchmark/run_baseline_matrix.py --dry-run

# Resume after interruption
python experiments/benchmark/run_baseline_matrix.py --resume

# Run only selected methods
python experiments/benchmark/run_baseline_matrix.py \
  --methods agent mpc_dynamic --city Tianjin --mpc-horizon 6

# Run only selected users
python experiments/benchmark/run_baseline_matrix.py \
  --personas basic_role_a_commuter_price_cooperative atom_control_auto \
  --methods agent --city Tianjin

# Smoke test one job
python experiments/benchmark/run_baseline_matrix.py --max-runs 1

# Sweep a longer VPP window
python experiments/benchmark/run_baseline_matrix.py \
  --city Tianjin --vpp-start-hour 17 --vpp-duration-hours 2 --max-runs 1
```

### Generate The Matrix Report

After the matrix finishes:

```bash
python experiments/benchmark/generate_baseline_matrix_report.py \
  --date 2026-06-14 --city Tianjin --horizon 6
```

If `--date` is omitted, the script uses today.

Report outputs:

```text
benchmark_results/<YYYY-MM-DD>/_batch_logs/baseline_matrix_report/
├── baseline_matrix_report.png
├── baseline_matrix_report.md
└── baseline_matrix_report_table.csv
```

The current report figure shows four persona-by-method matrices:

1. User score.
2. Total energy.
3. VPP-window energy.
4. Appliance shift success rate.

### Diagnose MPC-EP Predictor Error

Use this when checking whether the EnergyPlus replay predictor matches the
realized main EnergyPlus trajectory:

```bash
python experiments/benchmark/diagnose_mpc_ep_predictor.py \
  benchmark_results/2026-06-14/*_mpc_ep_H6_tianjin_3days \
  --output-dir benchmark_results/2026-06-14/_batch_logs/mpc_ep_diagnostics
```

This produces CSV/JSON comparisons between predicted H-step facility power and
the realized `eplusout.mtr` meter trace.

---

## Results And Naming

All current benchmark outputs go under:

```text
benchmark_results/<YYYY-MM-DD>/
```

Single-user role-play runs:

```text
benchmark_results/<YYYY-MM-DD>/<role>_<method>[_Hn]_<city>_<days>days/
├── run_summary.txt          # read this first
├── benchmark_result.json    # machine-readable metrics
└── eplusout.*               # EnergyPlus outputs
```

Examples:

```text
benchmark_results/2026-06-14/role_a_agent_tianjin_3days/
benchmark_results/2026-06-14/role_a_mpc_dynamic_H6_tianjin_3days/
benchmark_results/2026-06-14/role_a_mpc_ep_H6_tianjin_3days/
benchmark_results/2026-06-14/role_a_agent_germany_7days/
```

Human runs use the custom name:

```text
benchmark_results/2026-06-14/alice_human_agent_tianjin_3days/
benchmark_results/2026-06-14/alice_human_mpc_dynamic_H6_tianjin_3days/
```

If the exact same default run directory already exists, only that run directory
is replaced. Other dates, users, methods, cities, and horizons are not touched.
Passing `--output /custom/path` bypasses the default naming scheme.

Important result files:

| File | Purpose |
|------|---------|
| `run_summary.txt` | Human-readable result, event strategies, VPP target, appliance schedules, scores |
| `benchmark_result.json` | Raw metrics used by matrix/report scripts |
| `eplusout.mtr` | EnergyPlus meter trace used for VPP energy and MPC-EP diagnostics |
| `_batch_logs/baseline_matrix_summary_*.json` | Batch-level machine-readable summary |
| `_batch_logs/baseline_matrix_report/*.png` | Compact visual report |

Key metrics:

| Metric | Meaning |
|--------|---------|
| `user_pref_score` | Role-play or human user satisfaction, averaged over VPP events |
| `energy_kwh_total` | Total 3-day electricity consumption |
| `vpp_window_energy_kwh` | Energy consumed during VPP windows |
| `appliance_shift_success_rate` | Present shiftable tasks completed and shifted away from VPP |
| `appliance_task_completion_rate` | Present shiftable tasks completed |
| `ev_target_reached_rate` | EV service target success rate |
| `ewh_preheat_used_rate` | Water-heater preheat usage/readiness metric |
| `day_ahead_price_metrics` | Price-weighted EnergyPlus consumption; `NaN` when no price data is available |

---

## Personas And Calendars

Approved persona JSON files live in:

```text
energybridge/roleplay/personas/*.json
```

Paired 7-day synthetic calendars live in:

```text
energybridge/roleplay/personas/calendars/<persona_id>/calendar_7day.json
```

Day 1 is Sunday. The default 3-day benchmark evaluates Sunday, Monday, and
Tuesday. Calendars are loaded automatically and injected into role-play
strategy selection and scoring, so simulated users consider:

- appointments and away/home periods
- return-home comfort
- hot-water deadlines
- EV departure deadlines
- chore timing constraints

Persona schema details:

```text
energybridge/roleplay/personas/README.md
```

Approved persona IDs:

```text
atom_comfort_sensitive
atom_control_auto
atom_price_indifferent
atom_task_rigid
basic_role_a_commuter_price_cooperative
basic_role_b_home_comfort_gated
basic_role_c_irregular_cautious
basic_role_d_commuter_ideal_dr
basic_role_e_caregiver_low_dr
basic_role_f_commuter_ev_optimizer
```

---

## Methods

### `agent`

The EnergyBridge Agent receives:

- persona preferences
- paired calendar
- VPP event window
- capacity-quantified VPP target
- day-ahead price context when available
- live EnergyPlus state
- appliance state

It must explicitly control present controllable appliances and AC setpoints.
Role-play LLM users choose strategy candidates before VPP events and score
outcomes afterward.

### `mpc_dynamic`

Finite-horizon cumulative-cost MPC using the local dynamic model in:

```text
experiments/benchmark/baselines/mpc/dynamic_model/
```

This is the collaborator-derived control-oriented dynamic predictor adapted
into the benchmark package.

### `mpc_ep`

Finite-horizon cumulative-cost MPC using EnergyPlus replay rollouts in:

```text
experiments/benchmark/baselines/mpc/ep_predictor.py
```

Important caveat: this is an **EnergyPlus replay-based horizon predictor**, not
a perfect full-state EnergyPlus oracle. It starts fresh EnergyPlus candidate
runs and replays to the decision time. Diagnostics record IDF/EPW, warmup
policy, state alignment, and prediction error.

### RL Baseline

The PPO/RL baseline is separate from the main 10-persona matrix path. See:

```text
baselines/rl_energyplus_3day/README.md
baselines/rl_typical_human/
```

---

## Current Code Structure

```text
EnergyBridge/
├── energybridge/
│   ├── agent/                         # LangGraph agent pieces
│   ├── data/                          # real-weather, EPW, and day-ahead price helpers
│   ├── llm/                           # OpenAI-compatible client + key rotation
│   ├── quantification/                # VPP capacity quantification helpers
│   ├── roleplay/
│   │   ├── personas/                  # persona JSON files and schema README
│   │   └── calendar.py                # calendar attachment/loading
│   └── simulation/                    # EnergyPlus state/actuator adapters
├── experiments/benchmark/
│   ├── family_runner.py               # main 3-day family EnergyPlus runner
│   ├── run_persona_json.py            # single-persona CLI
│   ├── run_baseline_matrix.py         # 10-persona x methods batch runner
│   ├── generate_baseline_matrix_report.py
│   ├── diagnose_mpc_ep_predictor.py
│   ├── web_dashboard.py               # browser UI
│   ├── user_pref_scorer.py            # role-play/human event scoring
│   ├── baselines/mpc/                 # MPC planner, dynamic model, EP predictor
│   ├── models/family_home/            # family IDF models
│   └── weather/epw/                   # weather files
├── experiments/real_data/             # Germany 2025 weather and price CSVs
├── baselines/
│   ├── rl_energyplus_3day/            # PPO baseline against EnergyPlus
│   └── rl_typical_human/              # lightweight RL environment
├── benchmark_results/                 # generated outputs, ignored by default
├── requirements.txt
└── .env.example
```

Most coding-agent work starts in one of these files:

| Task | Start here |
|------|------------|
| Change Agent behavior/prompt | `experiments/benchmark/family_runner.py` |
| Change role-play scoring | `experiments/benchmark/user_pref_scorer.py` |
| Change matrix run list | `experiments/benchmark/run_baseline_matrix.py` |
| Change report plots/tables | `experiments/benchmark/generate_baseline_matrix_report.py` |
| Change MPC planner | `experiments/benchmark/baselines/mpc/planner.py` |
| Change dynamic predictor | `experiments/benchmark/baselines/mpc/dynamic_model/` |
| Change EP predictor | `experiments/benchmark/baselines/mpc/ep_predictor.py` |
| Change web UI | `experiments/benchmark/web_dashboard.py` |

---

## Legacy And Reference Commands

These commands are kept for reproducibility and archaeology. They are not the
current primary comparison path.

### Multi-Persona Household Discussion

```bash
python experiments/benchmark/run_multi_persona_json.py \
  basic_role_a_commuter_price_cooperative \
  basic_role_b_home_comfort_gated \
  --city Tianjin --verbose
```

This produces:

```text
benchmark_results/multi__<id_a>__<id_b>/
├── run_summary.txt
├── benchmark_result.json
└── household_meta.json
```

### Office Building PMV/Agent Baseline

```bash
python experiments/benchmark/office_runner.py --mode pmv --city tianjin
python experiments/benchmark/office_runner.py --mode agent --city tianjin
```

### Older Full Comparative Suite

This is an early reproduction path. Check the script before running because it
may contain stale machine-specific paths from older iterations.

```bash
bash experiments/benchmark/reproduce_benchmark.sh
bash experiments/benchmark/reproduce_benchmark.sh --resume
python experiments/benchmark/run_benchmark.py --scenario family/tianjin/pmv
python experiments/benchmark/run_benchmark.py --building family --skip-existing
```

### Legacy Interactive Demo

The current human-in-the-loop path is:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --user-mode human --human-name alice --method agent
```

The older lightweight demo is kept here:

```bash
python examples/run_agent_loop.py
```

### Automated Role-Play Evaluation Without EnergyPlus

```bash
python examples/run_roleplay_evaluation.py --turns 5
```

### Long-Term Memory/Learning Test

```bash
python experiments/benchmark/run_longterm.py --persona commuter --city Tianjin --days 7
```

---

## Reference Notes

Reference-derived DR capacity quantification and independent RL integration
notes are in:

```text
REFERENCE_CAPACITY_RL_INTEGRATION.md
baselines/rl_energyplus_3day/README.md
```

EnergyBridge is an independent implementation; code in `energybridge/` is
original unless otherwise noted.
