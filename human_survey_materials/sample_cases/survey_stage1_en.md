# VPP Human Survey: Stage 1 (Strategy Judgment)

## Role Card: `role_a_price_cooperative_commuter`

Role: Regular Commuter – Price-Cooperative
Role description: Away from home during weekday daytime; returns around 18:30. Energy use concentrated in the evening. Willing to reschedule household tasks to save money, but wants to know how much will be saved. Moderate temperature requirements; accepts small adjustments with confirmation. The most common cooperative user prototype encountered by the agent.
Routine: usually leaves home around 08:30, returns around 18:30, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, dishwasher, water heater.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 19:00, allowed 08:00-22:00; dishwasher preferred at 21:00, allowed 09:00-23:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 25/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy explains savings and makes hot water, chores, and return-home comfort feel reliable. You become less willing if it only talks about helping the grid or leaves evening arrangements uncertain.
Please judge as this role, not as your own real household.

Shared context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.

### Case VPP-001
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; dishwasher starts at 11:30; water heater preheats 01:30-05:30 to about 63.0°C. System explanation: these changes apply only during 18:00-19:00, with normal control restored afterward; it explicitly protects hot water before the shower, completion of required chores; you can cancel the plan and restore the usual setting if uncomfortable; the estimated shifted load is about 4.7 kW, with savings determined by the actual tariff or VPP rules.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-002
Strategy suggestion: Real generated VPP strategy: AC setpoint about 23.9°C; washer starts at 19:30; dishwasher starts at 20:30; water heater preheats 16:30-17:50 to about 57.2°C. Ordinary daily-plan AC setpoint is about 24.0°C for comparison. System explanation: to support this peak event, the plan will keep AC within a reasonable comfort range and return to normal operation afterward.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-003
Strategy suggestion: Real generated VPP strategy: AC setpoint about 26.5°C; washer starts at 19:00; dishwasher starts at 21:00; water heater preheats 14:00-18:00 to about 65.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-004
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; dishwasher starts at 12:00; water heater preheats 01:30-05:30 to about 63.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-005
Strategy suggestion: Real generated VPP strategy: AC setpoint about 27.5°C; washer starts at 08:00; dishwasher starts at 21:29; water heater preheats 13:07-16:07 to about 74.8°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

## Role Card: `role_b_home_comfort_gated`

Role: Stay-at-Home – Comfort-Gated
Role description: Home all day, possibly working remotely or caring for a family member. Sensitive to temperature changes; unwilling to sacrifice comfort for small savings. Can tolerate very short and very small thermostat adjustments, but only with prior confirmation. Agent challenge: protect comfort while finding minimal flexibility.
Routine: usually at home all day; wakes around 07:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.3 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 24.5-25.5°C, with tolerance around 0.5°C.
Device routine: washer preferred at 10:00, allowed 09:00-20:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 12/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy barely changes perceived comfort, keeps the impact brief, and protects work/rest, hot water, and chores. You become less willing if it creates stuffiness, distraction, or manual fixes.
Please judge as this role, not as your own real household.

Shared context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.

### Case VPP-006
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 09:00; water heater preheats 04:00-06:00 to about 63.0°C. System explanation: these changes apply only during 18:00-19:00, with normal control restored afterward; it explicitly protects hot water before the shower, completion of required chores; you can cancel the plan and restore the usual setting if uncomfortable; the estimated shifted load is about 0.0 kW, with savings determined by the actual tariff or VPP rules.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-007
Strategy suggestion: Real generated VPP strategy: AC setpoint about 24.0°C; washer starts at 19:30; water heater preheats 16:15-19:00 to about 57.2°C. Ordinary daily-plan AC setpoint is about 25.5°C for comparison. System explanation: to support this peak event, the plan will keep AC within a reasonable comfort range and return to normal operation afterward.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-008
Strategy suggestion: Real generated VPP strategy: AC setpoint about 26.5°C; washer starts at 10:00; water heater preheats 18:00-20:00 to about 65.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-009
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 09:00; water heater preheats 05:30-07:30 to about 63.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-010
Strategy suggestion: Real generated VPP strategy: AC setpoint about 27.3°C; washer starts at 10:00; water heater preheats 13:16-16:16 to about 75.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

