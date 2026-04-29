# EnergyBridge Minimal Agent Loop: Vibe Coding Guide

## 0. Repository Context

You are working in:

```bash
~/work/EnergyBridge
```

EnergyBridge is a home-grid coordination agent system. The goal is to build an agent framework for connecting:

- user preferences,
- VPP/grid signals,
- home/device state,
- control/MPC modules,
- safety validation,
- memory and trajectory logging.

We have decided to build our own agent framework using **LangChain + LangGraph**.

The external HEMA repository is only a reference system. It must not be copied into EnergyBridge because HEMA is GPLv3. You may inspect its high-level folder structure for inspiration, but you must not copy, translate, or modify its source code into this repository.

## 1. Current Goal

Build a **minimal runnable EnergyBridge agent loop**.

The first version should run locally without real devices, without frontend, without FastAPI, and without requiring a real LLM API key.

The loop should be:

```text
user_input + grid_signal + home_state
→ load memory
→ parse user preference
→ translate grid/VPP signal
→ generate candidate strategy
→ call mock MPC/controller
→ safety validation
→ generate explanation
→ update memory
→ save trajectory log
```

The final demo should run with:

```bash
python examples/run_agent_loop.py
```

## 2. Hard Constraints

Follow these strictly:

1. Do not copy code from `references/HEMA`.
2. Do not modify `references/HEMA`.
3. Do not delete or move `VPP-1`.
4. Do not implement frontend in this stage.
5. Do not implement FastAPI in this stage.
6. Do not connect real devices.
7. Do not call real LLM APIs in the default demo.
8. Do not require an API key for `examples/run_agent_loop.py`.
9. Do not build a complex multi-agent chat system.
10. Keep the first version simple, deterministic, and easy to debug.
11. Keep business skills as ordinary Python functions.
12. Use LangGraph only as the workflow orchestration layer.
13. Keep LLM API access in a dedicated `energybridge/llm/` layer, not scattered inside skills.

## 3. Expected Repository Structure

Create or reuse the following structure:

```text
EnergyBridge/
  README.md
  requirements.txt
  .env.example
  .gitignore

  energybridge/
    __init__.py

    agent/
      __init__.py
      state.py
      graph.py
      nodes.py

    skills/
      __init__.py
      preference_parser.py
      grid_signal_translator.py
      strategy_generator.py
      explanation_generator.py

    llm/
      __init__.py
      client.py
      prompts.py

    grid/
      __init__.py
      vpp_1/
        __init__.py
        schemas.py
        mock_signal.py
        adapter.py

    control/
      __init__.py
      mock_mpc.py
      safety_checker.py
      fallback_controller.py

    memory/
      __init__.py
      store.py

    evaluation/
      __init__.py
      logger.py
      metrics.py

    utils/
      __init__.py
      config.py

  examples/
    run_agent_loop.py
    run_llm_test.py

  docs/
    DEVELOPER_GUIDE.md
    DEV_NOTES.md
    ARCHITECTURE.md
    AGENT_LOOP_VIBECODING_GUIDE.md

  scripts/
    activate_energybridge.sh

  data/
  logs/
```

If some folders already exist, reuse them. Do not overwrite existing important content without checking.

## 4. Environment

The conda environment is:

```bash
energybridge
```

Activation command:

```bash
cd ~/work/EnergyBridge
source ~/miniconda3/etc/profile.d/conda.sh
conda activate energybridge
```

Installed core packages include:

```text
langgraph
langchain
langchain-openai
python-dotenv
pydantic
rich
```

If needed, update `requirements.txt` after changes:

```bash
pip freeze > requirements.txt
```

## 5. State Schema

Create:

```text
energybridge/agent/state.py
```

Define `EnergyBridgeState` using `TypedDict` or Pydantic.

Required fields:

```python
user_input: str
grid_signal: dict
home_state: dict

user_preferences: dict
translated_grid_signal: dict
candidate_strategy: dict
control_plan: dict
safety_report: dict
final_response: str

memory: dict
trajectory: list
```

The state should support the full workflow and be easy to serialize into JSON logs.

## 6. Deterministic Skills

Create the following modules.

### 6.1 Preference Parser

File:

```text
energybridge/skills/preference_parser.py
```

Function:

```python
parse_user_preference(user_input: str) -> dict
```

It should convert natural language into structured preferences.

Return fields:

```python
{
    "comfort_priority": float,
    "cost_priority": float,
    "grid_priority": float,
    "preferred_temp_min": float,
    "preferred_temp_max": float,
    "allow_pre_cooling": bool,
    "allow_temp_drift": bool
}
```

Use simple deterministic rules for now.

Support English and Chinese keywords, such as:

```text
comfort, comfortable, save, cheap, grid, demand response
舒服, 舒适, 省电, 便宜, 电网, 削峰, 需求响应
```

### 6.2 Grid Signal Translator

File:

```text
energybridge/skills/grid_signal_translator.py
```

Function:

```python
translate_grid_signal(grid_signal: dict) -> dict
```

