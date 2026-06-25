# EnergyBridge

EnergyBridge is a home-grid coordination benchmark for comparing an LLM
home-energy Agent against MPC baselines under persona preferences, calendars,
VPP demand-response events, EnergyPlus co-simulation, and role-play scoring.

The current main benchmark is the **family-home VPP evaluation**. For fast
iteration, use the Germany 3-day quick run: it starts on Sunday 2025-06-01,
uses real Germany weather, can include day-ahead prices, and still keeps
calendar context, capacity quantification, EnergyPlus execution, and role-play
scoring. The longer Tianjin/Germany 7-day runs remain the comparable full
baseline path.

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
3. Select method: `EnergyBridge`, `mpc_dynamic`, or `mpc_ep`.
4. Start the run and watch live logs, progressive event cards, appliance
   schedules, user scores, and the final `run_summary.txt`.
5. Open historical results from the collapsible sidebar.

The dashboard uses Python's standard library HTTP server. No extra package is
needed beyond `requirements.txt`.

---

## Reports And Baseline Interface

This is the section to share with collaborators who want to add another
baseline and compare it in the same reports.

### Recommended Report Workflow

Run one full matrix first. The matrix delegates every job to
`experiments/benchmark/run_persona_json.py`, so single-run behavior, calendar
loading, capacity quantification, VPP schedule handling, role-play scoring,
and output naming stay consistent.

Fast Germany 3-day matrix with real weather, the Berlin family IDF, and
day-ahead price:

```bash
python experiments/benchmark/run_baseline_matrix.py \
  --city Germany --days 3 --start-date 2025-06-01 --mpc-horizon 6 \
  --price-csv experiments/real_data/germany_2025_price.csv
```

Full Tianjin 7-day personal-user matrix. Tianjin automatically loads
`experiments/real_data/tianjin_tou_price_normalized.csv`, so the report uses
total electricity cost instead of total energy when the price metrics are
available.

```bash
PYTHONUNBUFFERED=1 \
python experiments/benchmark/run_baseline_matrix.py \
  --methods EnergyBridge mpc_dynamic mpc_ep rule_milp eb_rule_milp \
  --city Tianjin \
  --days 7 \
  --mpc-horizon 6 \
  --date <YYYY-MM-DD> \
  --workers 5 \
  --resume
```

Full Germany 7-day personal-user matrix. Germany automatically uses
`experiments/models/family_home/berlin_family_geg_final.idf`; pass the
Germany price CSV to make the report use total electricity cost.

```bash
PYTHONUNBUFFERED=1 \
python experiments/benchmark/run_baseline_matrix.py \
  --methods EnergyBridge mpc_dynamic mpc_ep rule_milp eb_rule_milp \
  --city Germany \
  --days 7 \
  --start-date 2025-06-01 \
  --mpc-horizon 6 \
  --price-csv experiments/real_data/germany_2025_price.csv \
  --date <YYYY-MM-DD> \
  --workers 5 \
  --resume
```

Matrix summaries are written to:

```text
benchmark_results/<YYYY-MM-DD>/_batch_logs/
├── baseline_matrix_summary_<city>_<days>days_H<horizon>.json
└── baseline_matrix_summary_<city>_<days>days_H<horizon>.csv
```

Generate the personal-user report figure/table/markdown from a summary:

```bash
python experiments/benchmark/generate_baseline_matrix_report.py \
  --summary-json benchmark_results/<YYYY-MM-DD>/_batch_logs/baseline_matrix_summary_tianjin_7days_H6.json \
  --artifact-prefix personal_tianjin_7day_5method \
  --output-dir benchmark_results/<YYYY-MM-DD>/_batch_logs/personal_tianjin_7day_5method_report
```

For the Germany personal-user report:

```bash
python experiments/benchmark/generate_baseline_matrix_report.py \
  --summary-json benchmark_results/<YYYY-MM-DD>/_batch_logs/baseline_matrix_summary_germany_7days_H6.json \
  --artifact-prefix personal_germany_7day_5method \
  --output-dir benchmark_results/<YYYY-MM-DD>/_batch_logs/personal_germany_7day_5method_report
```

Report outputs:

```text
benchmark_results/reports/<report_name>/
├── <prefix>_baseline_matrix_report.png
├── <prefix>_baseline_matrix_report.md
└── <prefix>_baseline_matrix_report_table.csv
```

The current report reads each job's `benchmark_result.json` and visualizes:

