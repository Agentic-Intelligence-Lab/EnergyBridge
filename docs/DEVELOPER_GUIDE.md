# EnergyBridge Developer Guide

## 1. Environment Activation

```bash
cd ~/work/EnergyBridge
source ~/miniconda3/etc/profile.d/conda.sh
conda activate energybridge
```

## 2. Dependencies

Install from requirements if needed:

```bash
pip install -r requirements.txt
```

## 3. Run Interactive Demo

```bash
python examples/run_agent_loop.py
```

Expected behavior:
- prompts for VPP-1 task mode, user preference, strategy choice, and satisfaction feedback;
- loads a real VPP-1 dispatch task and translates it into internal `grid_demand` plus `vpp_context`;
- when `USE_LLM=true`, calls the configured OpenAI-compatible API for strategy options;
- prints final response, control plan, safety report, execution result, metrics, and trajectory steps;
- creates `logs/memory.json`;
- creates `logs/trajectory_YYYYMMDD_HHMMSS.json`.

Returned metrics include:
- `api_latency_seconds`
- `token_usage`
- `user_satisfaction_score`
- `expected_energy_kwh`
- `meets_vpp_requirement`

## 4. Optional LLM Wrapper Test

1. Copy `.env.example` to `.env` and set values.
2. Set `USE_LLM=true`.
3. Run:

```bash
python examples/run_llm_test.py
```

This test is optional and not required for the default demo.

The local API settings are read from `.env` using these fields:
- `USE_LLM`
- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`

Role-play evaluation uses a second LLM configuration:
- `ROLEPLAY_USE_LLM`
- `ROLEPLAY_LLM_BASE_URL`
- `ROLEPLAY_LLM_API_KEY`
- `ROLEPLAY_LLM_MODEL`

## 5. Run Role-Play Evaluation

```bash
python examples/run_roleplay_evaluation.py
```

Optional arguments:

```bash
python examples/run_roleplay_evaluation.py --turns 5
```

Expected behavior:
- generates one simulated user persona using the role-play model;
- runs 5 full interactions against the EnergyBridge loop;
- stores one isolated memory file per simulated user;
- stores full per-turn logs with interaction content and memory changes;
- writes a final learning summary.

Artifacts are written to:

```text
logs/evaluations/<evaluation_user_id>/
```

Typical files:
- `persona.json`
- `memory.json`
- `turn_01.json` ... `turn_05.json`
- `trajectories/trajectory_*.json`
- `summary.json`

## 6. Run Batch Role-Play Evaluation

```bash
python examples/run_batch_roleplay_evaluation.py --users 10 --turns 5
```

For a larger demo:

```bash
python examples/run_batch_roleplay_evaluation.py --users 20 --turns 5
```

Batch artifacts are written to:

```text
logs/evaluations/batch_<timestamp>_<N>users_<T>turns/
```

The batch directory contains:
- `batch_result.json`: full nested output, including per-user artifacts
- `batch_summary.json`: aggregate metrics plus user-level summary rows
- `batch_summary.csv`: CSV table with one row per simulated user
- `batch_turns.csv`: CSV table with one row per user turn
- `users/<evaluation_user_id>/`: isolated user memory, persona, turns, trajectories, and summary

## 7. Simulation Object Structure

The evaluation simulation follows the requested four-object structure:

- `SimulatedUser` in `energybridge/simulation/user.py`
- `AgentSimulator` in `energybridge/simulation/agent.py`
- `GridSimulator` in `energybridge/simulation/grid.py`
- `HomeSimulator` in `energybridge/simulation/home.py`

The high-level runner is:

- `energybridge/simulation/simulation.py`

Evaluation metrics and learning summaries are in:

- `energybridge/evaluation/metrics.py`

Skill listing is in:

- `energybridge/skills/registry.py`

Memory read/write/update is in:

- `energybridge/memory/store.py`

## 8. Current Module Layout

- `energybridge/agent`: state schema, nodes, graph orchestration
- `energybridge/skills`: deterministic business logic
- `energybridge/control`: mock MPC, safety checker, fallback controller, mock actuator
- `energybridge/grid/vpp_1`: VPP-1 boundary schema and adapter
- `energybridge/memory`: JSON memory load/save/update
- `energybridge/evaluation`: trajectory logger and minimal metrics
- `energybridge/simulation`: simulation objects and simulation runners
- `energybridge/llm`: provider-agnostic LLM access layer
- `examples`: runnable scripts

## 9. Add a New Skill

1. Create a deterministic function in `energybridge/skills/your_skill.py`.
2. Add a node function in `energybridge/agent/nodes.py` to call it.
3. Insert the node in `energybridge/agent/graph.py`.
4. Extend `EnergyBridgeState` in `energybridge/agent/state.py` if new fields are needed.
5. Update docs and add tests as needed.