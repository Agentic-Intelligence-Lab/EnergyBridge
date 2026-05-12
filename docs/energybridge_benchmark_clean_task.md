# EnergyBridge Benchmark Work Prompt

## Goal

This task is to restart from a clean, benchmark-focused workflow.

Xudong's assigned work is **not** to finish the EnergyPlus simulator itself.  
Xudong's task is to build the **benchmark layer** around the existing simulator interface.

The benchmark layer should include:

1. scenario configuration;
2. metrics;
3. test algorithms / baselines;
4. evaluation runner;
5. concise progress documentation.

Do **not** rewrite the simulator.  
Do **not** keep large exploratory markdown files that are unrelated to this benchmark task.

---

## Task Division

The project responsibilities are:

### RA / Xuebing: Simulator

Responsible for completing the simulator itself:

- agent obtains parameters from EnergyPlus;
- agent makes decisions based on those parameters;
- agent decisions are applied back to EnergyPlus;
- EnergyPlus-agent loop reliability.

### Xudong: Benchmark Layer

Responsible for turning the simulator into a benchmark:

- define metrics;
- design test algorithms / baselines;
- build evaluation pipeline;
- organize scenario × agent × metric experiments.

### Tiantian: EP Scenarios

Responsible for EnergyPlus scenario construction:

- building structure;
- thermal-zone partition;
- electrical-device layout;
- representative scenarios.

---

## Step 0: Clean Current Repository State

Before doing new work, inspect git state:

```bash
cd /home/ha_agent/work/EnergyBridge
git status
git log --oneline -10
git diff --stat
```

Create a safety branch first:

```bash
git branch backup/pre-benchmark-cleanup-$(date +%Y%m%d_%H%M%S)
```

Then inspect recent commits:

```bash
git show --stat HEAD
git show --stat HEAD~1
git diff --name-only HEAD~2..HEAD
```

If recent commits contain exploratory simulator/debugging changes or large generated docs unrelated to Xudong's benchmark work, roll them back safely.

Prefer:

```bash
git revert <commit_hash>
```

Only use hard reset if explicitly safe:

```bash
git reset --hard <target_commit_hash>
```

If rollback may remove useful simulator work, stop and report before proceeding.

---

## Step 1: Remove Unrelated Exploratory Docs

Remove newly generated markdown files that distract from the benchmark task.

Candidate files to delete if they exist and were newly generated for exploration:

```text
docs/codebase_survey.md
docs/run_log.md
docs/control_loop_map.md
docs/hema_reference_notes.md
docs/benchmark_gap_analysis.md
docs/minimal_next_plan.md
docs/vibe_coding_simulator_benchmark_guide.md
```

Use:

```bash
git rm -f docs/codebase_survey.md docs/run_log.md docs/control_loop_map.md \
  docs/hema_reference_notes.md docs/benchmark_gap_analysis.md \
  docs/minimal_next_plan.md docs/vibe_coding_simulator_benchmark_guide.md 2>/dev/null || true
```

Commit cleanup separately:

```bash
git add -A
git commit -m "Clean up exploratory docs before benchmark work"
```

---

## Step 2: Survey the Current Simulator Interface

Create:

```text
docs/benchmark_interface_survey.md
```

Keep it concise. Do **not** write a full codebase survey.

It should answer:

1. What command currently runs one EnergyPlus-agent episode?
2. What inputs does the simulator require?
   - IDF path;
   - EPW path;
   - output directory;
   - trigger hour;
   - VPP context;
   - user input.
3. What outputs are available?
   - agent result;
   - control plan;
   - execution status;
   - EnergyPlus output path;
   - logs.
4. Which outputs are reliable enough for benchmark metrics now?
5. Which outputs are still uncertain?
6. What still depends on RA's simulator work?

Important: if EnergyPlus physical response has not been verified through output time series, do not claim the physical closed loop is fully validated.

---

## Step 3: Add Minimal Scenario Config

Create:

```text
data/scenarios/us_chicago_vpp_smoke.json
```

Use the available US EPW as a smoke-test scenario because Tianjin EPW is currently missing.

Suggested content:

```json
{
  "id": "us_chicago_vpp_smoke",
  "name": "US Chicago EPW VPP Smoke Test",
  "idf_path": "Family_Model/Family_Simple_3day.idf",
  "epw_path": "/home/ha_agent/EnergyPlus-24-1-0/WeatherData/USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw",
  "output_dir": "logs/benchmark_runs/us_chicago_vpp_smoke",
  "trigger_hour": 42.0,
  "user_input": "我希望舒适，但可以配合削峰。",
  "vpp_context": {
    "vpp_task_type": "INVITATION_DEMAND_RESPONSE",
    "requested_reduction_kw": 0.5,
    "duration_minutes": 60,
    "urgency": "medium"
  },
  "expected_outcomes": {
    "agent_triggered": true,
    "valid_control_plan": true,
    "execution_status": "executed"
  },
  "notes": "Smoke-test scenario using US EPW. Not a Tianjin local scenario."
}
```

