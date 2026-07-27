# Reproducing EnergyBridge

This guide maps the paper experiments to public data, benchmark code, input
assets, and from-scratch commands. The anonymous release profile intentionally
contains no historical EnergyPlus run directory, historical LLM response,
precomputed benchmark table, plot, or capacity event result.

New outputs are written under `generated_results/`, which is ignored by Git.

## Release boundary

Included:

- deidentified questionnaire microdata;
- benchmark and analysis source code;
- household, persona, calendar, tariff, weather, VPP-event, and building-model
  inputs;
- PPO checkpoint inputs;
- scripts that regenerate outputs from scratch.

Excluded:

- `benchmark_results/`, `paper_results/`, and prior run directories;
- precomputed capacity memories, summaries, tables, and figures;
- historical LLM prompts, responses, and token logs;
- the private questionnaire export and source-to-public ID mapping;
- local environment files and API keys.

Consequently, this release can recompute public-data analyses immediately and
can rerun simulator/API-dependent experiments from scratch. It does not claim
that a single command reconstructs every historical paper artifact without
rerunning its underlying experiment.

## Repository map

| Purpose | Location |
|---|---|
| Main benchmark runners | `experiments/benchmark/` |
| EnergyBridge implementation | `energybridge/` |
| MPC, Rule+MILP, PPO, and HEMA adapters | `experiments/benchmark/baselines/` |
| PPO training code | `baselines/rl_energyplus/` |
| PPO checkpoints | `models/rl_ppo_pref_v2_*.zip` |
| Household definitions | `energybridge/roleplay/households/` |
| Persona cards and calendars | `energybridge/roleplay/personas/` |
| Tianjin/Germany building models | `Family_Model/`, `experiments/models/` |
| Tariff and weather inputs | `experiments/real_data/` |
| Capacity event inputs | `dr_capacity_memory_toolkit/*/config/` |
| Questionnaire microdata | `human_roleplay_data/data/` |
| Questionnaire instruments and role cards | `human_survey_materials/` |
| This release's launch scripts | `reproducibility/` |

## Environment

The currently validated runtime uses:

- Python 3.10 or newer;
- EnergyPlus 24.1.0;
- six EnergyPlus zone timesteps per hour;
- the Python dependencies listed in `requirements.txt`.

Create an environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Install EnergyPlus 24.1.0 and set its root. The default example is neutral and
must be changed if EnergyPlus is installed elsewhere:

```bash
export EPLUS_ROOT=/opt/EnergyPlus-24-1-0
```

HEMA is an external baseline. Keep it outside this repository and pin the
revision used by the adapter:

```bash
mkdir -p ../reference
git clone https://github.com/humanbuildingsynergy/HEMA.git ../reference/HEMA
git -C ../reference/HEMA checkout 40d365f3c51fd9872caf96905db503e0fde0334c
python -m pip install -r ../reference/HEMA/requirements.txt
export ENERGYBRIDGE_HEMA_ROOT="$PWD/../reference/HEMA"
```

Create the local runtime configuration:

```bash
cp reproducibility/paper.env.example .env
```

Edit `.env` and supply the API credentials for the configured
OpenAI-compatible endpoint. `.env` and key files are ignored and must not be
committed.

The benchmark configuration uses `--mpc-horizon 6`. In the implementation this
means six 10-minute control steps, or one hour; it does not mean six hours.

## Quick validation

Run the release validator and API-free smoke tests before launching a
simulator job:

```bash
bash reproducibility/run_release_checks.sh
```

This checks the deidentified data package and the capacity, consent,
counterfactual-baseline, event-memory, and PPO interfaces without starting
EnergyPlus or contacting an external model service.

## 1. Human questionnaire data

The public release has two data files:

- `human_roleplay_data/data/participants.csv`;
- `human_roleplay_data/data/responses.csv`.

Validate and generate the human-only statistical outputs:

```bash
bash reproducibility/run_human_analysis.sh
```

Equivalent commands are documented in
`human_roleplay_data/README.md`. The analysis regenerates acceptance and
satisfaction summaries, age-band counts, Cochran Q tests, and paired exact
McNemar comparisons.

Human-LLM alignment requires counts from a new LLM evaluator run. No historical
LLM result is included. Pass a newly generated
`method,llm_accepted,llm_trials` CSV using the `--llm-counts` argument described
in the data README.

Completion-time/QC sensitivity and fine demographic tables cannot be rebuilt
from the public microdata because those participant-level fields were removed
for privacy.

## 2. Main regional benchmark

The main runner is:

```text
experiments/benchmark/run_household_matrix.py
```

It crosses five fixed households with EnergyBridge, HEMA, MPC Dynamic,
Rule+MILP, and PPO in one region. Each seven-day run contains one daily
18:00-19:00 demand-response event. Run both regions from scratch:

```bash
bash reproducibility/run_main_benchmark_from_scratch.sh --run
```