## Role Card: `role_c_irregular_cautious`

Role: Irregular Schedule – High-Confirmation
Role description: Highly variable routine; frequently works late, makes unplanned outings, or travels. Plans change often and historical patterns are unreliable. Open to energy-saving suggestions but needs clear benefit explanations and confirmation before each action. Agent challenge: real-time re-planning and avoiding over-reliance on historical data.
Routine: usually leaves home around 08:30, returns around 19:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 3.0 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 19:00, allowed 08:00-22:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 10/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy is conservative, clear, reversible, and does not rely on fixed schedule assumptions. You become less willing if it assumes you are away or moves hot water/chores/AC without confirmation.
Please judge as this role, not as your own real household.

Shared context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.

### Case VPP-011
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; water heater preheats 03:30-05:30 to about 63.0°C. System explanation: these changes apply only during 18:00-19:00, with normal control restored afterward; it explicitly protects hot water before the shower, completion of required chores; you can cancel the plan and restore the usual setting if uncomfortable; the estimated shifted load is about 0.0 kW, with savings determined by the actual tariff or VPP rules.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-012
Strategy suggestion: Real generated VPP strategy: AC setpoint about 26.0°C; washer starts at 19:30; water heater preheats 18:00-19:00 to about 57.2°C. Ordinary daily-plan AC setpoint is about 26.0°C for comparison. System explanation: to support this peak event, the plan will keep AC within a reasonable comfort range and return to normal operation afterward.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-013
Strategy suggestion: Real generated VPP strategy: AC setpoint about 26.5°C; washer starts at 19:00; water heater preheats 18:00-20:00 to about 65.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-014
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; water heater preheats 01:00-03:00 to about 63.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-015
Strategy suggestion: Real generated VPP strategy: AC setpoint about 27.1°C; washer starts at 19:00; water heater preheats 13:16-16:16 to about 75.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

## Role Card: `role_d_ideal_dr_participant`

Role: Regular Commuter – Ideal DR Candidate
Role description: Regular schedule with moderate temperature tolerance; willing to let the agent schedule automatically within preset bounds. Household tasks can be freely rescheduled. Price-sensitive and eager to earn demand-response rewards. The most reliable DR resource type; ideal for aggregator recruitment.
Routine: usually leaves home around 08:30, returns around 18:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.3 h.
Controllable devices: AC, washer, dishwasher, water heater.
Comfort boundary: AC comfort range about 23.0-27.0°C, with tolerance around 2.0°C.
Device routine: washer preferred at 13:00, allowed 08:00-23:00; dishwasher preferred at 22:00, allowed 09:00-23:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 45/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy clearly reduces peak load and still completes tasks at reasonable times. You become less willing if it misses hot water, EV, required chores, or uses clearly unreasonable extreme temperatures.
Please judge as this role, not as your own real household.

Shared context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.

### Case VPP-016
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; dishwasher starts at 12:30; water heater preheats 01:00-06:00 to about 63.0°C. System explanation: these changes apply only during 18:00-19:00, with normal control restored afterward; it explicitly protects hot water before the shower, completion of required chores; you can cancel the plan and restore the usual setting if uncomfortable; the estimated shifted load is about 4.7 kW, with savings determined by the actual tariff or VPP rules.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-017
Strategy suggestion: Real generated VPP strategy: AC setpoint about 23.3°C; washer starts at 19:30; dishwasher starts at 20:30; water heater preheats 17:00-18:00 to about 57.2°C. Ordinary daily-plan AC setpoint is about 24.0°C for comparison. System explanation: to support this peak event, the plan will keep AC within a reasonable comfort range and return to normal operation afterward.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-018
Strategy suggestion: Real generated VPP strategy: AC setpoint about 27.5°C; washer starts at 13:00; dishwasher starts at 21:30; water heater preheats 13:00-18:00 to about 65.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-019
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; dishwasher starts at 09:30; water heater preheats 02:30-07:30 to about 63.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-020
Strategy suggestion: Real generated VPP strategy: AC setpoint about 27.6°C; washer starts at 08:00; dishwasher starts at 21:30; water heater preheats 13:04-16:04 to about 74.6°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

## Role Card: `role_e_caregiver_low_dr`