- role-play/human user score
- total electricity cost when a price profile is available; otherwise EnergyPlus electricity consumption
- VPP-window electricity consumption
- appliance shift success rate

### Multi-User Household Matrix And EB+rule+MILP

The fixed multi-user household benchmark treats each household JSON under
`energybridge/roleplay/households/` as one large user. Each member keeps an
independent role-play context for strategy comments and scoring, while the
physical household owns one shared full appliance set: AC, washer, dryer,
dishwasher, water heater, EV, and refrigerator. Household scores are the mean
of the independent member scores.

`EB+rule+MILP` is the current hybrid Agent method for this path. Its design is:

- Rule+MILP proposes physically feasible appliance schedules and PMV/cost-min
  AC guidance.
- EnergyBridge reads those candidates, member preferences, calendar context,
  and prior in-run feedback.
- Appliance timing is inherited from Rule+MILP by default, so every present
  appliance has a real emitted policy and VPP-window non-AC loads stay avoided.
- The Agent may choose among equal-objective MILP options and explain the
  preference tradeoff.
- AC starts from the PMV/cost-min setpoint. If a member gives explicit warm or
  comfort-boundary feedback, the next decision may move back to the warm edge
  of the household comfort band. Set
  `ENERGYBRIDGE_HYBRID_COMFORT_OVERRIDE_AFTER=1` for one-feedback override,
  which is the default comparison setting.
- Preference memory is run-local by default. Set
  `ENERGYBRIDGE_PERSIST_AGENT_MEMORY=1` only when you want review files written
  into the run output directory.

Run one household with the hybrid method:

```bash
ENERGYBRIDGE_HYBRID_COMFORT_OVERRIDE_AFTER=1 \
ENERGYBRIDGE_PERSIST_AGENT_MEMORY=0 \
python experiments/benchmark/run_multi_user_household.py \
  --household household_s2_multigeneration_caregiver \
  --method eb_rule_milp \
  --city Tianjin \
  --days 7 \
  --start-date 2025-06-01 \
  --vpp-start-hour 18.0 \
  --vpp-duration-hours 1.0
```

Run all five households for Tianjin with all five comparable methods and five
parallel workers. Tianjin automatically uses the normalized TOU price profile.

```bash
ENERGYBRIDGE_HYBRID_COMFORT_OVERRIDE_AFTER=1 \
ENERGYBRIDGE_PERSIST_AGENT_MEMORY=0 \
PYTHONUNBUFFERED=1 \
python experiments/benchmark/run_household_matrix.py \
  --methods EnergyBridge mpc_dynamic mpc_ep rule_milp eb_rule_milp \
  --city Tianjin \
  --days 7 \
  --start-date 2025-06-01 \
  --date <YYYY-MM-DD> \
  --workers 5 \
  --resume
```

Run all five households for Germany with all five comparable methods. Germany
uses the Berlin family IDF by default; pass Germany day-ahead prices explicitly.

```bash
ENERGYBRIDGE_HYBRID_COMFORT_OVERRIDE_AFTER=1 \
ENERGYBRIDGE_PERSIST_AGENT_MEMORY=0 \
PYTHONUNBUFFERED=1 \
python experiments/benchmark/run_household_matrix.py \
  --methods EnergyBridge mpc_dynamic mpc_ep rule_milp eb_rule_milp \
  --city Germany \
  --days 7 \
  --start-date 2025-06-01 \
  --price-csv experiments/real_data/germany_2025_price.csv \
  --date <YYYY-MM-DD> \
  --workers 5 \
  --resume
```

Five workers are usually CPU-safe on the project server; the bottleneck is more
likely the LLM API. Watch process load and API retry signals while a batch is
running:

```bash
ps -eo pid,ppid,stat,pcpu,pmem,etime,cmd --sort=-pcpu | \
  rg 'run_household_matrix|run_multi_user_household|EnergyPlus|train_pref_v2|python -u'

rg -n 'LLM attempt|RateLimit|429|JSONDecodeError|Traceback|\\[FAILED\\]|\\[COMPLETED\\]' \
  benchmark_results/<YYYY-MM-DD>/_batch_logs/household_matrix_*_7days_H6/*.log
```

The household matrix summaries are written to:

```text
benchmark_results/<YYYY-MM-DD>/_batch_logs/
├── household_matrix_summary_tianjin_7days_H6.json
├── household_matrix_summary_tianjin_7days_H6.csv
├── household_matrix_summary_germany_7days_H6.json
└── household_matrix_summary_germany_7days_H6.csv
```

