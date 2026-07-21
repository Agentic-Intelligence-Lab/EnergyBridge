# Strategy Presentation Protocol

This protocol controls how real controller outputs are converted into participant-facing survey cases.

Do not show method labels to participants in the main study. Method labels remain in the researcher-side JSON/CSV only.

## Included Methods

The active single-person Tianjin pilot includes methods with complete six-persona source data by default:

- EnergyBridge
- HEMA
- MPC
- Rule+MILP
- RL

Missing method/persona/city combinations are not fabricated; they are recorded in `generation_summary.json`.

## Participant-Facing Strategy Text

All methods are converted from real benchmark outputs into plain, human-readable strategy text. When `vpp_acceptance_gate.proposed_plan` exists, it is used directly. For older result files without a gate, the builder reconstructs the visible strategy from the real event setpoint, selected strategy, day decisions, and VPP trigger actions. The survey strategy must contain:

- VPP event window
- AC target or AC change
- affected appliance services
- visible risk notes, such as appliance conflict or skipped service
- the real generated explanation summary when available, or a plain translation of the controller reason/action when no user-facing explanation exists

The survey should not introduce hand-written strategy actions that were not present in the benchmark output. Human edits are limited to translation, shortening, and replacing simulator-specific phrasing with equivalent user-facing wording.

## Explanation Treatment

EnergyBridge cases use `personalized_explanation`.

These cases may include a concise summary of the real generated user-facing explanation. This is part of the EB agent capability being evaluated: EB is expected to make strategy recommendations more acceptable by explaining how the plan fits the user.

HEMA cases use `generic_agent_explanation`.

These cases include a concise summary of the real generated HEMA explanation when present. It is treated as a generic agent explanation for analysis.

MPC, Rule+MILP, and RL cases use `plain_strategy_translation`.

These cases only translate the computed policy and controller reason into natural language. The survey should not add personalized persuasive explanations for these methods, because that would give traditional baselines an agent capability they do not have.

## Research Use

The researcher-side case table records:

- `source_method`
- `source_method_label`
- `strategy_presentation_style`

Participants should see only the anonymized case ID, role card, event, strategy text, and outcome branch.

The active short-form questionnaire asks five required items per case. It does not ask participants to copy the baseline probability or separately report an adjustment direction; both are already known or derivable. This reduces burden while preserving all variables needed for human-vs-role-play calibration.