Role: Family Caregiver – Low DR Value
Role description: Elderly or young children at home; comfort, safety, and stability have the highest priority. Not suitable as a demand-response target. The agent should focus on energy-saving tips and anomaly alerts, not proactively push DR or make adjustments. A key role for testing the agent's safety fallback strategy.
Routine: usually at home all day; wakes around 06:30, needs hot water around 20:00, sleeps around 22:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 22.0-25.0°C, with tolerance around 0.5°C.
Device routine: washer preferred at 10:00, allowed 09:00-18:00; hot water must be ready by 20:00.
VPP acceptance anchor: about 8/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become somewhat more willing if the strategy is very mild, barely changes temperature or routine, and clearly protects caregiving, hot water, showers, and chores. You become less willing if it affects care, delays hot water, raises AC, or creates extra work.
Please judge as this role, not as your own real household.

Shared context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.

### Case VPP-021
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 09:00; water heater preheats 04:00-06:00 to about 63.0°C. System explanation: these changes apply only during 18:00-19:00, with normal control restored afterward; it explicitly protects hot water before the shower, completion of required chores; you can cancel the plan and restore the usual setting if uncomfortable; the estimated shifted load is about 0.0 kW, with savings determined by the actual tariff or VPP rules.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-022
Strategy suggestion: Real generated VPP strategy: AC setpoint about 23.3°C; washer starts at 19:30; water heater preheats 17:50-19:00 to about 57.2°C. Ordinary daily-plan AC setpoint is about 25.0°C for comparison. System explanation: to support this peak event, the plan will keep AC within a reasonable comfort range and return to normal operation afterward.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-023
Strategy suggestion: Real generated VPP strategy: AC setpoint about 26.5°C; washer starts at 10:00; water heater preheats 17:00-19:00 to about 65.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-024
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 09:00; water heater preheats 02:30-04:30 to about 63.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-025
Strategy suggestion: Real generated VPP strategy: AC setpoint about 27.3°C; washer starts at 10:00; water heater preheats 13:16-16:16 to about 75.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

## Role Card: `role_f_ev_commuter_optimizer`

Role: Regular Commuter – EV Optimiser
Role description: EV owner who charges at home in the evening and leaves in the morning. Willing to let the system automatically schedule charging at off-peak tariffs, provided the required SOC for the next day's trip is always guaranteed. Price-sensitive and trusts the system to act autonomously. Typical user combining EV flexibility with time-of-use pricing.
Routine: usually leaves home around 07:30, returns around 18:30, needs hot water around 20:00, sleeps around 23:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, water heater, EV.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 20:00, allowed 08:00-22:00; hot water must be ready by 20:00; EV plugs in around 18:30 and must reach target charge before 07:30 next day.
VPP acceptance anchor: about 25/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy avoids the VPP peak, explicitly guarantees EV charge, completes hot water/chores, and explains price benefit. You become less willing if EV charging is uncertain or next-day travel may be affected.
Please judge as this role, not as your own real household.

Shared context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.

### Case VPP-026
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; water heater preheats 01:00-05:00 to about 63.0°C. System explanation: these changes apply only during 18:00-19:00, with normal control restored afterward; it explicitly protects hot water before the shower, target EV charge before departure, completion of required chores; you can cancel the plan and restore the usual setting if uncomfortable; the estimated shifted load is about 10.9 kW, with savings determined by the actual tariff or VPP rules.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-027
Strategy suggestion: Real generated VPP strategy: AC setpoint about 24.0°C; EV charging 19:00-23:00. Ordinary daily-plan AC setpoint is about 24.0°C for comparison. Note: some appliance timing may still conflict with the VPP window. System explanation: to support this peak event, the plan will keep AC within a reasonable comfort range and return to normal operation afterward.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-028
Strategy suggestion: Real generated VPP strategy: AC setpoint about 26.5°C; washer starts at 20:00; water heater preheats 15:00-19:00 to about 65.0°C; EV charging 18:30-07:30. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-029
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; water heater preheats 04:30-08:30 to about 63.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.

### Case VPP-030
Strategy suggestion: Real generated VPP strategy: AC setpoint about 27.2°C; washer starts at 08:00; water heater preheats 12:55-15:55 to about 74.9°C; EV charging 20:00-06:25. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Answer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.