This launches 50 seven-day simulations: five households, five methods, and two
regions. The wrapper fixes the same start date, event window, method list,
consent policy, and one-hour MPC horizon across methods. Interrupted matrices
can be restarted because the wrapper enables `--resume`.

Generated matrix summaries:

```text
generated_results/main_benchmark/
├── tianjin_7day/_batch_logs/
│   ├── household_matrix_summary_tianjin_7days_H6.json
│   └── household_matrix_summary_tianjin_7days_H6.csv
└── germany_7day/_batch_logs/
    ├── household_matrix_summary_germany_7days_H6.json
    └── household_matrix_summary_germany_7days_H6.csv
```

Generated reports are written to
`generated_results/main_benchmark_reports/`. The report generator is
`experiments/benchmark/generate_baseline_matrix_report.py`.

## 3. Capacity reporting baseline comparison

The capacity workflow is separate from the main regional matrix. It creates a
June retrieval pool and evaluates a held-out July query cohort. Every method
uses top-5 same-method retrieval. Consent selects the proposed controller
trajectory or the ordinary fallback before physical execution.

Run the full workflow:

```bash
bash reproducibility/run_capacity_reporting_from_scratch.sh --run
```

The wrapper launches 840 independent one-day simulations:

- two months;
- two regions;
- five households;
- the first seven configured event days;
- five evaluated methods plus the `no_dr` counterfactual reference.

The first monthly matrix uses:

```text
dr_capacity_memory_toolkit/june_2025_daily_eb/config/
  vpp_events_june_memory_merged30.json
```

The second uses:

```text
dr_capacity_memory_toolkit/july_2025_daily_eb/config/
  vpp_events_july_memory_merged30.json
```

After both matrices finish,
`experiments/benchmark/run_capacity_consent_joined_replay.py` joins capacity
reports and consent decisions over the common July cohort. Its compact table
contains exactly:

- accepted-only accuracy,
  \(P(0.8 \leq C^{actual}/\widehat C \leq 1.2 \mid accepted)\);
- acceptance rate, \(P(accepted)\);
- overall accurate coverage,
  \(P(accepted \cap accurate)\).

Overall accurate coverage equals accepted-only accuracy multiplied by
acceptance rate, up to displayed rounding.

All generated capacity files remain under:

```text
generated_results/capacity_reporting/
generated_results/capacity_reporting_analysis/
```

They are outputs and must not be added to the anonymous repository.

## 4. Controlled persona and calendar analyses

The relevant code paths are:

| Experiment step | Script |
|---|---|
| Create controlled persona inputs | `experiments/benchmark/create_controlled_adapt_personas.py` |
| Run a persona-method matrix | `experiments/benchmark/run_baseline_matrix.py` |
| Analyze no-gate personalization | `experiments/benchmark/analyze_persona_adaptability_no_gate.py` |
| Analyze the general adaptability matrix | `experiments/benchmark/analyze_persona_adaptability.py` |
| Generate matrix report | `experiments/benchmark/generate_baseline_matrix_report.py` |

These scripts are included, but historical controlled-run outputs are not.
Use each script's `--help` output to select a new result directory under
`generated_results/`.

## 5. Additional benchmark analyses

| Purpose | Script |
|---|---|
| Method-neutral traditional-controller consent replay | `experiments/benchmark/replay_traditional_acceptance_method_neutral.py` |
| Capacity/shed evaluator | `experiments/benchmark/run_capacity_shed_evaluation.py` |
| Importance-weight analysis | `importance_sampling/experiment_is_weight_improvements.py` |
| Supplementary paired/resource analysis | `experiments/benchmark/analyze_supplementary_evidence.py` |
| PPO training | `python -m baselines.rl_energyplus.train_pref_v2` |

Any of these scripts must write outputs below `generated_results/` or another
ignored external directory. Pre-generated IS weights and summaries are not
part of the anonymous release profile.

## Paper coverage

| Paper component | Public reproduction status |
|---|---|
| Human questionnaire statistics | Recomputed from deidentified microdata |
| Human-LLM alignment | Requires a new LLM evaluator run |
| Capacity reporting comparison | Full from-scratch workflow provided |
| Main regional benchmark | Full runner/config workflow provided |
| Controlled persona/calendar experiments | Code and input-generation workflow provided |
| QC and fine demographic analyses | Not reconstructable from public microdata |
| Token/resource tables | Regenerated only by a new API-backed run |

## Reproducibility limits

EnergyPlus and deterministic controller paths can be repeated when software
versions and inputs are held fixed. API-backed EnergyBridge, HEMA, and
role-play generation can change when the external model service changes, even
with fixed prompts and sampling parameters. A new run is therefore a
from-scratch reproduction, not a promise of byte-identical historical logs.

Before interpreting a rerun, record:

- Git commit of this repository and the HEMA checkout;
- EnergyPlus version and `python -m pip freeze` output;
- model/provider identifiers and generation settings;
- region, start date, event input, method list, and random seeds;
- whether the run resumed from prior local outputs.
