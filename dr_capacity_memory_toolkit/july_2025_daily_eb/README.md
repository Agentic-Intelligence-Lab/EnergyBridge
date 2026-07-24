# July 2025 EnergyBridge DR Capacity Dataset

This directory contains the July 2025 EnergyBridge simulation dataset, generated
as a held-out complement to the June 2025 `june_2025_daily_eb/` memory pool.
See the top-level `dr_capacity_memory_toolkit/README.md` for how this fits into
the overall toolkit and the 5-method comparison.

The held-out data was generated as independent daily samples:

```text
30 held-out days x 5 households x 2 cities x 2 methods(no_dr, EnergyBridge)
= 600 one-day runs
```

The 300 EnergyBridge events can be evaluated against the June memory library
(`dr_capacity_memory_toolkit/june_2025_daily_eb/data/energybridge_daily_dr_memory_rag_v2_weather.json`,
285 calibrated events), or against themselves as a leave-one-out self-check --
see "Running an evaluation" below.

## Contents

```text
config/
└── vpp_events_july_memory_merged30.json

data/
├── daily_dr_memory_summary_raw.json
├── daily_dr_memory_summary_raw.csv
├── daily_dr_memory_summary_with_counterfactual.json
├── daily_dr_memory_summary_with_counterfactual.csv
├── daily_dr_memory_no_dr_counterfactual_library.json
└── energybridge_daily_dr_memory.json
```

- `config/vpp_events_july_memory_merged30.json`: 30-day held-out DR event
  schedule. All events pinned to 18:00 (identical structure to the merged30
  variant of the June schedule) so that inter-month comparison is not
  confounded by different VPP hours.
- `data/daily_dr_memory_summary_raw.*`: 600 one-day simulation summaries.
- `data/daily_dr_memory_summary_with_counterfactual.*`: summaries after matching
  each EnergyBridge run against the corresponding no-DR counterfactual.
- `data/daily_dr_memory_no_dr_counterfactual_library.json`: no-DR baseline
  library derived from the 300 no_dr runs. Consumed by
  `run_daily_dr_memory_matrix.py`'s postprocess to overwrite
  `actual_shed_kwh` in every EnergyBridge result; kept here so the
  counterfactual settlement is reproducible without re-simulating no_dr.
- `data/energybridge_daily_dr_memory.json`: 300 EnergyBridge events. Enriched
  with `weather_features` via `weather_shift.features.attach_weather_to_memory`
  so it matches the field layout of the June
  `energybridge_daily_dr_memory_rag_v2_weather.json`.

This toolkit does **not** include per-evaluation capacity reports. Those should
be regenerated for each evaluation run (see below) to keep the target run, VPP
events, and counterfactual settlement clearly bound.

## Regenerate The Simulation Data (~2 hours, optional)

The committed `data/` files are already the products of this step. Skip it if
you want to reuse the committed data directly.

```bash
python experiments/benchmark/run_daily_dr_memory_matrix.py \
  --methods no_dr EnergyBridge \
  --cities Germany Tianjin \
  --days 30 --start-date 2025-07-01 \
  --vpp-events-json dr_capacity_memory_toolkit/july_2025_daily_eb/config/vpp_events_july_memory_merged30.json \
  --results-root $OUTPUT_DIR \
  --date $(date +%Y-%m-%d) \
  --workers 5
```

The runner's built-in postprocess (skipped only with `--no-postprocess`)
collects the 300 no_dr runs into
`$OUTPUT_DIR/<DATE>/_batch_logs/daily_dr_memory_no_dr_counterfactual_library.json`,
rewrites each EnergyBridge `benchmark_result.json` with the correct
`counterfactual_baseline_kwh` / `actual_shed_kwh`, and emits the two summary
JSON/CSV pairs plus `energybridge_daily_dr_memory.json`. Then attach weather
features (only needed if you regenerated the simulation):

```python
from energybridge.quantification.weather_shift.features import attach_weather_to_memory
attach_weather_to_memory(
    "$OUTPUT_DIR/<DATE>/_batch_logs/energybridge_daily_dr_memory.json",
    "$OUTPUT_DIR/<DATE>/_batch_logs/energybridge_daily_dr_memory.json",
)
```

These are exactly the files copied into this toolkit's `data/` directory to
lock in the committed dataset.

## Running An Evaluation

See the top-level `dr_capacity_memory_toolkit/README.md` for
`run_capacity_shed_evaluation.py`'s full usage and option list. This dataset
works as either the query set against the June pool
(`--pool-month june --query-month july`) or a July-only leave-one-out
self-check (`--pool-month july --query-month july`).
