# Adaptive harness architecture

EnergyBridge's current public harness name is `agentic_v3`. It is the default
for unconfigured Python entry points and launch wrappers, and is internally
canonicalized to the existing `adaptive_v2` result profile so prior V2/V3 result
manifests remain comparable. The controller uses V3 component schemas for
household modelling, memory, and planning.

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
      common validator + physical/tariff evidence
                         |
                         v
                model evidence deliberation
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

The capsule reserves bounded space for the highest-priority unresolved
household question without crowding out observed evidence. During a decision,
the base model may ask one natural clarification only when plausible answers
could materially change its plan. It may also proceed directly with a safe,
reversible plan. The harness does not rank questions, reward asking, or choose
an action from the answer. The role-play boundary returns only the household's
natural reply and a coarse certainty label; hidden resume content, prompts, and
raw provider output never become controller evidence. A valid reply is both
current-event evidence and a provenance-carrying update to the household model.

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

Optional portfolio `information_requests` are preserved in a separate
information-value audit. The audit records whether a request cited a supplied
unknown and explained how the answer affects the decision, but assigns no
score, target, or preferred question count and never changes the model's plan.

A model may also return a conservative executable selection together with a
deferred question. When independent evidence later establishes that the
selection is physically identical to the ordinary plan, that another feasible
model candidate has a supported incremental benefit, and that the model's
question cites a supplied unknown, the harness may ask that first question
once. The same base model then reconsiders its own portfolio with the direct
reply and the prior evidence cards. The harness neither authors the question
nor selects the material alternative; failure of this optional interaction
retains the original valid plan. This lets cautious models resolve genuine
household uncertainty without turning clarification into a mandatory recipe.

After a model proposes a portfolio, a method-blind accounting tool evaluates
each candidate independently. It can integrate fixed-power appliance schedules
against the visible hourly tariff, measure half-open event-window overlap,
check declared service windows and EV energy feasibility, and report the
physical direction of an HVAC setpoint change. It labels water-heater and EV
quantities as bounds when duty cycle or state trajectory is unknown, and does
not invent whole-home or HVAC kWh. The tool neither scalarizes objectives nor
selects a plan. The same base model sees the resulting evidence cards and owns
the confirm-or-revise decision.

The confirm-or-revise call receives a compact review projection rather than a
second copy of the verbose audit tree. Every model candidate and every checked
device/HVAC dimension remains present in columnar form, along with service
risks, event overlap, cost comparisons, uncertainty, supported offer claims,
and limitations. Repeated evidence-path strings move out of the model-visible
copy but remain intact in the lifecycle audit. This is context budgeting, not
candidate pruning, scoring, or a harness-authored choice.

Within one run, an exact review request may reuse its already validated,
privacy-projected model deliberation. The cache key covers the event stage,
model-visible planning context, anonymous advisor evidence, original portfolio,
and checked impact capsule. Any changed plan, profile/memory context, state, or
evidence therefore invokes the base model again; the audit records cache hit or
miss and whether a provider call occurred. This removes duplicate deliberation
without treating a merely similar decision as equivalent.

At event start, a separate state-physical capacity estimator combines current
device state, deadlines, thermal comfort headroom, measured facility power,
device reliability, and an explicit safety margin. When the external A3
reference forecast is unavailable, the VPP demand context is derived from this
observable household bid envelope rather than a fixed zero-shed fallback. The
result retains its point-in-time baseline basis and uncertainty fields; an A3
forecast, when present, remains authoritative. This is a traditional
deliverability calculation, not a planner reward or a method-specific bonus.

Schema: `energybridge.open_portfolio_planning.v3`.
Impact evidence schema: `energybridge.candidate_impact.v3`.
Impact review schema: `energybridge.impact_review_capsule.v1`.
Flexible-load opportunity schema: `energybridge.flexible_load_opportunities.v1`.
Decision-epoch schema: `energybridge.decision_epochs.v1`.

Decision-evidence schema: `energybridge.decision_evidence_ledger.v1`. The
ledger distinguishes a direct household statement made for the current event
from inferred profile beliefs and older observations. A current statement
governs the same topic for that event, while its conditions still require
evidence. It copies no free text, chooses no action, assigns no score, and
predicts no acceptance probability; it prevents uncertain historical traits
from silently overriding what the household just said without narrowing the
base model's planning space.

