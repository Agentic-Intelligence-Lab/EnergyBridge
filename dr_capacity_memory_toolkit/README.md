# DR Capacity Memory Toolkit

Per-method, per-month DR event libraries used by the capacity-shed evaluation
pipeline. Each directory is a self-contained snapshot of one control method's
historical DR events for one month, in a common schema (see "Common `data/`
layout" below). The evaluation script treats every directory the same way:
any one of them can serve as the retrieval pool, and any one (the same
directory for a leave-one-out self-check, or a different month for a
held-out check) can serve as the query set.

## Directories

| Directory | Method | Month | Primary data file |
|---|---|---|---|
| `june_2025_daily_eb/` | EnergyBridge (RAG + weather similarity) | June 2025 | `energybridge_daily_dr_memory_rag_v2_weather.json` |
| `july_2025_daily_eb/` | EnergyBridge | July 2025 | `energybridge_daily_dr_memory.json` |
| `june_2025_daily_rl/` | rl_ppo_pref_v2 | June 2025 | `rl_ppo_pref_v2_daily_dr_memory.json` |
| `july_2025_daily_rl/` | rl_ppo_pref_v2 | July 2025 | `rl_ppo_pref_v2_daily_dr_memory.json` |
| `june_2025_daily_rule_milp/` | rule_milp | June 2025 | `rule_milp_daily_dr_memory.json` |
| `july_2025_daily_rule_milp/` | rule_milp | July 2025 | `rule_milp_daily_dr_memory.json` |
| `june_2025_daily_mpc/` | mpc_dynamic | June 2025 | `mpc_dynamic_daily_dr_memory.json` |
| `july_2025_daily_mpc/` | mpc_dynamic | July 2025 | `mpc_dynamic_daily_dr_memory.json` |
| `june_2025_daily_hema/` | hema_agent | June 2025 | `hema_agent_daily_dr_memory.json` |
| `july_2025_daily_hema/` | hema_agent | July 2025 | `hema_agent_daily_dr_memory.json` |

These 10 directories are the 5-method x 2-month matrix used by the
cross-method capacity-shed comparison. See `july_2025_daily_eb/README.md`
for how the EnergyBridge datasets were generated (EnergyPlus simulation
recipe); the other 8 follow the same `no_dr` + method simulation pattern,
substituting the corresponding controller.

**`june_2025_daily_eb_rule_milp/` is not part of this matrix.** It predates
the 5-method comparison and holds a different, combined controller
(EnergyBridge-guided rule+MILP dispatch, method label `eb_rule_milp`) used
for an earlier evaluation. Don't confuse it with `june_2025_daily_rule_milp/`
(the standalone `rule_milp` baseline above) -- the two are different
controllers with similarly-named directories. See its own README for details.

## Common `data/` layout

Every one of the 10 matrix directories has the same six files:

- `<method>_daily_dr_memory.json` (or the method-specific filename in the
  table above): the per-event memory records themselves -- one entry per
  simulated DR event, with the fields the retrieval/reporting pipeline reads
  (`trigger_h`, `end_h`, `no_dr_baseline_kwh`, `realized_delivery_kwh`,
  `household_id`, `city`, `weather_features`, etc.).
- `daily_dr_memory_summary_raw.{json,csv}`: one row per simulated day
  (including the paired `no_dr` run), before counterfactual settlement.
- `daily_dr_memory_summary_with_counterfactual.{json,csv}`: same rows after
  matching each method run against its `no_dr` counterfactual for that
  household/city/day.
- `daily_dr_memory_no_dr_counterfactual_library.json`: the `no_dr` baseline
  library derived from that month's `no_dr` runs, kept so the counterfactual
  settlement is reproducible without re-simulating `no_dr`.

## Running an evaluation

`experiments/benchmark/run_capacity_shed_evaluation.py` is the single,
method-agnostic entry point for evaluating any of the 5 methods against
this toolkit. It rebuilds each query event purely from the memory record's
own fields, so it never needs to re-read an external `benchmark_result.json`
-- this matters because the MPC/HEMA datasets were originally simulated on a
different machine and their `source_result_path` values aren't reachable
from here.

```bash
# June leave-one-out self-check, real LLM band choice per event.
python experiments/benchmark/run_capacity_shed_evaluation.py \
  --method rl --pool-month june --query-month june \
  --output /tmp/rl_june_loo.json

# July held-out using June as the retrieval pool.
python experiments/benchmark/run_capacity_shed_evaluation.py \
  --method eb --pool-month june --query-month july \
  --output analysis/eb_july_self_to_self.json

# Same, plus an importance-sampling-weighted quantile correction
# (Germany/Tianjin only -- importance_sampling/IS_result only has
# June->July weight packages for those two cities)
python experiments/benchmark/run_capacity_shed_evaluation.py \
  --method eb --pool-month june --query-month july --apply-is \
  --output analysis/eb_july_self_to_self_is.json
```

`--dry-run` skips the LLM and deterministically picks the balanced/p70 band
for every event instead, making zero API calls -- the same convention used by
`dr_event_memory_library.py`'s `--dry-run` and every other script under
`experiments/benchmark/`. Useful for a fast sanity check of the retrieval
math before spending on the real run. `--limit N` caps how many query events
are evaluated, for a cheap pilot run before scaling to the full month.

The underlying retrieval (`energybridge.quantification.dr_event_memory.
estimate_event_capacity_from_memory`) and LLM band-choice + guardrails
(`energybridge.quantification.agent_capacity_reporter.
report_event_capacity_with_agent`) are unchanged production code; the script
only assembles the query event/metadata dicts these functions expect.