To generate a household report from explicit summaries:

```bash
python experiments/benchmark/generate_baseline_matrix_report.py \
  --summary-json \
    benchmark_results/<YYYY-MM-DD>/_batch_logs/household_matrix_summary_tianjin_7days_H6.json \
    benchmark_results/<YYYY-MM-DD>/_batch_logs/household_matrix_summary_germany_7days_H6.json \
  --output-dir benchmark_results/<YYYY-MM-DD>/_batch_logs/household_5x2_7day_5method_report \
  --artifact-prefix household_5x2_7day_5method \
  --row-label Household \
  --completion-metric physical
```

The three main 5-method report tables for a full refresh are:

```text
benchmark_results/<YYYY-MM-DD>/_batch_logs/personal_tianjin_7day_5method_report/
benchmark_results/<YYYY-MM-DD>/_batch_logs/personal_germany_7day_5method_report/
benchmark_results/<YYYY-MM-DD>/_batch_logs/household_5x2_7day_5method_report/
```

When the four-method household baseline has already been run and EB+rule+MILP
is rerun separately, merge the JSON summaries first, then pass the merged
`*_main_5method.json` files to `generate_baseline_matrix_report.py`. The report
script supports multiple summary JSONs in one call and automatically appends
city labels to the household rows.

### How To Add A New Baseline Method

Use a stable lowercase method id, for example `my_baseline`. Keep the method id
short because it is used in output directory names, matrix summaries, and
report columns.

Recommended integration path:

1. Implement the controller in or under `experiments/benchmark/baselines/`.
2. Add dispatch in `experiments/benchmark/family_runner.py`.
3. Expose the method in `experiments/benchmark/run_persona_json.py`.
4. Add the method to `experiments/benchmark/run_baseline_matrix.py`.
5. Add the display order/label in `experiments/benchmark/generate_baseline_matrix_report.py`.

Files to update:

| File | What to change |
|------|----------------|
| `experiments/benchmark/family_runner.py` | Accept the new `method` and call the baseline at each control decision |
| `experiments/benchmark/run_persona_json.py` | Add the method to `--method` choices and `_method_label()` |
| `experiments/benchmark/run_baseline_matrix.py` | Add the method to `DEFAULT_METHODS`; pass any method-specific CLI flags |
| `experiments/benchmark/generate_baseline_matrix_report.py` | Add `METHOD_ORDER` and `METHOD_LABEL` entries |
| `experiments/benchmark/web_dashboard.py` | Optional: add a button if the method should run from the browser |

The baseline should return the same control intent shape used by the existing
runner. At minimum it should provide an AC setpoint and a reason; if it controls
appliances, use the same keys as the Agent/MPC paths:

```python
{
    "setpoint": 25.5,
    "reason": "short explanation shown in logs and run_summary",
    "next_check_hour": 19.0,
    "washer_start_h": 14.0,
    "washer_skip": False,
    "dishwasher_start_h": 21.0,
    "dishwasher_skip": False,
    "water_heater_preheat": True,
    "water_heater_preheat_start_h": 14.0,
    "water_heater_preheat_end_h": 18.0,
    "water_heater_preheat_temp_c": 68.0,
    "ev_mode": "smart"
}
```

The report layer expects each run directory to contain:

```text
benchmark_result.json
run_summary.txt
eplusout.mtr
```

Important fields in `benchmark_result.json`:

| Field | Required for reports | Meaning |
|-------|----------------------|---------|
| `method` | yes | Method id, e.g. `EnergyBridge`, `mpc_dynamic`, `my_baseline`. The old `agent` id is accepted only as a deprecated alias. |
| `weather` | yes | City/scenario label |
| `exit_code` | yes | `0` means successful run |
| `user_pref_score` | yes | Average user score |
| `energy_kwh_total` | yes | Total EnergyPlus electricity |
| `vpp_window_energy_kwh` | yes | Total VPP-window electricity |
| `appliance_shift_success_rate` | yes | Shifted completed loads away from VPP |
| `vpp_event_log` | recommended | Per-event details for dashboard and debugging |
| `daily_energy_kwh` | recommended | Per-day energy shown in dashboard |

Quick smoke test for a new method:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Germany --days 3 --start-date 2025-06-01 \
  --price-csv experiments/real_data/germany_2025_price.csv \
  --method my_baseline