Input example:

```python
{
    "type": "DR_EVENT",
    "start_time": "18:00",
    "end_time": "19:00",
    "target_reduction_kw": 0.5,
    "price_level": "high"
}
```

Return fields:

```python
{
    "event_type": str,
    "price_level": str,
    "target_reduction_kw": float,
    "start_time": str,
    "end_time": str,
    "control_intent": str,
    "urgency": str
}
```

Possible `control_intent` values:

```text
normal_operation
cost_saving
reduce_load
```

### 6.3 Strategy Generator

File:

```text
energybridge/skills/strategy_generator.py
```

Function:

```python
generate_candidate_strategy(
    user_preferences: dict,
    translated_grid_signal: dict,
    home_state: dict
) -> dict
```

Return fields:

```python
{
    "mode": str,
    "recommended_setpoint": float,
    "pre_cooling": bool,
    "expected_user_impact": str,
    "rationale": list[str]
}
```

The logic should reflect:

- if grid event requests load reduction, increase setpoint moderately within comfort bounds;
- if price is high, use cost-saving mode;
- if no grid pressure, preserve comfort;
- never intentionally produce obviously unsafe plans.

### 6.4 Explanation Generator

File:

```text
energybridge/skills/explanation_generator.py
```

Function:

```python
generate_explanation(
    candidate_strategy: dict,
    control_plan: dict,
    safety_report: dict
) -> str
```

It should produce a concise user-facing explanation.

If the safety checker rejects the plan, explain the rejection.

## 7. Mock Control and Safety

### 7.1 Mock MPC

File:

```text
energybridge/control/mock_mpc.py
```

Function:

```python
run_mock_mpc(
    candidate_strategy: dict,
    home_state: dict,
    translated_grid_signal: dict
) -> dict
```

Return fields:

```python
{
    "action": "set_hvac_temperature",
    "setpoint": float,
    "duration_minutes": int,
    "estimated_power_kw": float,
    "estimated_reduction_kw": float,
    "controller": "mock_mpc_v0"
}
```

This is a placeholder for future real MPC integration.

### 7.2 Safety Checker

File:

```text
energybridge/control/safety_checker.py
```

Function:

```python
validate_safety(
    control_plan: dict,
    user_preferences: dict,
    home_state: dict
) -> dict
```

Check:

- preferred temperature bounds;
- hard HVAC temperature range `[18, 30]`;
- return structured report.

Return fields:

```python
{
    "safe": bool,
    "violations": list[str],
    "checked_rules": list[str]
}
```

Safety should be deterministic. Do not use LLM for hard safety validation.

### 7.3 Fallback Controller

File:

```text
energybridge/control/fallback_controller.py
```

Function:

```python
fallback_control_plan(home_state: dict, reason: str) -> dict
```

It should return a conservative safe plan when the main plan is rejected.

This can be simple in the first version.

## 8. Memory and Logging

### 8.1 Memory Store

File:

```text
energybridge/memory/store.py
```

Functions:

```python
load_memory(path: str = "logs/memory.json") -> dict
save_memory(memory: dict, path: str = "logs/memory.json") -> None
update_memory(memory: dict, episode: dict) -> dict
```

Use JSON storage for now.

Memory structure:

```python
{
    "hard_constraints": {},
    "stable_preferences": {},
    "contextual_preferences": {},
    "episodic_logs": []
}
```

### 8.2 Trajectory Logger

File:

```text
energybridge/evaluation/logger.py
```

Function:

```python
save_trajectory(state: dict, log_dir: str = "logs") -> str
```

It should save full trajectory to a timestamped JSON file:

```text
logs/trajectory_YYYYMMDD_HHMMSS.json
```

The JSON should be human-readable with indentation.

## 9. LangGraph Workflow

Create:

```text
energybridge/agent/graph.py
```

Use LangGraph `StateGraph`.

Workflow:

```text
START
→ load_memory
→ parse_preference
→ translate_grid
→ generate_strategy
→ control
→ safety
→ explanation
→ memory_update
→ logging
→ END
```

Expose:

```python
build_energybridge_graph()
```

Each node should:

1. read from `EnergyBridgeState`;
2. write outputs back to state;
3. append a record to `state["trajectory"]`.

Trajectory step format:

```python
{
    "node": "node_name",
    "output": {...}
}
```

Do not directly call external LLM APIs inside this default graph.

## 10. LLM API Layer

We will later call external LLM APIs, but the system should not be tied to GPT or Claude only.

Create a provider-agnostic LLM wrapper under:

```text
energybridge/llm/
```

Implement a minimal OpenAI-compatible client first, because many providers and relay services support:

```text
base_url + api_key + model
```

This can support OpenAI-compatible endpoints, DeepSeek, Qwen-compatible endpoints, OpenRouter, and many transfer/API relay services.

### 10.1 Required Files

```text
energybridge/llm/__init__.py
energybridge/llm/client.py
energybridge/llm/prompts.py
.env.example
```

### 10.2 `.env.example`

Create or update:

