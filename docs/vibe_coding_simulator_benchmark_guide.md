# Vibe Coding Guide: EP Simulator / Benchmark Exploration

## Goal

The current goal is **not** to redesign the project or implement a full benchmark from scratch.

The goal is to help Xudong quickly understand the existing codebase, run the current simulator/agent pipeline once, and identify what is still missing if we want to later turn it into a benchmark.

Please adapt to the current repository structure. Do not force a new architecture before understanding the existing one.

---

## Background

The teacher's task can be summarized as follows:

1. The simulator should mainly support two things:
   - the agent obtains parameter/state information from EP as decision evidence;
   - the agent applies decisions/actions back into EP.

2. If the simulator is further developed into a benchmark, we need:
   - metrics;
   - test algorithms / baselines;
   - scenario sets;
   - evaluation scripts.

3. EP-side scenario construction still needs more work, including:
   - building structure;
   - thermal-zone partition;
   - electrical-device layout;
   - several representative scenarios.

Xudong's immediate task is to understand the current status and prepare the benchmark direction.

---

## Working Principles

Please follow these principles:

- First explore, then run, then modify.
- Do not refactor early.
- Do not impose a new folder structure before understanding the existing one.
- Prefer documenting the current structure over rewriting it.
- If code changes are necessary, make the smallest safe change.
- If reference code is useful, place external references under `references/`.
- There is already a `references/HEMA` project. Inspect it and learn from it when relevant.
- Do not blindly copy HEMA. Only adapt small useful ideas or modules if they clearly fit the current project.

---

## Step 1: Explore the Current Repository

Start by inspecting the repository structure.

Suggested commands:

```bash
pwd
ls -la
find . -maxdepth 2 -type f | sort | head -200
find . -maxdepth 3 -iname "*readme*" -o -iname "requirements.txt" -o -iname "pyproject.toml" -o -iname "environment.yml" -o -iname ".env.example"
find . -maxdepth 4 -type f | grep -Ei "agent|sim|ep|energy|building|scenario|metric|benchmark|runner|main|demo|test"
```

Please identify:

- the main code directories;
- simulator-related files;
- EP interface files;
- agent-related files;
- scenario/config files;
- logging/output files;
- existing scripts or entry points;
- dependency/configuration files.

Write findings to:

```text
docs/codebase_survey.md
```

Keep it concrete and concise. The purpose is to help Xudong understand the current layout.

---

## Step 2: Try to Run the Current Pipeline Once

Before editing code, try to run the smallest existing demo or pipeline.

Use the repository's existing README or scripts if available.

Record:

- exact command used;
- whether it runs successfully;
- required environment variables or services;
- whether EP state/parameters are passed to the agent;
- whether the agent action is applied back to EP;
- output files or logs generated;
- exact error message if it fails.

Write findings to:

```text
docs/run_log.md
```

If it fails, do not immediately rewrite the system. First identify the minimal blocking issue.

---

## Step 3: Map the Current EP-Agent Loop

Please reconstruct the current control loop from the code.

The expected conceptual loop is:

```text
EP / simulator state
    -> state extraction / parameter parsing
    -> agent receives structured information
    -> agent produces decision / action
    -> action validation / translation
    -> action applied back to EP / simulator
    -> logs / next state
```

For each component, classify it as:

- implemented;
- partially implemented;
- missing;
- unclear.

Write the result to:

```text
docs/control_loop_map.md
```

Use a table like:

| Component | Status | Existing File / Function | Notes |
|---|---|---|---|
| EP state extraction | ... | ... | ... |
| Agent decision logic | ... | ... | ... |
| Action application to EP | ... | ... | ... |
| Scenario/config | ... | ... | ... |
| Logging | ... | ... | ... |
| Metrics | ... | ... | ... |

---

## Step 4: Inspect `references/HEMA`

There is a relatively complete reference project under:

```text
references/HEMA
```

Please inspect it and look for useful design patterns related to:

- building/device state representation;
- user preference modeling;
- grid/VPP/energy event representation;
- strategy/action generation;
- control-loop design;
- logging;
- metric/evaluation design;
- scenario configuration.

Write findings to:

```text
docs/hema_reference_notes.md
```

Please separate:

- directly reusable ideas;
- useful design inspiration;
- irrelevant parts;
- possible small code pieces worth adapting.

Do not copy large parts unless necessary.

---

## Step 5: Identify Benchmark Gaps

After understanding and running the current project, identify what is missing for a benchmark.

Focus on four aspects:

### 1. Scenarios

Check whether the current code supports multiple scenarios.

Important future scenario elements may include:

- building layout;
- thermal zones;
- controllable devices;
- grid/VPP events;
- user preference profiles;
- initial conditions;
- episode length.

### 2. Metrics

Check which metrics can already be computed from current logs.

Potential metrics include:

- energy consumption;
- energy cost;
- peak power;
- comfort violation;
- temperature deviation;
- user preference satisfaction;
- action feasibility;
- safety violation;
- response latency;
- accumulated reward.

### 3. Algorithms / Baselines

Check whether the current code can support multiple agents or control policies.

Possible baselines:

- rule-based agent;
- random agent;
- cost-first agent;
- comfort-first agent;
- LLM-only agent;
- LLM + memory agent;
- LLM + planner/MPC-style agent.

### 4. Evaluation Pipeline

Check whether the current project can run:

```text
for scenario in scenarios:
    for agent in agents:
        run episodes
        log trajectories
        compute metrics
        save comparison results
```

Write findings to:

```text
docs/benchmark_gap_analysis.md
```

---

## Step 6: Propose a Minimal Next Plan

After the survey, running attempt, control-loop mapping, HEMA inspection, and benchmark-gap analysis, propose a minimal next plan.

Do not propose a large rewrite.

The plan should answer:

1. What should be fixed first to make the current simulator run reliably?
2. What existing interfaces should be reused?
3. What is the minimal benchmark abstraction that fits the current code?
4. What is the easiest first baseline?
5. What are the first 2-3 metrics that can be computed with minimal extra logging?
6. What information is still needed from Tiantian or Xuebing?

Write this to:

```text
docs/minimal_next_plan.md
```

---

## Expected Output

At the end of this task, please provide a short summary including:

1. Current repository structure.
2. Whether the current pipeline can run.
3. The current EP-agent loop.
4. Missing components.
5. Useful lessons from HEMA.
6. Minimal next coding plan.
7. Any code changes made.

Again, the priority is:

```text
understand current code -> run once -> document gaps -> propose minimal changes
```

Do not start with a big architectural rewrite.
