# Adaptive harness architecture

EnergyBridge's public harness profile is still `adaptive_v2`. Within that
profile, the EnergyBridge controller uses V3 component schemas for household
modelling, memory, and planning. The V3 label describes internal data contracts;
it is not a new benchmark protocol and does not change the frozen paper profile.

`paper_v1` and `legacy_v1` remain frozen compatibility paths. They do not
silently opt into the components described here.

## Decision and learning loop

```text
observable onboarding / calendar / devices / feedback
                         |
                         v
             evidence-backed household profile
                         |
event + measured state -> stage-aware causal memory retrieval
                         |
                         v
                open portfolio planning
                         |
                         v
             common hard-constraint validator
                         |
                         v
       role-play consent -> actuator-facing execution -> measured outcome
                |                    |                    |
                +--------------------+--------------------+
                                     |
                          profile and memory update
```

The boundary is intentionally observable. Controller inputs may include answers
given during onboarding, calendars and device capabilities shared with the
agent, direct feedback, the event specification, home measurements, and past
executed outcomes. Hidden persona fields, evaluator state, acceptance targets,
credentials, and model or method identity are not profile or memory evidence.

### Evidence-backed profile

The household profile keeps preferences as uncertain beliefs rather than a
fixed archetype. Each belief carries confidence and provenance. Conflicting
evidence remains visible, while unknowns become questions that the agent can
resolve through ordinary interaction. A bounded profile capsule presents only
evidence relevant to the current decision; it is context, not a prescribed
action recipe.

Schema: `energybridge.observable_household_model.v3`.

### Stage-aware causal memory

An event episode records distinct stages:

```text
raw proposal -> validated plan -> consent decision -> executed plan -> outcome
```

This distinction prevents a rejected proposal, validator edit, or unexecuted
schedule from being credited as physical performance. Stable and contextual
beliefs retain provenance, contradictions, confidence, and time decay. Retrieval
uses the current event context and returns a bounded evidence capsule instead
of replaying the complete household history.

Schema: `energybridge_evidence_memory_v3`.

### Open portfolio planning

The base model owns both the candidate set and the selection. It may express
multiple candidates when genuine trade-offs exist, but the prompt does not
require a fixed number or canned categories. A legacy single plan remains a
valid response. Anonymous advisor outputs can be presented as evidence; they
never replace an invalid or missing model selection silently.

Every candidate passes through the same executable constraints. Feasible
trade-offs are compared without a method-specific scalar bonus, and validator
edits are preserved as JSON Patch audit records. An invalid selection requests
one semantic replan before the normal safe fallback path.
Evidence and constraint references use JSON Pointers, avoiding the ambiguity
between dotted field paths and private DNS names at the model boundary.

Schema: `energybridge.open_portfolio_planning.v3`.

### Consent, execution, and outcome

The role-play LLM remains active in `adaptive_v2`. It judges the concrete plan
and its event-specific explanation using observable household context. A
household prior may anchor that judgement, but the controller prompt contains
no desired acceptance probability or score. Acceptance and user rating are
evaluation outcomes, not planner objectives.

Only actuator-facing controls are recorded as executed. When a VPP episode has
pre-event dispatches and later replans, their ordered execution exposures are
kept together; later comfort, energy, cost, and feedback observations are
attributed to that exposure sequence rather than only the last proposal. This
closes the loop without treating an explanation, rejected plan, or unconfirmed
actuator command as evidence that an action occurred.

Consent is also treated as a commitment. A feasible pre-event plan that the
household accepted is retained at event start instead of being silently
replaced by a fresh model proposal. Replanning and renewed consent occur only
when current hard constraints make that commitment infeasible.

## Prompt and identity boundaries

Adaptive prompts describe capabilities, observable facts, uncertainty, and
hard constraints. They do not encode:

- EnergyBridge, HEMA, MPC, or RL acceptance-rate targets;
- bonuses or penalties based on method, provider, or model identity;
- a fixed user score or evaluator scoring weights;
- a mandatory candidate count or mechanical conservative/flexible grid; or
- hidden role-card traits as controller knowledge.

These boundaries leave room for different base models to form different
portfolios, explanations, and selections. Hard physical and safety constraints
remain deterministic because model diversity must not bypass executability.

## Memory operating modes

| Mode | Configuration | Behaviour |
|---|---|---|
| Cold start | `ENERGYBRIDGE_AGENT_MEMORY_STORE=` and `ENERGYBRIDGE_LOAD_AGENT_MEMORY=0` | Start from observable onboarding and shared context only. Use this mode for the default independent benchmark comparison. |
| Within-run learning | Always available to the adaptive EnergyBridge agent | Accumulate stage-linked feedback and outcomes during the current run; no cross-run load is required. |
| Explicit cross-run memory | Set `ENERGYBRIDGE_AGENT_MEMORY_STORE` to a private file or directory and `ENERGYBRIDGE_LOAD_AGENT_MEMORY=1` | Load a matching household memory, continue updating it, and save through the explicit store. Report this as a warm-start experiment. |

`ENERGYBRIDGE_PERSIST_AGENT_MEMORY=1` controls human-readable review artifacts
inside an owner-only `.adaptive_harness_private/` child of an individual run
output. It is separate from the cross-run store.

Persistent memory can contain household observations. Keep its location private
and outside version control, restrict filesystem access, and apply the same
retention policy as other participant data. The persistence envelope is
integrity checked and written with owner-only permissions where supported, but
it is not encrypted storage. The run manifest records only the component schema
IDs and whether warm start was enabled. It never records the memory location,
contents, or a digest of those contents.

Because that private state is intentionally absent from the fingerprint,
warm-start results are never reused by `--resume`; each warm-start job executes
against the explicitly selected store. Cold-start runs retain exact-manifest
resume support.

## Comparing EnergyBridge with HEMA

This architecture creates testable structural differences; it is not by itself
a performance claim. Under the same controller base model, model parameters,
household and event inputs, tariff, simulator configuration, and consent model,
measure at least:

- feasible-candidate and semantic-replan rates;
- portfolio trade-off coverage and model-selection diversity;
- profile and memory evidence-citation validity;
- proposal-to-validation and execution-fidelity gaps;
- calibration of predicted versus observed comfort, energy, and cost;
- acceptance, user rating, VPP energy, total energy, and price-weighted cost.

Run cold-start comparisons separately from warm-start studies. Report model and
seed variation rather than tuning prompts to reproduce a target acceptance
rate. EnergyBridge surpasses HEMA only if these controlled measurements support
that conclusion.
