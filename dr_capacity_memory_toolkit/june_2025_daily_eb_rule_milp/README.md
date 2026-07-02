# June 2025 Daily DR Capacity Memory Toolkit

This directory is a reusable capacity-reporting toolkit built from June 2025
historical DR simulations. It is intentionally separate from benchmark output
folders: these files are reusable historical data and reporting utilities, not
one-off evaluation results.

The historical data was generated as independent daily samples:

```text
30 historical days x 5 households x 2 cities x 2 methods(no_dr, eb_rule_milp)
= 600 one-day runs
```

The EB+rule+MILP memory contains 300 calibrated DR events. It is used to report
capacity for future VPP events without rerunning the June historical simulation.

## Contents

```text
config/
└── vpp_events_june_memory.json

data/
├── daily_dr_memory_summary_raw.json
├── daily_dr_memory_summary_raw.csv
├── daily_dr_memory_summary_with_counterfactual.json
├── daily_dr_memory_summary_with_counterfactual.csv
├── daily_dr_memory_no_dr_counterfactual_library.json
└── eb_rule_milp_daily_dr_memory.json
```

- `config/vpp_events_june_memory.json`: fixed June historical DR event schedule.
- `data/daily_dr_memory_summary_raw.*`: 600 one-day historical run summaries.
- `data/daily_dr_memory_summary_with_counterfactual.*`: summaries after matching
  each EB+rule+MILP run against the corresponding no-DR counterfactual.
- `data/daily_dr_memory_no_dr_counterfactual_library.json`: reusable historical
  no-DR baseline library for the June memory runs.
- `data/eb_rule_milp_daily_dr_memory.json`: the main historical DR memory used
  for future capacity reporting.

This toolkit does **not** include per-evaluation capacity reports. Those should
be regenerated for each new target benchmark, because the target run, VPP
events, and counterfactual settlement can change.

## Use The Memory On A New Evaluation

First, the target evaluation summary must already include no-DR counterfactual
delivery fields, such as:

```text
counterfactual_actual_shed_total_kwh
counterfactual_actual_shed_avg_per_hour_kwh
counterfactual_capacity_upper_bound_avg_per_hour_kwh
```

Then run the top-k agent capacity reporter:

```bash
python experiments/benchmark/dr_event_memory_library.py agent-report \
  --memory dr_capacity_memory_toolkit/june_2025_daily_eb_rule_milp/data/eb_rule_milp_daily_dr_memory.json \
  --summary-json benchmark_results/<DATE>/_batch_logs/<TARGET_WITH_COUNTERFACTUAL>.json \
  --output-summary-json benchmark_results/<DATE>/_batch_logs/<TARGET_WITH_AGENT_CAPACITY>.json \
  --methods eb_rule_milp \
  --top-k 5 \
  --write-result-json
```

For a no-API dry run, add `--dry-run`. Dry-run selects the calibrated/P50 band
deterministically, which is useful for checking the retrieval and distribution
math before calling the LLM:

```bash
python experiments/benchmark/dr_event_memory_library.py agent-report \
  --memory dr_capacity_memory_toolkit/june_2025_daily_eb_rule_milp/data/eb_rule_milp_daily_dr_memory.json \
  --summary-json benchmark_results/<DATE>/_batch_logs/<TARGET_WITH_COUNTERFACTUAL>.json \
  --output-summary-json benchmark_results/<DATE>/_batch_logs/<TARGET_DRYRUN_AGENT_CAPACITY>.json \
  --methods eb_rule_milp \
  --top-k 5 \
  --dry-run
```

## How Top-k Reporting Works

The reporter retrieves similar historical DR events from
`eb_rule_milp_daily_dr_memory.json` using:

- household/persona id,
- city,
- method,
- VPP hour,
- event duration,
- target no-DR baseline similarity,
- source day as a weak tie-breaker.

When enough same-hour examples exist, retrieval is restricted to the same VPP
hour so that a 16:00 event does not contaminate an 18:00 report.

For each retrieved event, historical delivery is mildly adjusted by:

```text
target no-DR baseline / historical no-DR baseline
```

The adjustment is clamped to `[0.8, 1.25]` so weather/load differences can be
reflected without allowing one outlier day to dominate.

The top-k adjusted delivery values form a distribution. The agent is not allowed
to invent a free-form capacity number; it must choose one precomputed band:

```text
P25 -> conservative
P50 -> calibrated
P75 -> assertive
```

Important summary fields written by `agent-report`:

```text
agent_capacity_report_total_kwh
agent_capacity_report_avg_kw
agent_capacity_report_primary_distribution_position
agent_capacity_report_distribution_position_counts
agent_capacity_report_distribution_positions
agent_capacity_report_primary_choice
agent_capacity_report_choice_counts
```

Example:

```text
agent_capacity_report_primary_distribution_position = p50
agent_capacity_report_distribution_position_counts = p25=0,p50=7,p75=0
agent_capacity_report_primary_choice = calibrated
```

## Regenerate The Historical Memory Only If Needed

The committed memory should be reused for future evaluations. Regeneration is
only needed when the household definitions, city physics/weather setup,
controller implementation, or historical event schedule changes.

To regenerate the same type of data:

```bash
PYTHONUNBUFFERED=1 \
python experiments/benchmark/run_daily_dr_memory_matrix.py \
  --methods no_dr eb_rule_milp \
  --cities Germany Tianjin \
  --days 30 \
  --start-date 2025-06-01 \
  --vpp-events-json dr_capacity_memory_toolkit/june_2025_daily_eb_rule_milp/config/vpp_events_june_memory.json \
  --date <DATE>_agent_dr_memory_daily \
  --workers 5 \
  --resume
```

After regeneration, copy only the reusable historical artifacts into a new
toolkit directory. Do not commit raw EnergyPlus output folders or one-off
target evaluation reports.
