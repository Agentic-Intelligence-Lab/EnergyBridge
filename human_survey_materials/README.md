# EnergyBridge Human Survey Materials

Created: 2026-07-20

This folder stores human role-play study materials for calibrating and auditing EnergyBridge LLM role-play preference modeling.

Current active pilot design: use single-person cases, not household cases. One volunteer can role-play the six basic personas under the Tianjin city/weather context and score the generated VPP strategies. This keeps the task close to the original single-user role-play benchmark while avoiding the cognitive load of multi-member household voting.

The compact paper-facing design, population grounding logic, and calibration analyses are summarized in `PAPER_CALIBRATION_STUDY.md`.

## Study Goals

The study has two connected tasks:

1. Population preference questionnaire

Before role-play, participants answer the same four core multiple-choice items used in EnergyBridge onboarding, plus one optional overall constraint note. These answers are used to estimate the distribution of preference patterns in the study population, support persona proportions in later household or large-scale experiments, and refine persona prompts. They can also be used as a covariate for checking whether a participant's own preferences leak into judgments made under an assigned role card.

2. Human role-card VPP decision and outcome feedback

Participants receive a fixed human-readable role card derived from the same persona JSON used by the LLM, then read the same VPP event and generated strategy. Each case has five required responses: acceptance probability and binary choice, up to three decision factors with a short reason, explanation helpfulness, outcome satisfaction, and short outcome feedback. These responses are compared with LLM role-play responses under semantically aligned role information.

## Folder Layout

```text
human_survey_materials/
  questionnaires/
    persona_onboarding_zh.md
    persona_onboarding_en.md
    onboarding_questionnaire_schema.json
    strategy_presentation_protocol.md
    roleplay_vpp_feedback_zh.md
    roleplay_vpp_feedback_en.md
    human_response_schema.md
  role_cards/
    fixed_role_cards_zh_en.json
    fixed_role_cards_readable.md
    researcher_role_card_codebook.md
  scripts/
    build_vpp_survey_cases.py
  sample_cases/
    cleaned_vpp_survey_cases.json
    cleaned_vpp_survey_cases.csv
    survey_cases_readable_zh.md
    survey_cases_readable_en.md
    survey_stage1_zh.md
    survey_stage1_en.md
    survey_stage2_outcomes_zh.md
    survey_stage2_outcomes_en.md
    human_response_template.csv
    persona_onboarding_response_template.csv
    role_card_response_template.csv
    generation_summary.json
```

## Recommended Human Workflow

1. Give each participant one short preference questionnaire.
2. Assign the six fixed single-person role cards one by one. Within each role block, randomize the five anonymized methods. A participant may evaluate all six cards; each response must record the assigned role card ID.
3. For each VPP case:
   - Show role card.
   - Show event context.
   - Show the user-facing strategy in natural language.
   - Ask acceptance probability, binary accept/reject, up to three factors, and a 1-3 sentence reason.
   - Ask whether the strategy explanation helps acceptance on a 1-5 scale.
   - Show the outcome matching the participant's accept/reject choice.
   - Ask satisfaction score and combine the outcome reason and next improvement into one short response.
4. After all cases for one role card, ask role-play confidence once.
5. Store the answers using `questionnaires/human_response_schema.md`.

With Tianjin-only data, the full design contains 6 role blocks × 5 anonymized strategies = 30 short cases. The four-item onboarding is completed once, each role card is read once per block, and a short break can be inserted between blocks.

## Important Design Notes

- The human participant should see human-readable strategy text, not raw JSON.
- The participant should not see the algorithm/method label unless we are explicitly running a method-bias study.
- Use `survey_stage1_*.md` for the first-stage participant view. The role card and shared context appear once per five-strategy block. Keep `survey_stage2_outcomes_*.md` with the RA and reveal only the branch matching the participant's choice.
- Public case IDs use anonymous IDs such as `VPP-001`; method labels and raw paths stay in the researcher-side JSON/CSV.
- The role card is fixed and transparent to the participant; the participant is asked to role-play that card, not report only personal preferences.
- Role cards are controlled human-readable prompts. Each participant-facing card gives an approximate baseline willingness and qualitative judgment cues, while `role_cards/researcher_role_card_codebook.md` stores the exact baselines and expected adjustment ranges for RA auditing. This keeps non-reasoning factors controlled without making the participant task feel like a scoring formula.
- The response format preserves only two short text fields per case: acceptance reasoning and outcome feedback. These support reasoning-gap analysis and future preference training without making the survey unnecessarily long.
- `questionnaires/onboarding_questionnaire_schema.json` is the canonical schema shared by the human user preference questionnaire and EnergyBridge role-play LLM onboarding. Keep the four `question_id`s and option IDs aligned when editing either side.
- `questionnaires/strategy_presentation_protocol.md` defines how EB, HEMA, MPC, Rule+MILP, and RL are converted into participant-facing strategy text. EB receives a personalized explanation, HEMA receives a generic agent explanation when available, and traditional baselines receive only plain strategy translation.

## What Real Data Enters The Questionnaire

The participant-facing questionnaire should use real generated cases rather than hand-written strategies. For the active single-person pilot, each case merges:

- The real persona JSON, rewritten as a concise human-readable role card: schedule, comfort range, appliance constraints, preference weights, and VPP willingness anchor.
- The real city and VPP event context from `benchmark_result.json`: city, event day, VPP window, and requested reduction.
- The real generated VPP strategy: AC setpoint or HVAC-off equivalent, washer/dishwasher/dryer/water-heater/EV timing, and the method's own user-facing explanation when it exists.
- The real outcome metrics from the benchmark event: VPP-window energy, realized shed, reference user score, and reference comfort/energy/VPP sub-scores.

The method label, raw result path, and role-play reference scores stay in the researcher-side CSV/JSON and should not be shown to participants in the main calibration study. This lets us compare human reasoning with role-play LLM reasoning without introducing method-name bias.

## Source Data For Sample Cases

The active single-person Tianjin sample cases are generated from:

```text
benchmark_results/2026-07-07_mainfig_refresh_v1/
benchmark_results/2026-07-21_hema_persona_tianjin_1day_v1/
```

These sources provide complete 6 basic personas × Tianjin coverage for EnergyBridge, HEMA, MPC, Rule+MILP, and RL. The default human pilot uses Tianjin only so participants do not need to reason about multiple weather/price contexts.

The older household sample cases can still be generated from:

```text
paper_results/01_main_household_5x2_final/
benchmark_results/2026-07-19_eb_acceptance_cap76_mainfig_v1/
benchmark_results/2026-07-19_hema_household_5x2_7day_v2/
```

The script records exact raw `benchmark_result.json` paths in the generated JSON/CSV.

## Minimal Paper Story

The human pilot supports a focused claim: real-user questionnaires ground the persona distribution and prompt design, while human role-play under the same controlled persona measures and calibrates probability, decision, reasoning, explanation-effect, and satisfaction bias in LLM role-play. Human role-play is a reference proxy rather than real-user ground truth. The same records can later become supervised calibration data or pairwise preference data without changing the current EnergyBridge controller.

By default, `scripts/build_vpp_survey_cases.py` now builds the single-person 6 persona ×
Tianjin set. The participant-facing Markdown uses anonymous case IDs and does not expose
the method label.

Example:

```bash
python human_survey_materials/scripts/build_vpp_survey_cases.py
```

To build a short smoke subset:

```bash
python human_survey_materials/scripts/build_vpp_survey_cases.py --max-cases 12
```

To rebuild the older household sample:

```bash
python human_survey_materials/scripts/build_vpp_survey_cases.py --profile-mode household
```
