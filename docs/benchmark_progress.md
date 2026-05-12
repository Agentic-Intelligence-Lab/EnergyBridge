# Benchmark Progress

**Date:** 2026-05-12

> This is a benchmark scaffold over the current simulator interface, not a completed EnergyPlus benchmark yet.

---

## 1. Cleanup Done

Removed 7 auto-generated exploratory docs (commit `806c959`):
`codebase_survey.md`, `run_log.md`, `control_loop_map.md`, `hema_reference_notes.md`,
`benchmark_gap_analysis.md`, `minimal_next_plan.md`, `vibe_coding_simulator_benchmark_guide.md`

Simulator files (`energybridge/simulation/`, `examples/run_eplus_agent_loop.py`,
`Family_Model/Family_Simple_3day.idf`) kept intact — not Xudong's scope.

---

## 2. Benchmark Components Added

| File | Purpose |
|---|---|
| `data/scenarios/us_chicago_vpp_smoke.json` | First scenario config (Chicago EPW smoke test) |
| `energybridge/evaluation/benchmark_metrics.py` | Event-level proxy metrics dataclass + extraction |
| `energybridge/evaluation/baselines.py` | 3 rule-based baselines |
| `examples/run_benchmark_smoke.py` | Smoke-test runner |
| `docs/benchmark_interface_survey.md` | Concise simulator interface survey |
| `docs/benchmark_progress.md` | This file |

---

## 3. How to Run

**Full LLM agent (needs EnergyPlus + LLM API):**

```bash
python examples/run_benchmark_smoke.py \
  --scenario data/scenarios/us_chicago_vpp_smoke.json \
  --agent current
```

**Rule-based baseline (fast, no EnergyPlus, no LLM):**

```bash
python examples/run_benchmark_smoke.py \
  --scenario data/scenarios/us_chicago_vpp_smoke.json \
  --agent rule_based_balanced
# also: comfort_first | grid_first
```

**Output:** `logs/benchmark_runs/us_chicago_vpp_smoke/<timestamp>/metrics.json` + `summary.md` + `raw_agent_result.json`

---

## 4. Current Limitations

- **Tianjin EPW missing**: all EP runs use Chicago climate; results are not valid for the target building.
- **EP physical closed-loop not validated**: `execution_result.status="executed"` only confirms Python write; temperature response in EP is unverified (RA/Xuebing's work).
- **Event-level metrics are proxy metrics**: `estimated_reduction_kw` / `estimated_vpp_compliance` are agent estimates, not physical measurements.
- **Baseline runs use mock home_state**: rule-based baselines test decision logic only, not EP integration.
- **No full trajectory metrics**: actual kWh, comfort violation hours require `eso_parser.py` (future work).

---

## 5. Next Steps

| Priority | Action | Owner |
|---|---|---|
| High | Obtain Tianjin EPW; add `tianjin_*.json` scenario | Tiantian / team |
| High | Validate EP physical closed loop (`.eso` time series) | Xuebing (simulator) |
| Medium | Add richer IDF scenarios (multi-zone, EV, hot water) | Tiantian → Xudong |
| Medium | Implement `evaluation/eso_parser.py` for actual energy metrics | Xudong |
| Low | Connect MPC baseline (`Family_Model/control_model/`) | Xudong + Xuebing |
| Low | Multi-scenario × multi-agent loop harness | Xudong |


---

## 6. Metric Extraction & Verification Layer (added 2026-05)

### New modules

| File | Purpose |
|------|---------|
| `energybridge/evaluation/trajectory_metrics.py` | Unified metric extraction from trajectory JSON or AgentResult; stdlib only |
| `examples/inspect_metrics.py` | CLI: inspect single / latest / all trajectory metrics |
| `examples/export_metrics_table.py` | CLI: scan all logs and export full CSV table |
| `docs/physical_feedback_verification.md` | Simulator interface audit: what is truly from EP vs. Python-reflected |
| `docs/metrics_schema.md` | Full metric field taxonomy with source labels |

### Metric status taxonomy

Each metric dict includes a `metric_status` block:
```
api_metrics:                available | missing
event_physical_snapshot:    available | partial | missing
user_preference_metrics:    available | missing
physical_trajectory_metrics: not_implemented   ← requires .eso parsing
```

### Verified EP variables (snapshot-level)

| home_state field       | EnergyPlus variable                        | Verified? |
|------------------------|--------------------------------------------|-----------|
| `indoor_temp`          | Zone Mean Air Temperature                  | ✓ EP read |
| `outdoor_temp`         | Zone Outdoor Air Drybulb Temperature       | ✓ EP read |
| `hvac_power_kw`        | Cooling Coil Total Cooling Rate            | ✓ EP read |
| `facility_power_kw`    | Facility Total Electricity Demand Rate     | ✓ EP read |
| `hvac_setpoint`        | —                                          | Python-reflected (not read from EP) |
| `occupancy`            | —                                          | Hardcoded True |

### How to use

**Inspect latest run:**
```bash
python examples/inspect_metrics.py --latest
```

**Inspect specific trajectory:**
```bash
python examples/inspect_metrics.py --trajectory logs/trajectory_20260512_120758.json
```

**Table view + CSV export:**
```bash
python examples/inspect_metrics.py --dir logs --export
python examples/export_metrics_table.py
# Output: logs/metric_exports/metrics_<timestamp>.csv
```

**Benchmark runner now outputs unified_metrics.json automatically** alongside the existing `metrics.json`.

### Not yet implemented

- Actual energy kWh, peak power, comfort violation minutes → require `.eso` parsing (RA scope)
- `post_action_temperature_delta`, `setpoint_tracking_error` → same
- All future fields are `None` and labelled `future_placeholder` in `METRIC_SOURCES`

---
