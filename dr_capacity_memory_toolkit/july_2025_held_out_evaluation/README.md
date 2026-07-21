# July 2025 Held-Out DR Capacity Evaluation Toolkit

This directory contains the held-out evaluation dataset generated from July 2025
DR simulations. It is used to measure the July generalization performance of the
RAG-based capacity reporting system trained on the June 2025 memory library.

Unlike `june_2025_daily_eb_rule_milp/` and `rag_similarity_v2_weather/`, which
serve as **reusable memory pools** for retrieval, this toolkit is an
**evaluation target**: the 300 EnergyBridge events here are consumed by the
reporting pipeline as future queries, not as historical references.

The held-out data was generated as independent daily samples:

```text
30 held-out days x 5 households x 2 cities x 2 methods(no_dr, EnergyBridge)
= 600 one-day runs
```

The 300 EnergyBridge events are then evaluated against the June memory library
(`dr_capacity_memory_toolkit/rag_similarity_v2_weather/data/energybridge_daily_dr_memory_rag_v2_weather.json`,
285 calibrated events).

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
- `data/energybridge_daily_dr_memory.json`: 300 EnergyBridge events serving as
  the held-out query set. Enriched with `weather_features` via
  `weather_shift.features.attach_weather_to_memory` so it matches the field
  layout of the June `energybridge_daily_dr_memory_rag_v2_weather.json`.
  This file is a snapshot of the held-out data; the reporting pipeline
  itself does not consume it directly (Step 3 retrieves from the June
  memory library, not this one), but it is useful as an audit reference
  for the 300 events.

This toolkit does **not** include per-evaluation capacity reports (e.g.
`*_with_agent_capacity.json` or shed-ratio analysis outputs). Those should be
regenerated for each evaluation to keep the target run, VPP events, and
counterfactual settlement clearly bound.

## Reproduce The Full Evaluation

The recipe uses two directories:

- `$OUTPUT_DIR` -- a scratch directory for this evaluation run's
  intermediates (e.g. `benchmark_results/july_2025_held_out/<DATE>/`).
- `$AGENT_REPORT_DIR` -- where the Step 3 agent-report output is written
  (e.g. `$OUTPUT_DIR/agent_capacity/`).

Substitute both wherever they appear below.

### Step 1 -- Regenerate simulation data (~2 hours, optional)

The committed `data/` files are already the products of this step. Skip this
step if you want to reuse the committed data directly.

```bash
python experiments/benchmark/run_daily_dr_memory_matrix.py \
  --methods no_dr EnergyBridge \
  --cities Germany Tianjin \
  --days 30 --start-date 2025-07-01 \
  --vpp-events-json dr_capacity_memory_toolkit/july_2025_held_out_evaluation/config/vpp_events_july_memory_merged30.json \
  --results-root $OUTPUT_DIR \
  --date $(date +%Y-%m-%d) \
  --workers 5
```

The runner's built-in postprocess (skipped only if you pass
`--no-postprocess`) collects the 300 no_dr runs into
`$OUTPUT_DIR/<DATE>/_batch_logs/daily_dr_memory_no_dr_counterfactual_library.json`,
rewrites each EnergyBridge `benchmark_result.json` with the correct
`counterfactual_baseline_kwh` / `actual_shed_kwh`, and emits the two
summary JSON/CSV pairs (`daily_dr_memory_summary_raw` and
`daily_dr_memory_summary_with_counterfactual`) plus
`energybridge_daily_dr_memory.json`. These are exactly the files copied
into this toolkit's `data/` directory to lock in the committed dataset.

### Step 2 -- Enrich agent memory with weather features (~5 seconds, optional)

Only needed if you regenerated the simulation in Step 1. The committed
`data/energybridge_daily_dr_memory.json` already has weather features attached.

```python
from energybridge.quantification.weather_shift.features import attach_weather_to_memory
attach_weather_to_memory(
    "$OUTPUT_DIR/<DATE>/_batch_logs/energybridge_daily_dr_memory.json",
    "$OUTPUT_DIR/<DATE>/_batch_logs/energybridge_daily_dr_memory.json",
)
```

### Step 3 -- Agent capacity reporting (~10 min, calls LLM)

Uses the shared `experiments/benchmark/dr_event_memory_library.py agent-report`
entry point with the June memory library:

```bash
mkdir -p $AGENT_REPORT_DIR
python experiments/benchmark/dr_event_memory_library.py agent-report \
  --memory dr_capacity_memory_toolkit/rag_similarity_v2_weather/data/energybridge_daily_dr_memory_rag_v2_weather.json \
  --summary-json dr_capacity_memory_toolkit/july_2025_held_out_evaluation/data/daily_dr_memory_summary_with_counterfactual.json \
  --output-summary-json $AGENT_REPORT_DIR/summary_with_agent_capacity.json \
  --methods EnergyBridge \
  --top-k 5 \
  --write-result-json
```

For a no-LLM deterministic dry-run add `--dry-run`; the reporter then selects
the balanced/P70 band without calling the LLM.

### Step 4 -- Accept-only shed-ratio pass rate

```bash
python experiments/benchmark/analyze_shed_ratio_accept_only.py \
  --summary-json $AGENT_REPORT_DIR/summary_with_agent_capacity.json \
  --output-json $AGENT_REPORT_DIR/shed_ratio_accept_only.json
```

The analyzer splits every EnergyBridge event by its
`vpp_plan_acceptance_rate` (persona-aware consent gate outcome) and reports
the pass rate at the 0.8-1.2 shed ratio band separately for accepted and
rejected events. Events whose VPP plan was rejected fall back to a
rule-based comfort routine that is intentionally opaque to the RAG
reporter; the accept-only pass rate is the reporting-quality metric.

### Step 5 (optional) -- Importance-sampling vs pure-RAG comparison

```bash
python experiments/benchmark/run_is_vs_rag_comparison.py \
  --memory dr_capacity_memory_toolkit/rag_similarity_v2_weather/data/energybridge_daily_dr_memory_rag_v2_weather.json \
  --is-weights-json importance_sampling/IS_result/weights_package_germany_6to7.json \
  --summary-json $AGENT_REPORT_DIR/summary_with_agent_capacity.json \
  --output-json $AGENT_REPORT_DIR/is_vs_rag_comparison.json
```

Attaches the June->July importance-sampling weights from PR #20 to the top-k
retrieval and produces an apples-to-apples comparison against the
unweighted RAG baseline.

## What The Analyzer Reports

The committed dataset yields three buckets — accepted, rejected, and
mixed — split by the persona-aware acceptance gate outcome. For each
bucket the analyzer emits:

- pass rate at the 0.8-1.2 shed ratio band
- mean and standard deviation of the shed ratio
- mean absolute deviation from a perfect 1.0 ratio
- MAE and RMSE of `reported_capacity - actual_shed` in kWh

Accept-only is the primary signal for RAG reporting quality because
rejected events execute a rule-based comfort routine that the memory
library has no visibility into. Specific numeric outcomes on the
committed data are reported separately.

## Comparison To Sibling Toolkits

| Toolkit | Role | Purpose |
|---|---|---|
| `june_2025_daily_eb_rule_milp/` | Memory library | 6月 rule_milp historical DR memory pool |
| `rag_similarity_v2_weather/` | Memory library | 6月 EnergyBridge RAG+weather memory pool |
| **`july_2025_held_out_evaluation/`** | **Query set** | **7月 held-out target for July generalization evaluation** |
