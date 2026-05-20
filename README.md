# EnergyBridge

EnergyBridge is a home-grid coordination agent framework that connects user preferences, grid/VPP signals, home state, control decisions, safety checks, execution, and memory logging.

Current status: stage-1 interactive agent loop is implemented with LangGraph. It runs locally without an API key by default, and can optionally use an OpenAI-compatible API from a local .env file to generate strategy options.

## Quick Start

1. Activate environment:

```bash
cd ~/work/EnergyBridge
source ~/miniconda3/etc/profile.d/conda.sh
conda activate energybridge
```

2. Run interactive demo:

```bash
python examples/run_agent_loop.py
```

3. Optional LLM wrapper test (requires `.env` and `USE_LLM=true`):

```bash
python examples/run_llm_test.py
```

4. Role-play preference-learning evaluation:

```bash
python examples/run_roleplay_evaluation.py
```

5. Batch role-play evaluation with formal reports:

```bash
python examples/run_batch_roleplay_evaluation.py --users 10 --turns 5
```

For a larger demo run:

```bash
python examples/run_batch_roleplay_evaluation.py --users 20 --turns 5
```

Batch outputs include:

- `batch_result.json`: full machine-readable batch result
- `batch_summary.json`: aggregate metrics + user-level rows
- `batch_summary.csv`: one row per simulated user
- `batch_turns.csv`: one row per user turn

The demo will:

- load an upstream dispatch task from VPP-1;
- translate the VPP-1 task/query into EnergyBridge internal grid signal;
- ask for user preference text;
- optionally call the configured API to generate strategy options;
- let the user choose a strategy;
- run safety checks and mock actuation;
- collect user satisfaction feedback;
- report metrics including API latency, token usage, expected energy, and whether the local response meets the VPP-derived target;
- update memory and save a trajectory log.

Role-play evaluation will:

- use a second LLM configuration to simulate one user persona;
- run 5 turns with isolated memory for that simulated user;
- save persona, per-turn interaction logs, trajectories, final memory, and a learning summary under `logs/evaluations/<evaluation_user_id>/`.

## Current Simulation Structure

The simulation layer is organized around four objects:

- `SimulatedUser`: role-play user persona and decisions
- `AgentSimulator`: EnergyBridge agent graph, skills, strategy generation, and control projection
- `GridSimulator`: VPP-1-backed grid task/query generation
- `HomeSimulator`: local home state and turn-to-turn home updates

Main files:

- `energybridge/simulation/user.py`
- `energybridge/simulation/agent.py`
- `energybridge/simulation/grid.py`
- `energybridge/simulation/home.py`
- `energybridge/simulation/simulation.py`
- `energybridge/skills/registry.py`
- `energybridge/memory/store.py`
- `energybridge/evaluation/metrics.py`

## Minimal Loop

`user_input + grid_signal + home_state`
`-> load memory`
`-> parse preference`
`-> translate grid signal`
`-> generate strategy`
`-> user selects strategy`
`-> run mock control`
`-> safety validation`
`-> mock actuation`
`-> explanation`
`-> memory update`
`-> trajectory logging`

## EnergyPlus Benchmark / EP-Agent Loop

For running the EnergyPlus co-simulation agent loop, analyzing outputs, and generating benchmark metrics, see:

- [`docs/eplus_benchmark_loop_readme.md`](docs/eplus_benchmark_loop_readme.md) — commands, parameters, metrics reference
- [`docs/eplus_baseline_comparison_report.md`](docs/eplus_baseline_comparison_report.md) — controlled vs no-control baseline comparison
- [`docs/benchmark_metric_line_progress_report.md`](docs/benchmark_metric_line_progress_report.md) — project progress report

Quick EP run:

```bash
python examples/run_eplus_agent_loop.py \
  --idf Family_Model/Family_Simple_3day.idf \
  --epw /path/to/weather.epw \
  --output logs/eplus_new_run \
  --trigger 42.0

python examples/analyze_eplus_run.py \
  --output logs/eplus_new_run \
  --trigger 42.0 \
  --report
```

## Reference Note

`references/HEMA` is kept only as architecture inspiration because it is GPLv3. EnergyBridge code is implemented independently and does not copy HEMA source code.

---

