# EnergyBridge

EnergyBridge is a home-grid coordination agent framework that connects user
preferences, grid/VPP signals, home state, control decisions, safety checks,
execution, and memory logging.

LLM backend: OpenAI-compatible API (configured via `.env`).

Reference-derived DR capacity quantification and the independent Typical Human
RL baseline are documented in `REFERENCE_CAPACITY_RL_INTEGRATION.md`.
For comparable three-day PPO training, continuation, metrics, known issues, and
handoff guidance, see `baselines/rl_energyplus_3day/README.md`.

---

## Setup

### 1. Prerequisites

| Requirement | Version |
|-------------|---------|
| Python | 3.10+ |
| EnergyPlus | 24.1.0 (default path: `/home/hku_user/EnergyPlus-24-1-0`; change `EPLUS_ROOT` in `experiments/benchmark/family_runner.py` if needed) |
| conda env | `energybridge` |

```bash
conda activate energybridge
pip install -r requirements.txt
```

### 2. API key configuration

```bash
cp .env.example .env   # then fill in your keys
```

`.env` format (OpenAI-compatible):

```ini
LLM_MODEL=gpt-4o-mini
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-xxxx
# Optional: multiple keys rotated automatically on failure
LLM_API_KEY_POOL=sk-key1,sk-key2,sk-key3
```

---

## Benchmark 1 — Family Home Persona Evaluation

Co-simulates a 3-day residential building (Tianjin, July) with 3 VPP
demand-response events (daily 18:00-19:00). An LLM agent controls the
thermostat on behalf of a persona-defined user.

### Persona files

`energybridge/roleplay/personas/*.json` — 10 archetypes across 6 behavioral
dimensions (comfort, cost, control, flexibility, trust, ecology).

See `energybridge/roleplay/personas/README.md` for schema and field definitions.

### Run a single persona

```bash
cd experiments/benchmark

# By persona ID
python3 run_persona_json.py atom_comfort_sensitive

# All options
python3 run_persona_json.py atom_comfort_sensitive --city Tianjin --output /tmp/out

# Available persona IDs:
#   atom_comfort_sensitive, atom_control_auto, atom_price_indifferent, atom_task_rigid
#   basic_role_a_commuter_price_cooperative, basic_role_b_home_comfort_gated
#   basic_role_c_irregular_cautious, basic_role_d_commuter_ideal_dr
#   basic_role_e_caregiver_low_dr, basic_role_f_commuter_ev_optimizer
```

### Run a multi-persona household (multi-agent mode)

Multiple personas become household members who **discuss** VPP strategy before
each event and **score** the outcome together afterward. The LLM-synthesized
consensus is injected as the AC agent's `user_pref` for that event.

```bash
cd experiments/benchmark

# Two household members
python3 run_multi_persona_json.py basic_role_a_commuter_price_cooperative \
                                   basic_role_b_home_comfort_gated --city Tianjin

# Three members
python3 run_multi_persona_json.py basic_role_a_commuter_price_cooperative \
                                   basic_role_b_home_comfort_gated \
                                   basic_role_c_irregular_cautious

# With explicit output directory
python3 run_multi_persona_json.py persona_a persona_b --city Tianjin --output /tmp/household_run

# Verbose (prints full LLM dialogue)
python3 run_multi_persona_json.py persona_a persona_b --verbose
```

