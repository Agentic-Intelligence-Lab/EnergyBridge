# EnergyBridge

EnergyBridge is a consent-aware residential grid-flexibility benchmark. It
connects capacity reporting, household-specific planning, simulated household
authorization, controller-or-fallback execution, EnergyPlus measurement, and
post-event audit.

This repository is organized as a result-free research artifact: it provides
deidentified questionnaire data, benchmark code, model/configuration inputs,
and from-scratch launch scripts. Historical benchmark outputs, LLM transcripts,
precomputed capacity results, tables, and figures are intentionally excluded
from the anonymous release profile.

## Start here

- Full experiment and data map:
  [`reproducibility/README.md`](reproducibility/README.md)
- Deidentified questionnaire data:
  [`human_roleplay_data/README.md`](human_roleplay_data/README.md)
- Benchmark implementation: `experiments/benchmark/`
- Core package: `energybridge/`

## Install

The validated runtime is Python 3.10+ with EnergyPlus 24.1.0.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp reproducibility/paper.env.example .env
```

Edit `.env` with a local EnergyPlus path and, for API-backed methods, local
credentials. `.env` and key files are ignored and must never be committed.

HEMA is an external baseline. Its pinned revision and installation steps are
listed in the reproducibility guide.

## Public data

The questionnaire release contains only:

```text
human_roleplay_data/data/participants.csv
human_roleplay_data/data/responses.csv
```

Both files use newly randomized public participant IDs. Participant-level
geography, gender, exact age, timestamps, response duration, free text, source
IDs, and the source-to-public mapping are not distributed.

Validate and analyze the release:

```bash
bash reproducibility/run_human_analysis.sh
```

Generated statistics are written to `generated_results/`, not to the tracked
data directory.

## From-scratch benchmarks

Main two-region household benchmark:

```bash
bash reproducibility/run_main_benchmark_from_scratch.sh --run
```

June-to-July capacity reporting comparison:

```bash
bash reproducibility/run_capacity_reporting_from_scratch.sh --run
```

Both wrappers require `--run` to prevent accidental large simulator/API jobs.
They support resuming completed jobs and write only beneath the ignored
`generated_results/` directory.

The capacity analysis reports three quantities:

1. accepted-only capacity accuracy;
2. simulated household acceptance rate;
3. overall accurate coverage, equal to the first two quantities multiplied
   together up to displayed rounding.

No prior value for any of these quantities is stored in the anonymous release.

## Code map

```text
energybridge/
├── data/               # tariff/weather readers
├── llm/                # provider-neutral LLM client
├── memory/             # household/event memory
├── quantification/     # event baselines and capacity reporting
├── roleplay/           # personas, households, calendars, consent simulator
├── simulation/         # EnergyPlus and appliance interfaces
└── skills/             # controller/tool interfaces

experiments/benchmark/
├── baselines/          # HEMA, MPC, Rule+MILP, PPO adapters
├── family_runner.py    # household physical/control loop
├── run_household_matrix.py
├── run_daily_dr_memory_matrix.py
└── run_capacity_consent_joined_replay.py

reproducibility/
├── README.md
├── paper.env.example
├── run_human_analysis.sh
├── run_main_benchmark_from_scratch.sh
└── run_capacity_reporting_from_scratch.sh
```

PPO training code is under `baselines/rl_energyplus/`, with inference
checkpoints under `models/`. Region-specific building, tariff, weather, persona,
calendar, and VPP-event inputs are mapped in the reproducibility guide.

## Tests

Run the release-data validator and core tests from the repository root:

```bash
python human_roleplay_data/scripts/validate_release.py
PYTHONPATH=. pytest -q tests experiments/benchmark/tests
```

EnergyPlus integration tests require a valid `EPLUS_ROOT`; API integration
tests require credentials supplied only through the local environment.

## Result and privacy policy

Do not add generated outputs to source control. In particular, keep these
paths untracked:

```text
benchmark_results/
paper_results/
generated_results/
reproduced_results/
experiments/benchmark/results/
importance_sampling/IS_result/
dr_capacity_memory_toolkit/*/data/
```

The private questionnaire ZIP must never enter Git history. The public
source-data-only ZIP under `human_roleplay_data/release/` is built from the two
deidentified CSV files and contains no precomputed analysis result.

For double-blind submission, do not push this working repository or its Git
history. Use the history-free export and audit workflow documented under
`scripts/`; connect only the newly initialized anonymous snapshot to the
submission remote.

Create the snapshot outside this repository:

```bash
python scripts/export_anonymous_release.py \
  --output-dir ../EnergyBridge-anonymous \
  --archive ../EnergyBridge-anonymous.zip \
  --init-git
```

The exporter reads only tracked files, applies the explicit result denylist,
runs the privacy/path/credential audit, creates a deterministic ZIP, and
optionally initializes one commit owned by `Anonymous Authors`. It does not
copy the current remote, branches, tags, reflog, or author history.

For an additional local identity check, repeat `--forbidden-token` for any
username, author surname, institution, or organization string that must not
appear:

```bash
python scripts/audit_anonymous_release.py \
  ../EnergyBridge-anonymous \
  --forbidden-token LOCAL_USERNAME \
  --forbidden-token ORGANIZATION_NAME
```

Inspect the new repository before connecting it to a remote:

```bash
git -C ../EnergyBridge-anonymous log --format=fuller -1
git -C ../EnergyBridge-anonymous remote -v
git -C ../EnergyBridge-anonymous status --short
```

Only after those checks should the new repository be connected to the
anonymous submission remote. The current `origin` must not be reused.