```

Then run a tiny matrix before the full comparison:

```bash
python experiments/benchmark/run_baseline_matrix.py \
  --city Germany --days 3 --start-date 2025-06-01 \
  --price-csv experiments/real_data/germany_2025_price.csv \
  --methods EnergyBridge my_baseline --personas basic_role_a_commuter_price_cooperative \
  --max-runs 2
```

If that succeeds, run the full matrix and generate the report with the commands
above.

---

## Main Benchmark Commands

### Recommended Fast Iteration: Germany 3-Day

Use this when tuning Agent prompts or runner logic. It is shorter than the
7-day matrix but still exercises the modern stack: persona calendar,
capacity quantification, VPP events, EnergyPlus, optional day-ahead price, and
role-play scoring.

```bash
python experiments/benchmark/run_germany_3day_quick.py basic_role_a_commuter_price_cooperative
```

Defaults:

```text
city      : Germany
dates     : 2025-06-01 to 2025-06-03
weekday   : Sunday, Monday, Tuesday
days      : 3
VPP       : daily 18:00-19:00
price CSV : experiments/real_data/germany_2025_price.csv
IDF       : generated from experiments/models/family_home/berlin_family_geg_final.idf
output    : benchmark_results/<YYYY-MM-DD>/<role>_<method>_germany_3days/
```

The generated run-specific IDF is stored under:

```text
benchmark_results/<YYYY-MM-DD>/_run_assets/<run_name>/family_simple_3day_2025-06-01_3days.idf
```

Useful variants:

```bash
# Same quick path, but a different user
python experiments/benchmark/run_germany_3day_quick.py basic_role_f_commuter_ev_optimizer

# Quick Germany MPC checks
python experiments/benchmark/run_germany_3day_quick.py basic_role_a_commuter_price_cooperative --method mpc_dynamic
python experiments/benchmark/run_germany_3day_quick.py basic_role_a_commuter_price_cooperative --method mpc_ep

# Disable price input while keeping Germany weather/date
python experiments/benchmark/run_germany_3day_quick.py basic_role_a_commuter_price_cooperative --no-price

# Inspect the expanded command without running EnergyPlus
python experiments/benchmark/run_germany_3day_quick.py basic_role_a_commuter_price_cooperative --dry-run
```

For a quick 10-persona matrix on the same Germany 3-day setup:

```bash
python experiments/benchmark/run_baseline_matrix.py \
  --city Germany --days 3 --start-date 2025-06-01 --mpc-horizon 6 \
  --price-csv experiments/real_data/germany_2025_price.csv
```

### Run One Persona And One Method

Run from the repository root:

```bash
cd /home/hku_user/work/EnergyBridge
conda activate energybridge
```

EnergyBridge:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Tianjin --method EnergyBridge
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
  --city Tianjin --method EnergyBridge --user-mode human --human-name alice
```

Tianjin 7-day Agent run using the existing 7-day IDF:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Tianjin --method EnergyBridge --days 7
```

### Germany Real-Data Full Variant

The full Germany comparison uses real weather and a 7-day date range:

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

Run Germany EnergyBridge:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Germany --method EnergyBridge
```

Enable day-ahead price optimization for Germany:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Germany --method EnergyBridge \
  --price-csv experiments/real_data/germany_2025_price.csv
```

The same price-aware path works for Tianjin or any other city if a compatible
price CSV is supplied:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Tianjin --method EnergyBridge \
  --price-csv /path/to/tianjin_day_ahead_price.csv
```

Regenerate the EPW from the real-weather CSV:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Germany --method EnergyBridge --regenerate-epw
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
  --city Germany --method EnergyBridge --days 7 --start-date 2025-06-01
```

If no price CSV is provided, the run still works and the price metrics are
reported as `NaN`.

VPP windows are parameterized. The default is one event per day from 18:00 to
19:00. Change the start time or duration with:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Tianjin --method EnergyBridge \
  --vpp-start-hour 17 --vpp-duration-hours 2
```

Current VPP windows must stay within a single simulation day
(`start + duration <= 24`). Cross-midnight VPP events need a separate absolute
time-window pass.

For varied windows or multiple events per day, pass a JSON schedule:

```bash
python experiments/benchmark/run_persona_json.py basic_role_a_commuter_price_cooperative \
  --city Tianjin --method EnergyBridge --days 7 \
  --vpp-events-json experiments/benchmark/configs/vpp_events_7day_variable.json
```