Discussion structure per VPP event: 2 rounds × N members → LLM synthesis →
consensus preference string → injected to building agent.  
When N = 1 this degrades naturally to a single-user instruction (the one
member's opinion becomes the consensus directly).

### Run all 10 personas (batch)

```bash
cd experiments/benchmark
python3 run_all_personas.py

# Options
python3 run_all_personas.py --results-dir /path/to/output --city Tianjin
python3 run_all_personas.py --no-skip       # re-run even if log exists
```

The batch script resumes automatically: skips personas whose log already
contains `[family/agent]`.

---

## Output & Run Summary

> **All run results are saved to `benchmark_results/` at the project root.**
> The most important file in every run directory is **`run_summary.txt`** —
> a human-readable digest that does not require any tools to read.

### Single-persona run output

```
benchmark_results/<persona_id>/
├── run_summary.txt          ← ★ human-readable summary (always check this first)
├── benchmark_result.json    ← raw metrics in JSON
└── <eplus files>            ← EnergyPlus simulation outputs
```

### Multi-persona household run output

```
benchmark_results/multi__<id_a>__<id_b>/
├── run_summary.txt          ← ★ full dialogue + strategy + scores per VPP event
├── benchmark_result.json    ← raw metrics
└── household_meta.json      ← member profiles + complete discussion transcripts
```

### What `run_summary.txt` contains

```
══════════════════════════════════════════════════════════════
  EnergyBridge 运行摘要  (run_summary.txt)
══════════════════════════════════════════════════════════════
  用户档案   : Name · 舒适30% 节能40% VPP30%
  本户电器   : ✓ 洗衣机 | 热水器   ✗ 未配置: 烘干机 | EV充电桩
──────────────────────────────────────────────────────────────
  [事件1] Day1 18:00-19:00  目标：需求侧削峰1小时

  ┌─ 策略讨论 (2轮 · 2人) ──────────────────────────────────
  │  [初始意见]
  │    [成员A]  各自发言（自动换行）
  │    [成员B]  ...
  │  [第2轮]   收敛后意见
  └→ 共识偏好: For the 18:00–19:00 VPP event, keep AC ...  ← 注入 AC agent

    执行策略 ↓  AC 设定点 + 家电排程
    VPP需求    : 目标 ≤ 2.00 kWh  实际 0.68 kWh  比率 0.34 ✓达标
    Agent理由  : ...

  ┌─ 满意度讨论 (2轮 · 2人) ───────────────────────────────
  └→ 共识评分: 4.0/5 — 舒适度基本无影响
──────────────────────────────────────────────────────────────
  需求达成比率  : vpp1:1.30✗  vpp2:0.34✓  vpp3:1.01✗  (总体0.83)
  共识满意度均值: 4.17/5  [事件1:4.0  事件2:4.0  事件3:4.5]
══════════════════════════════════════════════════════════════
```

### Result metrics

| Field | Meaning |
|-------|---------|
| `pmv_ok_fraction` | Fraction of occupied hours in PMV comfort range [-0.5, +0.5] |
| `vpp_compliance_rate` | Fraction of 3 VPP events where agent set >= 26 C |
| `user_pref_score` | LLM-evaluated user satisfaction (1-5), averaged over events |
| `energy_kwh_total` | Total electricity consumption over 3 days |
| `llm_call_failures` | LLM API errors (0 = clean run) |

---

## VPP Demand-Response Agent

### How the building agent gives peak-shaving decisions

Each VPP demand-response window (18:00–19:00 daily) involves **two separate
LLM agents** with no shared context:

```
Grid side                       Building side
─────────────────────           ─────────────────────────────────────────
VPP Demand Agent          →     AC Thermostat Agent
  • Role: grid coordinator        • Role: household comfort manager
  • Input: household's past        • Input: user preference (or household
    VPP window kWh history           consensus from discussion pool)
  • Output: energy cap (kWh)       • Input: VPP demand target (kWh)
    for the next 1-hour window     • Output: per-timestep setpoint (°C)
                                     + appliance schedule commands
```

**Grid-side VPP Demand Agent** (`_call_vpp_demand_agent` in `family_runner.py`):

1. Receives the household's historical VPP-window energy consumption list.
2. For the **first event** (no history): issues a baseline target of **2.0 kWh**.
3. For **subsequent events**: sets target = historical average — letting the
   agent track the household's actual baseline without artificial reduction.
4. Returns `{"target_kwh": <float>, "reason": "<brief>"}` as JSON.
5. Falls back to a rule-based value if the LLM call fails.

**EnergyBridge Agent** (`_FamilyLoop` in `family_runner.py`):

1. Receives the VPP demand target and the user preference string (single-user)
   or the household consensus string (multi-agent mode).
2. Makes an LLM decision every simulation timestep during the VPP window:
   - Setpoint strategy: pre-cool before the event, then raise setpoint during
     the window (configurable range in system prompt).
   - Appliance scheduling: defer or skip shiftable loads (washer, dishwasher,
     dryer) to keep in-window consumption below the cap.
3. The agent's reasoning is stored in `reason` field of `vpp_event_log`.

**Multi-agent household discussion** (`multi_agent_pool.py`):

1. Before each VPP event, all household members (N personas) discuss in
   **2 rounds** × N speakers.
2. A synthesis LLM call converts the dialogue into a single English preference
   string and injects it as the AC agent's `user_pref`.
3. After the event, members discuss satisfaction and vote on a consensus score.
4. Full transcripts are saved in `household_meta.json` and displayed in
   `run_summary.txt`.

---

## Benchmark 2 — Office Building PMV Baseline

Runs a 15-zone medium office building with PMV or agent-based control.

```bash
cd experiments/benchmark

# PMV rule-based control (default)
python3 office_runner.py --mode pmv --city tianjin

# LLM agent control
python3 office_runner.py --mode agent --city tianjin

# Available cities: tianjin, beijing, shanghai
```

---

## Benchmark 3 — Full Comparative Suite (3 cities x 2 buildings x methods)

Runs 12+ EnergyPlus simulations: family + office x PMV/agent x 3 cities.
Results are saved to `experiments/benchmark/results/`.

```bash
cd experiments/benchmark

# Full run
bash reproduce_benchmark.sh

# Resume interrupted run (skips completed scenarios)
bash reproduce_benchmark.sh --resume

# Single scenario
python3 run_benchmark.py --scenario family/tianjin/pmv

# Family building only
python3 run_benchmark.py --building family --skip-existing
```

---

## Interactive Agent Demo

### Human-in-the-loop mode (`run_agent_loop.py`)

Runs the **identical** 3-day EnergyPlus + VPP co-simulation as
`run_persona_json.py`, but replaces the LLM-simulated user with **real human
terminal input**.

```bash
cd /path/to/EnergyBridge
conda activate energybridge
python3 examples/run_agent_loop.py
python3 examples/run_agent_loop.py --city Tianjin
python3 examples/run_agent_loop.py --output /tmp/my_run
```

Before each VPP event (18:00-19:00, Days 1–3) you will see:

```
  ┌─[Strategy Candidates | VPP event 1]──────────────────────────
  │  [A] 舒适优先  —  保持设定点25°C，接受较高电耗  (舒适度最高，电耗偏多)
  │  [B] 平衡策略  —  升温至26°C，家电提前完成或延后  (轻微温漂，节电约15%)
  │  [C] 节能优先  —  升温至27°C，所有可平移家电延迟  (明显温漂，节电约30%)
  └──────────────────────────────────────────────────────────────
  > A          ← type A / B / C, free text, or Enter for auto
```

After each event ends you rate your satisfaction (1–5) and leave a comment.

Output goes to `benchmark_results/human_<YYYYMMDD_HHMMSS>/` — same layout as
single-persona runs (`run_summary.txt` + `benchmark_result.json`).

| Mode | User input | EnergyPlus | VPP agent |
|------|-----------|-----------|-----------|
| `run_persona_json.py` | LLM roleplay | ✓ | ✓ |
| `run_multi_persona_json.py` | LLM household discussion | ✓ | ✓ |
| `run_agent_loop.py` | **Real human** | ✓ | ✓ |

### Automated roleplay evaluation (`run_roleplay_evaluation.py`)

Runs a **fully automated** multi-turn evaluation loop (no EnergyPlus, no VPP)
to test the agent's learning behavior across turns.  No human input needed.

```bash
cd /path/to/EnergyBridge
conda activate energybridge
python examples/run_roleplay_evaluation.py --turns 5
```

---

## Project Layout

```
EnergyBridge/
├── energybridge/          <- core Python package
│   ├── agent/             <- LangGraph agent (graph, nodes, state)
│   ├── llm/               <- LLM client with key-pool rotation
│   ├── roleplay/          <- persona schema, loader, 10 JSON personas
│   └── simulation/        <- appliance simulation helpers
├── experiments/
│   └── benchmark/         <- family_runner.py, office_runner.py, run_benchmark.py
│       ├── run_persona_json.py    <- single-persona CLI
│       ├── run_all_personas.py    <- batch all-persona CLI
│       ├── reproduce_benchmark.sh <- full 3-city benchmark
│       ├── models/        <- IDF building models
│       └── weather/       <- EPW weather files
├── .env.example           <- API key template
└── requirements.txt
```

---

## Reference

EnergyBridge is an independent implementation; all code in `energybridge/` is
original unless otherwise noted.
