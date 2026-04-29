# EnergyBridge Architecture (Stage 1)

## 1. Module Structure

- `energybridge/agent`: workflow state, nodes, LangGraph definition
- `energybridge/skills`: deterministic parsers/translators/strategy/explanation
- `energybridge/control`: mock MPC + safety validation + fallback control
- `energybridge/grid/vpp_1`: boundary adapter from VPP-1 shape to internal schema
- `energybridge/memory`: JSON persistence for short-term memory
- `energybridge/evaluation`: trajectory logging and basic metrics
- `energybridge/llm`: provider-agnostic optional LLM client layer

## 2. Workflow Diagram (Text)

`START`
`-> load_memory`
`-> parse_preference`
`-> translate_grid`
`-> generate_strategy`
`-> user_selects_strategy` 
`-> control`
`-> safety`
`-> actuate`
`-> explanation`
`-> memory_update`
`-> logging`
`-> END`

The CLI layer handles user input and strategy selection before graph execution continues through control, safety, actuation, memory update, and logging.

## 3. Deterministic Modules

- Preference parser: keyword-based weights and flags
- Grid translator: signal normalization + intent inference
- Strategy generator: bounded rule policy
- Mock MPC: deterministic control estimate
- Safety checker: strict, non-LLM rule checks
- Mock actuator: local stand-in for downstream electrical execution

## 4. Replaceable Modules

- `skills/preference_parser.py`: can be replaced with LLM parser later
- `control/mock_mpc.py`: can be replaced with real MPC
- `memory/store.py`: can move from JSON to DB/vector memory
- `grid/vpp_1/adapter.py`: can expand to production VPP protocol parser

## 5. Future Integration Plan

- Introduce optional LLM-assisted strategy modules via `energybridge/llm`
- Add richer VPP-1 adapters and schema validation
- Add real-time controller interfaces and safety envelope checks
- Expose loop via API service after stage-1 stabilization