```text
.env.example
```

Required content:

```bash
# Whether to enable LLM calls in optional modules
USE_LLM=false

# Provider can be openai_compatible for now
LLM_PROVIDER=openai_compatible

# OpenAI-compatible endpoint
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your_api_key_here
LLM_MODEL=gpt-4o-mini

# Generation parameters
LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=1024
```

Make sure `.env` is ignored by git.

### 10.3 LLM Client

File:

```text
energybridge/llm/client.py
```

Implement:

```python
class LLMClient:
    def chat(self, system_prompt: str, user_prompt: str) -> str:
        ...
```

Requirements:

1. Read configuration from `.env`.
2. Use `OpenAI(api_key=..., base_url=...)` from the OpenAI Python SDK.
3. Do not hard-code API keys.
4. Raise a clear error if `LLM_API_KEY` is missing.
5. Do not call this client in the default agent loop.
6. Leave room for future Anthropic-native and local model clients, but do not implement them now unless necessary.

### 10.4 Optional LLM Test Script

Create:

```text
examples/run_llm_test.py
```

It should:

1. load `.env`;
2. check `USE_LLM=true`;
3. call the LLM wrapper with a simple prompt;
4. print the response;
5. fail gracefully if no API key is configured.

This script is optional to run and should not be required for the main demo.

## 11. VPP-1 Integration Boundary

Do not tightly couple VPP-1 with the agent graph.

Reserve this module:

```text
energybridge/grid/vpp_1/
```

Purpose:

- convert VPP-1 raw signals into EnergyBridge internal `grid_signal` schema;
- keep VPP-specific parsing separate from agent logic.

Create:

```text
energybridge/grid/vpp_1/schemas.py
energybridge/grid/vpp_1/mock_signal.py
energybridge/grid/vpp_1/adapter.py
```

The internal `grid_signal` should look like:

```python
{
    "type": "DR_EVENT",
    "start_time": "18:00",
    "end_time": "19:00",
    "target_reduction_kw": 0.5,
    "price_level": "high"
}
```

## 12. Runnable Demo

Create:

```text
examples/run_agent_loop.py
```

It should:

1. build the graph;
2. define sample initial state;
3. invoke the graph;
4. print:
   - final response;
   - control plan;
   - safety report;
   - trajectory steps;
5. save logs.

Sample initial state:

```python
{
    "user_input": "我希望尽量舒服，但如果电网有需求，也可以短时间配合削峰。",
    "grid_signal": {
        "type": "DR_EVENT",
        "start_time": "18:00",
        "end_time": "19:00",
        "target_reduction_kw": 0.5,
        "price_level": "high"
    },
    "home_state": {
        "indoor_temp": 25.8,
        "outdoor_temp": 33.0,
        "hvac_setpoint": 25.0,
        "hvac_power_kw": 2.2,
        "occupancy": True
    },
    "trajectory": []
}
```

Run command:

```bash
python examples/run_agent_loop.py
```

Expected output:

- final response printed;
- control plan printed;
- safety report printed;
- trajectory printed;
- `logs/memory.json` created;
- `logs/trajectory_*.json` created.

## 13. Documentation Updates

Update:

```text
docs/DEVELOPER_GUIDE.md
docs/DEV_NOTES.md
docs/ARCHITECTURE.md
README.md
```

### 13.1 `docs/DEVELOPER_GUIDE.md`

Include:

- environment activation command;
- dependency installation;
- how to run demo;
- how to test optional LLM wrapper;
- current module layout;
- how to add a new skill.

### 13.2 `docs/DEV_NOTES.md`

Include:

- HEMA is GPLv3 and only used as reference;
- EnergyBridge implements its own code;
- current first-stage scope;
- current TODO list.

### 13.3 `docs/ARCHITECTURE.md`

Include:

- module structure;
- workflow diagram in text;
- deterministic modules;
- replaceable modules;
- future LLM/MPC/VPP integration plan.

### 13.4 `README.md`

Keep it concise:

- what EnergyBridge is;
- current status;
- quick start;
- demo command;
- related reference note.

## 14. Verification

After implementation, run:

```bash
python examples/run_agent_loop.py
```

Then check:

```bash
ls -la logs
cat logs/memory.json
```

The demo must run without any LLM API key.

Also run:

```bash
python -m compileall energybridge examples
```

Fix syntax errors if any.

## 15. Final Report

At the end, report:

1. files created or modified;
2. exact command to run the demo;
3. example output summary;
4. whether logs were created;
5. whether `python -m compileall energybridge examples` passed;
6. any errors encountered;
7. next recommended steps.

## 16. Scope Control

Do not over-engineer.

This first stage is not a production system. The goal is to establish a clean, runnable, auditable skeleton.

Future replacements:

```text
rule-based preference parser → LLM-based preference parser
mock grid signal → real VPP-1 adapter
mock MPC → real MPC
JSON memory → SQLite/Postgres/vector memory
CLI demo → FastAPI endpoint
rule-based strategy → LLM + optimizer hybrid strategy
```
