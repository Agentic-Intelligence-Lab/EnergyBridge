# Researcher Role Card Codebook

This file is for researchers and RAs only. Do not show these exact ranges to participants in the main study. The participant-facing role cards describe the same tendencies in natural language.

Use this codebook to audit whether a human response follows the intended role prior and whether the reasoning plausibly moves from baseline to final probability.

| Role | Baseline | Upward Evidence | Approx. High-Fit Range | Downward Evidence | Approx. Poor-Fit Range |
| --- | ---: | --- | ---: | --- | ---: |
| `role_a_price_cooperative_commuter` | 25/100 | Clear savings, return-home comfort protected, hot water/chores/EV reliable | 55-85 | Grid-only explanation, uncertain hot water/EV/chores, poor return comfort | 0-20 |
| `role_b_home_comfort_gated` | 12/100 | Barely changes comfort, brief impact, protects work/rest/hot water/chores | 45-75 | AC discomfort, stuffiness, distraction, manual fixes | 0-10 |
| `role_c_irregular_cautious` | 10/100 | Conservative, clear, reversible, robust to same-day schedule changes | 35-65 | Assumes away-from-home, no confirmation, loss of control | 0-8 |
| `role_d_ideal_dr_participant` | 45/100 | Clear peak reduction, task completion, reasonable timing | 75-95 | Missed hot water/EV/chores, extreme temperature | 15-35 |
| `role_e_caregiver_low_dr` | 8/100 | Very mild, protects caregiving, hot water, showers, chores | 30-60 | Affects care, delays hot water, raises AC, creates extra work | 0-5 |
| `role_f_ev_commuter_optimizer` | 25/100 | Avoids VPP peak, guarantees EV charge, completes hot water/chores, explains price benefit | 60-90 | EV uncertain, next-day travel risk, unclear charging completion | 0-15 |

Recommended coding:

- `prior_alignment`: whether the response starts from the intended baseline range.
- `evidence_alignment`: whether the response cites role-relevant strategy evidence.
- `adjustment_direction`: increase, decrease, or unchanged.
- `final_probability_plausibility`: whether final probability is plausible given the baseline and cited evidence.
- `reasoning_gap_note`: short explanation of any human/LLM reasoning mismatch.

