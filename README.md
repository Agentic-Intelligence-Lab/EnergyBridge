# EnergyBridge

EnergyBridge is a home-grid coordination agent framework that connects user
preferences, grid/VPP signals, home state, control decisions, safety checks,
execution, and memory logging.

LLM backend: OpenAI-compatible API (configured via `.env`).

---

## Setup

### 1. Prerequisites

| Requirement | Version |
|-------------|---------|
| Python | 3.10+ |
| EnergyPlus | 24.1.0 (default path: `/home/ha_agent/EnergyPlus-24-1-0`; change `EPLUS_ROOT` in `experiments/benchmark/family_runner.py` if needed) |
| conda env | `energybridge` |

```bash
conda activate energybridge
pip install -r requirements.txt
```

### 2. API key configuration

```bash
cp .env.example .env   # then fill in your keys
```

`.env` format (OpenAI-compatible):

```ini
LLM_MODEL=gpt-4o-mini
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-xxxx
# Optional: multiple keys rotated automatically on failure
LLM_API_KEY_POOL=sk-key1,sk-key2,sk-key3
```

---

## Benchmark 1 — Family Home Persona Evaluation

Co-simulates a 3-day residential building (Tianjin, July) with 3 VPP
demand-response events (daily 18:00-19:00). An LLM agent controls the
thermostat on behalf of a persona-defined user.

### Persona files

`energybridge/roleplay/personas/*.json` — 10 archetypes across 6 behavioral
dimensions (comfort, cost, control, flexibility, trust, ecology).

See `energybridge/roleplay/personas/README.md` for schema and field definitions.

### Run a single persona

```bash
cd experiments/benchmark

# By persona ID
python3 run_persona_json.py atom_comfort_sensitive

# All options
python3 run_persona_json.py atom_comfort_sensitive --city Tianjin --output /tmp/out

# Available persona IDs:
#   atom_comfort_sensitive, atom_control_auto, atom_price_indifferent, atom_task_rigid
#   basic_role_a_commuter_price_cooperative, basic_role_b_home_comfort_gated
#   basic_role_c_irregular_cautious, basic_role_d_commuter_ideal_dr
#   basic_role_e_caregiver_low_dr, basic_role_f_commuter_ev_optimizer
```

Results go to `experiments/benchmark/results/<persona_id>/` by default.

### Run all 10 personas (batch)

```bash
cd experiments/benchmark
python3 run_all_personas.py

# Options
python3 run_all_personas.py --results-dir /path/to/output --city Tianjin
python3 run_all_personas.py --no-skip       # re-run even if log exists
```

Output layout:

```
logs/results_<YYYYMMDD>/
├── summary_<YYYYMMDD>.json                       <- per-persona metrics
├── atom_comfort_sensitive/
│   ├── atom_comfort_sensitive_log_<YYYYMMDD>.log  <- full run log
│   └── eplus/                                     <- EnergyPlus raw files
└── ...
```

The batch script resumes automatically: skips personas whose log already
contains `[family/agent]`.

### Result metrics

| Field | Meaning |
|-------|---------|
| `pmv_ok_fraction` | Fraction of occupied hours in PMV comfort range [-0.5, +0.5] |
| `vpp_compliance_rate` | Fraction of 3 VPP events where agent set >= 26 C |
| `user_pref_score` | LLM-evaluated user satisfaction (1-5), averaged over events |
| `energy_kwh_total` | Total electricity consumption over 3 days |
| `llm_call_failures` | LLM API errors (0 = clean run) |

---

## Benchmark 2 — Office Building PMV Baseline

Runs a 15-zone medium office building with PMV or agent-based control.

```bash
cd experiments/benchmark

# PMV rule-based control (default)
python3 office_runner.py --mode pmv --city tianjin

# LLM agent control
python3 office_runner.py --mode agent --city tianjin

# Available cities: tianjin, beijing, shanghai
```

---

## Benchmark 3 — Full Comparative Suite (3 cities x 2 buildings x methods)

Runs 12+ EnergyPlus simulations: family + office x PMV/agent x 3 cities.
Results are saved to `experiments/benchmark/results/`.

```bash
cd experiments/benchmark

# Full run
bash reproduce_benchmark.sh

# Resume interrupted run (skips completed scenarios)
bash reproduce_benchmark.sh --resume

# Single scenario
python3 run_benchmark.py --scenario family/tianjin/pmv

# Family building only
python3 run_benchmark.py --building family --skip-existing
```

---

## Interactive Agent Demo

```bash
conda activate energybridge
python examples/run_agent_loop.py
python examples/run_roleplay_evaluation.py
```

---

## Project Layout

```
EnergyBridge/
├── energybridge/          <- core Python package
│   ├── agent/             <- LangGraph agent (graph, nodes, state)
│   ├── llm/               <- LLM client with key-pool rotation
│   ├── roleplay/          <- persona schema, loader, 10 JSON personas
│   └── simulation/        <- appliance simulation helpers
├── experiments/
│   └── benchmark/         <- family_runner.py, office_runner.py, run_benchmark.py
│       ├── run_persona_json.py    <- single-persona CLI
│       ├── run_all_personas.py    <- batch all-persona CLI
│       ├── reproduce_benchmark.sh <- full 3-city benchmark
│       ├── models/        <- IDF building models
│       └── weather/       <- EPW weather files
├── .env.example           <- API key template
└── requirements.txt
```

---

## Reference

This project references the HEMA open-source framework (MIT License) as a
conceptual reference for multi-agent home energy management. See `references/HEMA/`.
EnergyBridge is an independent implementation; all code in `energybridge/` is
original unless otherwise noted.
