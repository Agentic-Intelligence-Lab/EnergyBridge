# Population-Grounded Personas And Human Role-play Calibration

## Study Overview

The human study has two connected but distinct stages:

1. **Population grounding:** real users answer the preference questionnaire as themselves. The response distribution is used to estimate the prevalence of different preference patterns, determine persona proportions in household or large-scale experiments, and refine the persona prompts.
2. **Role-play calibration:** participants enact the resulting persona cards and evaluate real VPP strategies. Their human-role-play responses are used to measure and calibrate the gap in LLM role-play.

The first stage answers **which user preferences and persona proportions should be represented**. The second stage answers **whether the LLM can reason and score like a human who is enacting the same persona**.

## Stage 1: Population Preference Grounding

Participants first complete the four-item EnergyBridge preference questionnaire according to their own household preferences. The questionnaire measures four dimensions that directly affect VPP participation:

- priority among comfort, cost, grid support, balance, and confirmation;
- acceptable thermostat flexibility;
- appliance-shifting consent;
- calendar and routine constraints.

The optional free-text constraint records important preferences not covered by the fixed choices.

These responses have two primary uses.

### Estimating Persona Proportions

Each response is encoded as a preference vector. Similar response patterns can be grouped or mapped to the six seed personas. The resulting empirical proportion for persona `k` is:

```text
pi_k = number of respondents mapped to persona k / total valid respondents
```

The proportions `pi_k` can then determine:

- the mix of persona members used when constructing household samples;
- the sampling frequency of each persona in larger experiments;
- population-weighted aggregate results.

For transparency, the benchmark may report two views:

- **balanced persona benchmark:** each persona has equal weight, which exposes method behavior on rare or extreme users;
- **population-grounded benchmark:** results are weighted by the questionnaire-derived `pi_k`, which better approximates the sampled population.

The six personas should not be described as equally common unless the survey supports that assumption. Extreme personas may still be intentionally oversampled for stress testing, but this should be reported separately from population-weighted results.

### Refining Persona Prompts

The questionnaire also grounds persona construction. For each response group, researchers summarize:

- the modal answer on each preference dimension;
- common combinations of comfort, cost, automation, and calendar constraints;
- representative free-text concerns;
- within-group variation and disagreement.

These summaries are used to revise the persona JSON and role-play prompt. The prompt should express the group's characteristic trade-offs and constraints in natural language rather than merely inserting a persona label. Existing six personas can be treated as seed archetypes whose descriptions, boundaries, and population weights are updated from the survey.

The prompt-construction procedure should be documented and frozen before the final benchmark. Individual respondents should not be copied verbatim into a persona prompt, and rare free-text statements should not be presented as group-level preferences without supporting responses.

## Stage 2: Human Role-play Calibration

After personas have been grounded, human participants are asked to enact the fixed role cards. They do not answer according to their own household preferences in this stage.

The main research question is:

> Given semantically aligned persona facts and the same VPP event, strategy, and outcome, how different are LLM role-play and human role-play in acceptance, probability, reasoning, and satisfaction?

EnergyBridge uses LLM role-play because it can generate preference feedback at benchmark scale. Human role-play is more expensive, but it provides a valuable reference for checking whether the LLM follows the persona in a human-plausible way.

The LLM and participant receive the same underlying persona constraints, although the presentation differs: the LLM receives machine-readable persona context and role-play instructions, while the participant receives a concise human-readable role card derived from the same persona. Both evaluate the same generated strategy and corresponding outcome.

## Human Role-play Assumption

We assume that a participant who understands and consistently enacts a concrete persona can provide a useful approximation of how the represented user would respond in a real VPP interaction. This makes it possible to study deliberately distinct and sometimes extreme preference types without recruiting a large number of people who naturally match every type.

Human role-play is therefore treated as a **reference proxy**, not literal behavioral ground truth. It is expected to approximate the direction, relative strength, and reasoning behind a user's response. It does not replace longitudinal field deployment or prove that a real household would make the same decision.

With multiple human role-players, human-human agreement should be measured before interpreting LLM-human disagreement. Low human agreement may indicate an ambiguous persona or scenario rather than an LLM error.

## Minimal Tianjin Role-play Study

- Six fixed single-person role cards grounded in the preference dimensions.
- Five anonymized real strategies per role: EnergyBridge, HEMA, MPC, Rule+MILP, and RL.
- One Tianjin VPP context, giving 30 short cases per participant.
- Method order randomized within each role.
- Method names and role-play reference scores hidden from participants.
- Acceptance judged before the outcome is revealed.
- Only the outcome branch matching the participant's accept/reject choice is shown.

Each case collects:

1. final acceptance probability and binary accept/reject choice;
2. up to three decision factors and a short reason;
3. explanation helpfulness from 1-5;
4. outcome satisfaction from 1-5;
5. short outcome feedback and one requested improvement when needed.

Role-play confidence is collected once after each role block.

## Calibration Targets

| LLM-vs-human role-play gap | Human-role-play signal | Main analysis |
| --- | --- | --- |
| Probability bias | Acceptance probability | MAE, Brier score, calibration curve, ECE |
| Decision bias | Accept/reject | Agreement, F1, disagreement by persona and method |
| Reasoning bias | Selected factors and short reason | Factor overlap and coded primary-reason agreement |
| Score bias | Outcome satisfaction | MAE and rank correlation |
| Explanation effect | Explanation helpfulness | Blinded comparison across EB, HEMA, and translated baselines |

A low-capacity calibration model can map LLM-role-play probability to human-role-play probability. Logistic or isotonic calibration is suitable for a small dataset. Calibration and evaluation must use participant-level separation or cross-validation, and both raw and calibrated LLM outputs should be reported.

## Combining The Two Stages

For method `m`, a population-grounded expected outcome can be estimated as:

```text
Expected outcome(m) = sum_k pi_k * Outcome(m | persona k)
```

Here, `pi_k` comes from the real-user preference questionnaire, while `Outcome(m | persona k)` comes from human role-play or calibrated LLM role-play under persona `k`.

This decomposition is central to the paper story:

- real-user questionnaires ground **who is represented and in what proportion**;
- human role-play provides limited but valuable **conditional preference feedback**;
- calibrated LLM role-play scales that feedback to the full benchmark.

## Future Training

Human-role-play records directly support supervised examples of:

```text
persona + event + strategy -> accept choice + probability + reason
persona + selected outcome -> satisfaction + feedback
```

Strategies evaluated under the same persona and event can also form pairwise preferences. These data could later support supervised fine-tuning, reward modeling, or preference optimization. The current paper should distinguish post-hoc calibration from future model training.

## Writing Boundaries

- Do not call human role-play `real-user ground truth`; call it a human-role-play reference or proxy.
- Do not claim that the six personas are equally prevalent unless the preference survey shows this.
- Report balanced and population-weighted results separately when extreme personas are deliberately oversampled.
- A single human role-player is sufficient for a pipeline pilot or qualitative case analysis, but stronger calibration claims require multiple participants and uncertainty intervals.
- Population generalization depends on how the preference-survey sample was recruited; convenience-sample proportions should not be presented as universal population proportions.