Supported JSON shape:

```json
{
  "events": [
    {"day": 1, "start_h": 18.0, "duration_h": 1.0},
    {"day": 3, "start_h": 12.0, "duration_minutes": 30},
    {"day": 3, "start_h": 18.0, "end_h": 19.0}
  ]
}
```

Each event wakes the controller at VPP start. The runner forces another wake-up
at the event end so the Agent can restore comfort and the role-play user can
score the result. The Agent can still request additional future wake-ups with
`next_check_hour`.

### Run The 10-Persona Matrix

This is the current five-method personal-user comparison. Tianjin uses the
normalized TOU price profile automatically.

```bash
PYTHONUNBUFFERED=1 \
python experiments/benchmark/run_baseline_matrix.py \
  --methods EnergyBridge mpc_dynamic mpc_ep rule_milp eb_rule_milp \
  --city Tianjin --days 7 --mpc-horizon 6 \
  --workers 5 --resume
```

Germany uses the Berlin family IDF automatically. Pass day-ahead prices so the
right-top metric is total electricity cost:

```bash
PYTHONUNBUFFERED=1 \
python experiments/benchmark/run_baseline_matrix.py \
  --methods EnergyBridge mpc_dynamic mpc_ep rule_milp eb_rule_milp \
  --city Germany --days 7 --start-date 2025-06-01 --mpc-horizon 6 \
  --price-csv experiments/real_data/germany_2025_price.csv \
  --workers 5 --resume
```

Current full personal-user matrix:

```text
10 approved personas x 5 methods = 50 jobs per city
methods: EnergyBridge, mpc_dynamic, mpc_ep, rule_milp, eb_rule_milp
duration: 7 days for the comparable full run
calendar: enabled
capacity quantification: enabled
role-play scoring: enabled
cost metric: total electricity cost when price data is available
```

Useful controls:

```bash
# Preview commands without running
python experiments/benchmark/run_baseline_matrix.py --dry-run

# Resume after interruption
python experiments/benchmark/run_baseline_matrix.py --resume

# Run only selected methods
python experiments/benchmark/run_baseline_matrix.py \
  --methods EnergyBridge mpc_dynamic --city Tianjin --mpc-horizon 6

# Run only selected users
python experiments/benchmark/run_baseline_matrix.py \
  --personas basic_role_a_commuter_price_cooperative atom_control_auto \
  --methods EnergyBridge --city Tianjin

# Smoke test one job
python experiments/benchmark/run_baseline_matrix.py --max-runs 1

# Sweep a longer VPP window
python experiments/benchmark/run_baseline_matrix.py \
  --city Tianjin --vpp-start-hour 17 --vpp-duration-hours 2 --max-runs 1

# Run a custom 7-day VPP schedule
python experiments/benchmark/run_baseline_matrix.py \
  --city Tianjin --days 7 \
  --vpp-events-json experiments/benchmark/configs/vpp_events_7day_variable.json \
  --max-runs 1
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
benchmark_results/2026-06-14/role_a_EnergyBridge_tianjin_3days/
benchmark_results/2026-06-14/role_a_mpc_dynamic_H6_tianjin_3days/
benchmark_results/2026-06-14/role_a_mpc_ep_H6_tianjin_3days/
benchmark_results/2026-06-14/role_a_EnergyBridge_germany_7days/
```

Human runs use the custom name:

```text
benchmark_results/2026-06-14/alice_human_EnergyBridge_tianjin_3days/
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

### `rule_milp`

Oracle-style baseline for transparent lower-bound comparisons. HVAC uses a PMV
rule to select the warmest feasible cooling setpoint. Shiftable appliances,
water-heater preheat, and EV charging are scheduled by a small MILP over
feasible windows, with a large penalty for non-AC appliance operation inside
VPP windows.

### `eb_rule_milp`

Hybrid EnergyBridge method for the multi-user household benchmark. Rule+MILP
provides the appliance schedule and PMV/cost-min AC candidate; EnergyBridge
uses member role-play feedback and run-local preference memory to choose among
equivalent candidates and make bounded comfort adjustments. This method is
intended to keep Rule+MILP-like energy/VPP behavior while recovering user
preference score through explicit household feedback.

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
│   ├── configs/                       # VPP event schedule JSON examples
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
  --user-mode human --human-name alice --method EnergyBridge
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