Before the base model chooses a portfolio, a method-blind appliance scheduling
primitive enumerates tariff-relevant feasible starts inside each declared
service window. It exposes separate cost, VPP-overlap, and routine-deviation
dimensions plus the ordinary-plan comparison. It does not combine them into a
score, recommend a start, or select a plan. The base model can therefore use
traditional scheduling arithmetic without being collapsed into a fixed MPC or
rule policy, and different models can still make different household tradeoffs.
The model-visible version uses a lossless columnar option capsule: every start
and every dimension remains available in its original order, while repeated
JSON field names are emitted once. This reduces context cost without pruning
alternatives or changing their trade-offs.

A companion event-triggered control primitive enumerates future times when new
observable evidence can appear: VPP boundaries, tariff transitions, device
availability, ordinary service starts/finishes, and declared service
deadlines. These epochs are unranked. The model remains free to choose any
future `next_check_hour` or `null`; the harness neither inserts a fixed cadence
nor forces another model call. This gives receding-horizon planning a physical
time structure without encoding one preferred policy.

Adaptive controller and role-play calls that require an object use the
provider's JSON-object response mode when available, followed by the existing
semantic validator. This is a transport constraint only: it does not prescribe
candidate count, objectives, adjustment values, or the selected plan. If an
OpenAI-compatible endpoint explicitly reports that response mode is
unsupported, the client retries the identical prompt without that transport
option and records the downgrade. Run telemetry separates provider failures,
empty responses, JSON/schema validation failures, and successful structured
calls so protocol reliability can be evaluated independently of strategy
quality.

### Consent, execution, and outcome

The role-play LLM remains active in the current harness. It judges the concrete plan
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

The selected candidate's pre-action impact card is stored with the attributed
outcome in episodic memory. Future retrieval therefore links an observable
tradeoff claim, the actual actuator exposure, and the household response. This
supports calibration from evidence without converting one score into a fixed
household rule.

The episode also keeps a bounded decision record containing the base model's
feasible alternatives, its chosen candidate, its own selection reason,
uncertainties, and counterfactual conditions. Anonymous advisor candidates are
not recast as model choices, and the record contains no harness-generated
ranking. On a later similar event, the model can therefore recall not only what
happened but which genuine alternative it declined and what new fact could have
changed its decision. The prompt projection keeps at most a small relevant
subset under the normal memory character budget.

Pre-event willingness and post-event satisfaction are stored as two distinct
observations. A narrow attitude-transition record carries the continuous
willingness, the independently authored post-event rating, their signed
difference, and the newly observed comfort/service/VPP evidence. It is written
only for a live role-play judgement, never for a fallback prior. This allows a
household to become more or less satisfied after seeing execution while making
that change of mind auditable; no formula remaps either judgement.

`energybridge.outcome_calibration.v1` compares that bound forecast with later
physical appliance, service, and comfort measurements only when execution was
observed and causally attributed. Relevant records are summarized as
`energybridge.calibration_capsule.v1`. The capsule reports agreement and
disagreement by signal plus evidence paths; it deliberately contains no
learned controller weight, scalar reward, method rank, or recommended action.
The base model remains responsible for interpreting how much confidence those
observations warrant in the current context.

If one event contains several distinct actuator-bound forecasts, calibration
is performed per signal rather than assigned to an arbitrary final plan. A
signal is retained only when every bound forecast supplies the same prediction;
forecast disagreement or missing coverage remains explicit and uncalibrated.
This preserves useful invariant evidence without claiming causal credit for a
single member of a multi-dispatch sequence.

Consent is also treated as a commitment. A feasible pre-event plan that the
household accepted is retained at event start instead of being silently
replaced by a fresh model proposal. Replanning and renewed consent occur only
when current hard constraints make that commitment infeasible.

When a same-day event is already observable, consent is requested during the
day-ahead decision before any event-specific appliance or HVAC action can run.
The comparison plan is a separately constructed, event-free ordinary routine
derived from visible device settings and current controls. It is never the
controller's already-optimized offer relabelled as a default. A rejection thus
restores the genuine ordinary routine, while an acceptance creates the
commitment used by subsequent pre-event and event-start decisions.

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
