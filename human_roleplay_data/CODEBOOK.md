# Codebook

## Persona conditions

| Persona | Participants |
|---|---:|
| Price-sensitive | 97 |
| Comfort-sensitive | 116 |
| Irregular routine | 73 |
| Cooperative regular | 88 |
| Caregiver | 91 |
| EV commuter | 119 |

## Translated questions

- **Authorization:** "Would you be willing to adopt this plan?"
- **Satisfaction:** "How satisfied are you with the outcome?"

Authorization options were Accept and Reject. Satisfaction used a 0-5 slider.
Method names were hidden during collection.

## `data/responses.csv`

| Field | Type | Definition |
|---|---|---|
| `participant_id` | string | Random public ID used only to pair three judgments. |
| `persona` | categorical | Assigned household role-play condition. |
| `method` | categorical | MPC, HEMA, or EnergyBridge. |
| `method_order` | integer | Questionnaire block order: 1, 2, or 3. |
| `acceptance` | binary | 1=accept; 0=reject. |
| `satisfaction_score` | numeric | Satisfaction value in [0,5]. |

## `data/participants.csv`

| Field | Type | Definition |
|---|---|---|
| `participant_id` | string | Random ID shared with `responses.csv`. |
| `persona` | categorical | Assigned role-play condition. |
| `age_band` | categorical | One of 20-24, 25-34, 35-44, or 45-55. |

Participant-level gender, exact age, and geography are not released.

## Generated analysis

No derived result table is distributed. Running
`scripts/reproduce_analysis.py` creates human-only acceptance, satisfaction,
age-band, omnibus, and paired-comparison tables in a user-selected output
directory. Rates and interval bounds are proportions in [0,1]. Wilson
intervals use the standard normal 95% critical value. Paired contrasts use
participants as the resampling unit and exact McNemar tests for discordant
pairs.
