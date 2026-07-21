# Human Response Data Schema (Short Form)

This schema records the minimum data needed to compare human role-play and LLM role-play judgments, calibrate LLM role-play probabilities and scores, and construct future preference-training examples.

Do not show `source_method` to participants. Keep method labels and raw source paths in the researcher-side case table.

## One Row Per Strategy Case

| Column | Type | Description |
| --- | --- | --- |
| participant_id | string | Anonymous participant identifier. |
| session_id | string | Survey session identifier. |
| participant_case_id | string | Public case ID, such as `VPP-001`. |
| source_case_id | string | Researcher-side case ID. |
| role_card_id | string | Assigned role card. |
| language | enum | `zh` or `en`. |
| final_accept_probability_0_100 | integer | Human probability of accepting after reading the strategy. |
| accept_choice | enum | `accept` or `reject` for this event. |
| selected_factors | string/list | Up to three factors: `comfort`, `chores`, `hot_water`, `ev`, `cost`, `grid_support`, `calendar_fit`, `explanation`, `user_control`, `reliability`, `other`. |
| accept_reason_text | string | 1-3 sentence baseline-to-final reasoning. |
| explanation_helpfulness_1_5 | integer | Effect of the explanation on willingness; 1 reduces willingness, 3 no effect, 5 clearly helps. |
| outcome_branch_seen | enum | `accepted_outcome` or `rejected_outcome`. |
| outcome_satisfaction_1_5 | integer | Satisfaction after seeing the matching result. |
| outcome_feedback_text | string | 1-3 sentence score reason and, when needed, the most important change for next time. |
| response_timestamp | datetime | Completion time. |

The role card's baseline probability is already stored in `cleaned_vpp_survey_cases.json`; it does not need to be copied by the participant. Adjustment direction can be derived by comparing that baseline with `final_accept_probability_0_100`.

## One Row Per Completed Role Card

| Column | Type | Description |
| --- | --- | --- |
| participant_id | string | Anonymous participant identifier. |
| session_id | string | Survey session identifier. |
| role_card_id | string | Completed role card. |
| roleplay_confidence_1_5 | integer | Confidence in answering as the role rather than as oneself. |
| role_card_issue_text | string | Optional unclear or unrealistic detail. |

## Optional Researcher Coding

These fields may be added after collection; participants should not fill them.

| Column | Type | Description |
| --- | --- | --- |
| coded_primary_reason | enum | Primary acceptance or rejection reason. |
| coded_secondary_reason | enum | Secondary reason. |
| coded_reasoning_match | integer | Human vs role-play reasoning agreement for this case. |
| notes | string | Researcher notes. |

## Paper and Training Use

- Probability calibration: compare human-role-play and LLM-role-play acceptance probabilities with calibration curves, Brier score, and expected calibration error.
- Decision calibration: compare accept/reject labels with accuracy, F1, and disagreement by persona and method.
- Reasoning calibration: compare selected factors and manually coded primary reasons.
- Satisfaction calibration: compare human and role-play 1-5 scores and ranking consistency.
- Role leakage: test whether a participant's responses follow the assigned role card or correlate more strongly with the participant's own onboarding answers.
- Future training: use `role card + event + strategy -> accept choice/probability/reason`, and derive pairwise preferences between strategies shown under the same role and event.