## EnergyPlus Co-Simulation Benchmark (`experiments/`)

The `experiments/` directory contains a full EnergyPlus co-simulation benchmark
suite evaluating LLM-based demand-response control against a PMV (Predicted Mean
Vote) baseline across **3 cities × 2 building types × 2 methods = 12 scenarios**.

### Directory layout

```
experiments/
├── benchmark/              # Simulation runners & orchestrator
│   ├── family_runner.py    # Single-zone family home (EnergyPlus Python API)
│   ├── office_runner.py    # 15-zone medium office (EnergyPlus Python API)
│   ├── run_benchmark.py    # 12-scenario orchestrator, result table
│   ├── user_pref_scorer.py # Roleplay LLM user preference + satisfaction scoring
│   └── results/            # JSON results + per-scenario EP output
├── models/
│   ├── family_home/        # family_simple_3day.idf  (July 1–3, single zone)
│   └── medium_office/      # medium_office_3day.idf  (June 1–3, 15 zones)
└── weather/epw/            # Beijing / Shanghai / Tianjin EPW files (CSWD)
```

### Benchmark design

| Dimension | Value |
|-----------|-------|
| Simulation duration | 3 days (72 h) |
| VPP demand-response events | 3 × per simulation (1 h each, same clock hour daily) |
| Family event trigger | 18:00 each day |
| Office event trigger | 17:00 each day |
| Methods compared | **PMV baseline** (physics-optimal setpoint) vs **LLM Agent** (claude-sonnet-4-6) |
| Cities | Beijing, Shanghai, Tianjin (CSWD weather) |
| Buildings | Single-zone family home, 15-zone medium office |

### LLM Agent control loop (per VPP event)

```
1. RoleplayUser.generate_user_input()   ← user preference BEFORE agent acts
2. HVAC LLM → setpoint decision + reason  (reads user preference + past memory)
3. EnergyPlus runs VPP window (1 h)
4. RoleplayUser.generate_feedback()     ← satisfaction score (1–5) AFTER event
5. Score + comment stored in agent memory → agent learns across 3 events
```

### Key metrics

| Metric | Description |
|--------|-------------|
| `能耗(kWh)` | Total 3-day electricity consumption |
| `PMV达标率` | Fraction of occupied hours with |PMV| ≤ 0.5 |
| `均温(°C)` | Mean zone dry-bulb temperature during occupied hours |
| `未满足(h)` | Hours where zone temp > setpoint + 0.556°C (unmet cooling) |
| `VPP响应率` | Fraction of VPP events where setpoint raised ≥ 0.5°C above baseline (Agent only; PMV = 0%) |
| `Roleplay评分(s1/s2/s3)` | Satisfaction score per VPP event from LLM roleplay persona (1–5) |

### How to run

```bash
cd experiments/benchmark
conda activate energybridge

# Full 12-scenario benchmark
python run_benchmark.py

# Single scenario
python run_benchmark.py --scenario family/tianjin/agent

# Only one building type
python run_benchmark.py --building office
python run_benchmark.py --building family


# 生成全部 18 个场景的 CSV（默认输出到 metrics_table.csv）
python3 generate_metrics_csv.py

# 只输出住宅
python3 generate_metrics_csv.py --building family

# 指定路径
python3 generate_metrics_csv.py --results-dir ./results --output my_metrics.csv

# 只写 CSV，不打印
python3 generate_metrics_csv.py --no-print

```



Requires:
- EnergyPlus 24.1.0 at `/home/ha_agent/EnergyPlus-24-1-0/` (Linux default)
- `energybridge` conda env with `pyenergyplus`, `pythermalcomfort`, `openai`
- `.env` at `EnergyBridge/.env` with `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`


Agent achieves **14–18% energy savings** on office scenarios while maintaining
>91% PMV compliance. Learning curve (score s1→s3) shows improvement in 3/6
agent scenarios. PMV baseline maintains higher comfort due to no VPP response.

### LLM reliability

The `LLMClient` now includes **exponential-backoff retry** (3 retries,
5/10/20 s delays) to handle API rate-limiting. Root cause of previous failures:
the dmxapi.cn endpoint returns an empty response body (rather than an HTTP error
code) when throttled; the client now detects empty responses and retries.

