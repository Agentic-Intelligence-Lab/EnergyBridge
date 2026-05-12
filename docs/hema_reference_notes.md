# HEMA Reference Notes

**Date:** 2026-05-12  
**Source:** `references/HEMA/` (GPLv3 — do not copy code; design inspiration only)

---

## 1. HEMA Overview

| Aspect | HEMA | EnergyBridge |
|---|---|---|
| Agent architecture | Hierarchical: LLM classifier → Analysis/Knowledge/Control sub-agents | Single sequential 10-node pipeline |
| Control target | Smart home devices (thermostat, EV) via JSON state files | HVAC setpoints via EnergyPlus actuators |
| Evaluation | Multi-scenario, multi-persona, objective metrics + LLM judge | Roleplay preference learning + VPP compliance |
| Building simulation | None (mock devices) | EnergyPlus co-simulation |
| Grid/VPP integration | None | VPP-1 backed DR event system |
| License | GPLv3 | Independent |

---

## 2. Directly Reusable Ideas

### 2a. Scenario + Persona Separation

HEMA clearly separates:
- **Scenario** (WHAT): `primary_goal`, `success_criteria`, `opening_message`, `expected_device_changes`
- **Persona** (WHO): `background`, `technical_level`, `communication_style`, `typical_behaviors`

**For EnergyBridge:** Current `SimulatedUser` mixes persona and scenario.
Separating them as simple dataclasses enables `for scenario in scenarios: for persona in personas: run()`.

### 2b. `expected_device_changes` Verification Pattern

```python
expected_device_changes = {
    "thermostat": {"temperature": {"_in_range": [24, 26]}},
}
```

EnergyBridge equivalent (already computable from existing fields):
```python
expected_outcomes = {
    "hvac_setpoint": {"_in_range": [24, 26]},
    "safety_ok": True,
    "meets_vpp_requirement": True,
}
```

### 2c. Three-Tier Objective Metrics

- **Tier 1**: Pure counting (no LLM) — turn count, response length
- **Tier 2**: LLM semantic extraction — questions answered, recommendations given
- **Tier 3**: Factual claims verification — LLM extracts claim-value pairs, arithmetic check

EnergyBridge has Tier 1 equivalents. Adding Tier 3-style check for setpoint correctness
(does `control_plan.setpoint` fall in VPP-requested range?) requires no new infrastructure.

### 2d. `TaskCompletionMetrics` Dataclass

```python
@dataclass
class TaskCompletionMetrics:
    goal_achieved: bool
    turns_to_completion: Optional[int]
    task_efficiency: float  # max_turns / turns_to_completion
    goal_progress_score: int  # 1-5
```

EnergyBridge could add `goal_achieved` (did agent reduce load as requested?) with minimal work.

---

## 3. Useful Design Inspiration (do not copy code)

### 3a. Device State as JSON Config File

HEMA loads `data/device_config/demo_home_devices.json` as the home state baseline.
EnergyBridge hardcodes mock state in `simulation/home.py`.
A JSON home config enables: multiple scenarios, initial condition injection, reproducibility.

### 3b. Simulated User Negative Instructions

HEMA's `SIMULATED_USER_SYSTEM_PROMPT` explicitly instructs the LLM to NOT:
- Break character or mention it's an AI
- Give perfect, well-structured responses
- Be overly polite unless persona requires it
- Keep asking tangential questions after main goal is answered

Worth adopting for robustness in EnergyBridge roleplay evaluation.

### 3c. `OpeningMode` Enum (CONTROLLED vs RANDOM)

CONTROLLED: reproducible experiments (same opening each run)
RANDOM: robustness testing (varied phrasings)
Useful for benchmark reproducibility control.

### 3d. `run_experiment.py` / `run_comparison.py` Separation

Two separate scripts: one runs one agent on all scenarios, one compares agent variants.
This is the skeleton EnergyBridge needs for a benchmark harness.

---

## 4. Irrelevant Parts

| HEMA Component | Reason |
|---|---|
| FastAPI + REST routes (`api/`) | EnergyBridge is a local agent loop |
| React frontend | No UI needed |
| RAG knowledge base | EnergyBridge uses memory store |
| LLM provider cascade | EnergyBridge uses single endpoint |
| TOU rate analysis tools | VPP-1 handles grid pricing |
| Solar analysis tools | Out of scope for current model |

---

## 5. Small Code Pieces Worth Adapting (write from scratch, not copy)

### EBScenario dataclass

```python
# energybridge/evaluation/scenario.py  (new file, ~20 lines)
@dataclass
class EBScenario:
    id: str
    name: str
    idf_path: str
    epw_path: str
    vpp_context: dict
    user_profile: dict
    trigger_hour: float
    expected_outcomes: dict
    episode_length_hours: float = 1.0
```

### EBPersona dataclass

```python
# energybridge/evaluation/persona.py  (new file, ~15 lines)
@dataclass
class EBPersona:
    id: str
    comfort_priority: float
    cost_priority: float
    grid_priority: float
    preferred_temp_min: float
    preferred_temp_max: float
    allow_pre_cooling: bool
    allow_temp_drift: bool
    description: str
```

Both are formalizations of what already exists implicitly in `simulation/user.py`.
