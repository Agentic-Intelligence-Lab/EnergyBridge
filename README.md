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