Do not assume Tianjin EPW exists.

---

## Step 4: Add Benchmark Metrics

Create:

```text
energybridge/evaluation/benchmark_metrics.py
```

Implement metrics that can be computed from the current simulator/agent result.

At minimum:

### API-Level Control Metrics

- `agent_triggered`
- `valid_control_plan`
- `action_type`
- `setpoint`
- `execution_status`
- `safety_ok`

### VPP Metrics

- `requested_reduction_kw`
- `estimated_reduction_kw`
- `estimated_vpp_compliance`

### Comfort Metrics

- `indoor_temp_at_event`
- `outdoor_temp_at_event`
- `setpoint_after_action`
- `simple_temp_deviation`

### Energy/Power Proxy Metrics

- `hvac_power_kw_at_event`
- `facility_power_kw_at_event`

Important:

These are **event-level proxy metrics**, not full physical energy-saving metrics.

Do not claim actual energy saving until EnergyPlus output time series is parsed.

---

## Step 5: Add Simple Baselines

Create one of:

```text
energybridge/benchmark/baselines.py
```

or:

```text
energybridge/evaluation/baselines.py
```

Implement three pure-function baselines:

1. `comfort_first`
   - small or no setpoint change;
   - prioritizes user comfort.

2. `grid_first`
   - larger setpoint increase during DR event;
   - prioritizes load reduction.

3. `rule_based_balanced`
   - moderate setpoint adjustment based on urgency and requested reduction.

Example function signature:

```python
def rule_based_balanced(home_state: dict, vpp_context: dict, user_input: str) -> dict:
    ...
    return control_plan
```

Do not integrate MPC yet.

---

## Step 6: Add Minimal Benchmark Runner

Create:

```text
examples/run_benchmark_smoke.py
```

Required command:

```bash
python examples/run_benchmark_smoke.py \
  --scenario data/scenarios/us_chicago_vpp_smoke.json \
  --agent current
```

Optional support:

```bash
python examples/run_benchmark_smoke.py \
  --scenario data/scenarios/us_chicago_vpp_smoke.json \
  --agent rule_based_balanced
```

The runner should save outputs under:

```text
logs/benchmark_runs/<timestamp>/
```

Save:

```text
metrics.json
summary.md
raw_agent_result.json
```

The runner should not require Tianjin EPW.

---

## Step 7: Write Benchmark Progress Doc

Create:

```text
docs/benchmark_progress.md
```

Keep it short.

It should include:

1. what cleanup was done;
2. what benchmark components were added;
3. how to run the benchmark smoke test;
4. current limitations:
   - Tianjin EPW missing;
   - simulator physical closed-loop validation belongs to simulator-side work;
   - event-level metrics are proxy metrics;
   - full trajectory metrics require future ESO/CSV parsing;
5. next steps:
   - plug in Tianjin EPW when available;
   - add richer EP scenarios from Tiantian;
   - add real trajectory metrics once simulator outputs are finalized.

---

## Commit Policy

Use separate commits.

### Commit 1: Cleanup

```bash
git add -A
git commit -m "Clean up exploratory docs before benchmark work"
```

### Commit 2: Benchmark Scaffold

```bash
git add data/scenarios energybridge/evaluation energybridge/benchmark \
  examples/run_benchmark_smoke.py docs/benchmark_interface_survey.md \
  docs/benchmark_progress.md
git commit -m "Add initial benchmark scaffold for EnergyBridge"
```

Do not mix simulator fixes and benchmark scaffold in the same commit.

---

## Constraints

- Do not rewrite `energybridge/simulation/` unless absolutely necessary.
- Do not take over RA's simulator implementation.
- Do not claim EnergyPlus physical closed loop is fully validated unless output time-series evidence exists.
- Keep benchmark work modular.
- Keep new docs concise.
- Delete unrelated generated docs that distract from the benchmark task.
- If rollback is risky, stop and report before modifying.

---

## Final Report to Xudong

At the end, report:

1. which commits were reverted or cleaned;
2. which markdown files were deleted;
3. which new benchmark files were added;
4. the command to run the benchmark smoke test;
5. where `metrics.json` and `summary.md` are saved;
6. what still depends on RA's simulator work;
7. what still depends on Tianjin EPW / Tiantian's scenarios.

Use this wording:

> This is a benchmark scaffold over the current simulator interface, not a completed EnergyPlus benchmark yet.
