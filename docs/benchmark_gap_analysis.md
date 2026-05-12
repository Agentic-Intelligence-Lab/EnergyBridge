# Benchmark Gap Analysis

**Date:** 2026-05-12  
**Purpose:** What is missing if EnergyBridge is to become a benchmark.

---

## 1. Scenarios

### Current State
- **1 building model**: `Family_Simple.idf` (Tianjin, single zone `living_unit1`)
- **1 weather**: Tianjin EPW (missing); US fallback only
- **0 scenario config files**: IDF/EPW/trigger_hour are CLI arguments only
- **VPP types**: invitation + emergency both injectable; no automated sweep

### Gaps

| Element | Gap |
|---|---|
| Multiple building models | Only 1 IDF |
| Multiple weather files | Only US EPW available |
| Scenario config files (YAML/JSON) | Absent |
| Multi-device beyond HVAC | Only cooling_sch/heating_sch |
| Multi-event episode | One event per run; no multi-event day |
| Initial condition variation | No mechanism to set indoor_temp, occupancy per scenario |
| Reproducibility seed | No seeding for LLM-backed strategy |

---

## 2. Metrics

### Already Computable

| Metric | Source |
|---|---|
| `expected_reduction_kw` | `control_plan.estimated_reduction_kw` (agent estimate) |
| `expected_energy_kwh` | `metrics.expected_energy_kwh` (estimate) |
| `safety_ok` | `safety_report.safe` |
| `meets_vpp_requirement` | `metrics.meets_vpp_requirement` |
| `api_latency_seconds` | `metrics.api_latency_seconds` |
| `token_usage` | `metrics.token_usage` |
| `user_satisfaction_score` | `user_feedback.satisfaction_score` (1–5) |
| `preference_learning_score` | `evaluation/metrics.learning_score()` |

### Missing

| Metric | How to Get |
|---|---|
| **Actual energy consumption (kWh)** | Parse EP `eplusout.eso` → `Facility Total Electricity Demand Rate` |
| **Temperature deviation** | `Zone Mean Air Temperature` vs `hvac_setpoint` from EP |
| **Comfort violation hours** | Timesteps where `indoor_temp` outside preferred range |
| **Peak power reduction (measured)** | EP `Facility Total Electricity Demand Rate` time series |
| **Setpoint accuracy** | `ActuatorWriter.last_cooling_setpoint` vs `control_plan.setpoint` |
| **Response latency P50/P95** | Aggregate `api_latency_seconds` across episodes |

---

## 3. Algorithms / Baselines

### Current State
Only one agent: **LLM-backed EnergyBridge** (with rule-based fallback if LLM disabled).

### Missing Baselines

| Baseline | Effort |
|---|---|
| Rule-based agent (setpoint table by urgency) | Low (~50 lines) |
| Always-accept (+2°C regardless) | Trivial |
| Comfort-first (never adjust) | Trivial |
| LLM-only agent (no memory) | Low (set `memory={}`) |
| LLM + memory (current) | Done |
| MPC-style agent (`control_model.py` exists) | Medium (needs EP integration) |

`Family_Model/control_model/control_model.py` already provides MPC implementation.

### Missing Infrastructure
- No agent registry / factory
- No common `run_episode(agent, scenario) → metrics` interface
- No cross-agent results aggregation

---

## 4. Evaluation Pipeline

### Current State
```python
# Single-scenario, single-agent, multi-turn roleplay
for turn in range(n_turns):
    run_one_turn(agent, simulated_user, home, grid)
    log_trajectory()
    compute_metrics()
```

### Needed for Benchmark
```python
for scenario in scenarios:
    for agent in agents:
        for episode in range(n_episodes):
            run_episode(agent, scenario) → metrics
        summarize_agent_on_scenario()
    compare_agents()
generate_report()
```

### Specific Gaps

| Element | Status |
|---|---|
| Scenario registry | ❌ Missing |
| Agent factory | ❌ Missing |
| Episode runner | ⚠️ Partial (`simulation/simulation.py` covers single-agent) |
| EP `.eso` results parser | ❌ Missing |
| Cross-agent comparison table | ❌ Missing |
| Benchmark report generator | ❌ Missing |

---

## 5. Summary

| Dimension | Current | Priority Gap |
|---|---|---|
| Scenarios | 1 building, 0 configs | JSON scenario config files |
| Metrics | 8 proxy/estimated | EP `.eso` parser for actual energy |
| Baselines | 1 agent | Rule-based baseline (~50 lines) |
| Pipeline | Multi-turn roleplay | scenario×agent loop harness |
| Reproducibility | No seed, no registry | Scenario configs + deterministic mode |
| Weather | No Tianjin EPW | Obtain from CSWD or EnergyPlus weather portal |
