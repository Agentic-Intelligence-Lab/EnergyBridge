# EnergyBridge

EnergyBridge is a consent-aware benchmark for residential demand response. It
evaluates the complete household-to-grid loop rather than only the controller:

```text
VPP request → capacity report → household-specific plan → simulated consent
            → controller or fallback execution → EnergyPlus measurement → audit
```

The evaluated methods are EnergyBridge, HEMA, MPC Dynamic, Rule+MILP, and PPO.
The capacity experiment additionally uses `no_dr` as a counterfactual
reference.

The reviewer-facing export provides benchmark code, model and configuration
inputs, PPO checkpoints, deidentified questionnaire microdata, and
from-scratch launch scripts. Its export profile excludes precomputed benchmark
results, historical LLM transcripts, tables, and figures. Every new output is
generated locally under the Git-ignored `generated_results/` directory.

## Quick start

Run all commands from the repository root. The validated environment uses
Python 3.10 or newer and EnergyPlus 24.1.0.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp reproducibility/paper.env.example .env
```

For the simulator-backed experiments, set the EnergyPlus installation path in
`.env`:

```text
EPLUS_ROOT=/opt/EnergyPlus-24-1-0
```

EnergyBridge and HEMA require an OpenAI-compatible API configuration. HEMA is
kept as a pinned external checkout rather than copied into this repository.
The exact revision and setup commands are in
[`reproducibility/README.md`](reproducibility/README.md#environment).

Do not commit `.env`, API keys, or generated outputs.

## Verify the release

The release checks validate the questionnaire package and exercise the
capacity, consent, counterfactual-baseline, event-memory, and PPO interfaces.
They do not launch EnergyPlus or call an external API.

```bash
bash reproducibility/run_release_checks.sh
```

## Reproduce the experiments

| Experiment | Command | Workload and requirements | Output |
|---|---|---|---|
| Human questionnaire analysis | `bash reproducibility/run_human_analysis.sh` | Deidentified CSV files; no EnergyPlus or API | `generated_results/human_roleplay/` |
| Main regional benchmark | `bash reproducibility/run_main_benchmark_from_scratch.sh --run` | 50 seven-day simulations; EnergyPlus, HEMA, and API configuration | `generated_results/main_benchmark/` and `generated_results/main_benchmark_reports/` |
| Capacity reporting comparison | `bash reproducibility/run_capacity_reporting_from_scratch.sh --run` | 840 one-day simulations; EnergyPlus, HEMA, and API configuration | `generated_results/capacity_reporting/` and `generated_results/capacity_reporting_analysis/` |

The two simulator wrappers require the explicit `--run` flag to prevent an
accidental large job. Both enable resume mode, so completed runs are reused
after an interruption. Control parallelism and output location with:

```bash
export ENERGYBRIDGE_WORKERS=5
export ENERGYBRIDGE_GENERATED_RESULTS_ROOT="$PWD/generated_results"
```

The main benchmark crosses two regions, five fixed households, and five
methods. The capacity workflow separately builds a June retrieval pool and
evaluates the first seven configured July event days as a held-out cohort,
using top-5 same-method retrieval. Its final table reports:

1. capacity accuracy conditional on acceptance;
2. simulated household acceptance rate;
3. overall accurate coverage, computed as the product of the first two
   quantities.

All fixed dates, event definitions, methods, consent settings, and analysis
commands are documented in the
[`detailed reproduction guide`](reproducibility/README.md).

## Code architecture

| Path | Responsibility |
|---|---|
| `energybridge/agent/` | Agent state and workflow orchestration |
| `energybridge/quantification/` | Event baselines, retrieval memory, and capacity reporting |
| `energybridge/roleplay/` | Household, persona, calendar, and simulated-consent models |
| `energybridge/control/` | Controllers, fallback behavior, and safety checks |
| `energybridge/simulation/` | EnergyPlus and appliance execution interfaces |
| `energybridge/evaluation/` | Metrics, trajectory logging, and post-event audit |
| `energybridge/llm/` | Provider-neutral LLM clients and prompts |
| `energybridge/memory/` | Household and event memory |

The execution-facing benchmark code is separate from the reusable package:

| Path | Contents |
|---|---|
| `experiments/benchmark/` | Experiment runners, analysis scripts, and report generators |
| `experiments/benchmark/baselines/` | HEMA, MPC Dynamic, Rule+MILP, and PPO adapters |
| `reproducibility/` | Reviewed entry points and environment template |
| `Family_Model/`, `experiments/models/` | Residential and office EnergyPlus models |
| `experiments/weather/`, `experiments/real_data/` | Weather and tariff inputs |
| `energybridge/roleplay/households/`, `energybridge/roleplay/personas/` | Fixed household, persona, and calendar inputs |
| `dr_capacity_memory_toolkit/*/config/` | June and July VPP event definitions |
| `models/` | PPO inference checkpoints |
| `human_roleplay_data/` | Deidentified microdata, codebook, validator, and analysis code |
| `tests/`, `experiments/benchmark/tests/` | Release and benchmark tests |

The primary paper-facing entry points are:

```text
experiments/benchmark/run_household_matrix.py
experiments/benchmark/run_daily_dr_memory_matrix.py
experiments/benchmark/run_capacity_consent_joined_replay.py
human_roleplay_data/scripts/reproduce_analysis.py
```

## Reproducibility notes

- Questionnaire statistics are recomputed directly from the released
  microdata.
- EnergyPlus and deterministic-controller paths can be repeated when software
  versions and inputs are held fixed.
- API-backed methods may vary when the external model service changes. Record
  the model identifier, provider, generation settings, repository commit, and
  external HEMA commit for each run.
- Human–LLM alignment requires a newly generated evaluator file; historical
  LLM counts are not bundled.
- Fine-demographic and response-time sensitivity analyses cannot be rebuilt
  from the public microdata because those fields were removed for privacy.

## Documentation

- [`reproducibility/README.md`](reproducibility/README.md): full environment,
  experiment parameters, outputs, and reproducibility limits.
- [`human_roleplay_data/README.md`](human_roleplay_data/README.md): study
  design and public-data analysis.
- [`human_roleplay_data/CODEBOOK.md`](human_roleplay_data/CODEBOOK.md): field
  definitions.
