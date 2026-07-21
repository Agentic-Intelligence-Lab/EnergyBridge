# User Preference Questionnaire

Purpose: use four choices to record the participant's own home-energy preferences. Answer based on your own preferences; no role-play is required. Estimated time: 2 minutes. These answers do not determine your assigned role; they are used only to test whether personal preferences influence later role-play.

This questionnaire uses the same four `question_id`s and option IDs as the EnergyBridge onboarding questionnaire shown to the role-play LLM. Researchers retain it as a record of the participant's own preferences and as a role-leakage covariate.

## Background

A virtual power plant / VPP event means the grid asks households to reduce electricity use for a short peak period. For example, during 18:00-19:00, a home system may suggest a small AC temperature adjustment, or shifting washer, dishwasher, water heater, or EV charging. You may accept or reject the suggestion.

## Core Questions

### Q1. Priority During VPP Events

`question_id`: `vpp_priority`

During a one-hour VPP peak event, what should the home prioritize?

Choose the closest option.

- `comfort_routine_first`: Comfort and routine first; AC, hot water, and household rhythm should not noticeably change.
- `bill_savings_first`: Bill savings first; I can accept some inconvenience if the savings are clear.
- `grid_support_first`: Grid/environment support first; I am willing to help peak shaving if the impact is controlled.
- `balanced_tradeoff`: Balanced tradeoff; save money and support the grid when disruption is low.
- `confirm_before_changes`: Explanation and confirmation first; the system must explain clearly and ask before acting.

### Q2. Thermostat Flexibility

`question_id`: `thermostat_flexibility`

If the home remains safe, how much temporary AC setpoint change would you usually accept during a peak event?

Choose the closest option.

- `almost_none_0_5c`: Almost none, around 0.5°C or less.
- `small_1c_short`: Around 1°C, but only for a short time.
- `moderate_1_2c_with_benefit`: Around 1-2°C if savings or grid benefits are clear.
- `larger_when_unoccupied`: Larger changes are acceptable when nobody is home or when sleep/work/care routines are unaffected.

### Q3. Appliance Shift Consent

`question_id`: `appliance_shift_consent`

For washer, dishwasher, water heater, or EV charging, can the system automatically shift timing when deadlines are protected, or should it ask first?

Choose the closest option.

- `do_not_move_without_approval`: Do not move without explicit approval; follow my original plan.
- `shift_1_2h_deadline_protected`: It can shift by 1-2 hours if deadlines are protected.
- `shift_to_cheaper_periods`: It can shift to cheaper periods if hot water, laundry, travel, and other readiness needs are protected.
- `automatic_optimization_ok`: Automatic optimization is acceptable; I do not need to confirm every time.

### Q4. Calendar And Routine Constraints

`question_id`: `calendar_routine_constraints`

Which calendar or household routines should not be disturbed, especially around evening arrival, meals, showers, caregiving, sleep, or work?

Choose 1-2 options.

- `arrival_comfort`: Return-home comfort must be protected.
- `meals_chores`: Meals and fixed household chores must not be disrupted.
- `shower_hot_water`: Shower and hot-water timing must be protected.
- `caregiving_sleep_work`: Caregiving, sleep, or work routines must be protected.
- `irregular_confirm_same_day`: My schedule often changes; the system should confirm on the same day before acting.

## Optional Follow-up (One Item)

5. What is the one thing a home energy system must not interfere with for you? One sentence is enough.
