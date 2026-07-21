# Sample VPP Human Survey Cases

## Case 1: VPP-001
User persona ID: `role_a_price_cooperative_commuter`

### User persona Profile

Role: Regular Commuter – Price-Cooperative
Role description: Away from home during weekday daytime; returns around 18:30. Energy use concentrated in the evening. Willing to reschedule household tasks to save money, but wants to know how much will be saved. Moderate temperature requirements; accepts small adjustments with confirmation. The most common cooperative user prototype encountered by the agent.
Routine: usually leaves home around 08:30, returns around 18:30, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, dishwasher, water heater.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 19:00, allowed 08:00-22:00; dishwasher preferred at 21:00, allowed 09:00-23:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 25/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy explains savings and makes hot water, chores, and return-home comfort feel reliable. You become less willing if it only talks about helping the grid or leaves evening arrangements uncertain.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; dishwasher starts at 11:30; water heater preheats 01:30-05:30 to about 63.0°C. System explanation: these changes apply only during 18:00-19:00, with normal control restored afterward; it explicitly protects hot water before the shower, completion of required chores; you can cancel the plan and restore the usual setting if uncomfortable; the estimated shifted load is about 4.7 kW, with savings determined by the actual tariff or VPP rules.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 1.06 kWh, with estimated shed about 2.76 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 2: VPP-002
User persona ID: `role_a_price_cooperative_commuter`

### User persona Profile

Role: Regular Commuter – Price-Cooperative
Role description: Away from home during weekday daytime; returns around 18:30. Energy use concentrated in the evening. Willing to reschedule household tasks to save money, but wants to know how much will be saved. Moderate temperature requirements; accepts small adjustments with confirmation. The most common cooperative user prototype encountered by the agent.
Routine: usually leaves home around 08:30, returns around 18:30, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, dishwasher, water heater.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 19:00, allowed 08:00-22:00; dishwasher preferred at 21:00, allowed 09:00-23:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 25/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy explains savings and makes hot water, chores, and return-home comfort feel reliable. You become less willing if it only talks about helping the grid or leaves evening arrangements uncertain.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 23.9°C; washer starts at 19:30; dishwasher starts at 20:30; water heater preheats 16:30-17:50 to about 57.2°C. Ordinary daily-plan AC setpoint is about 24.0°C for comparison. System explanation: to support this peak event, the plan will keep AC within a reasonable comfort range and return to normal operation afterward.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system would execute the VPP strategy above. In the full experiment, this sample's realized branch was rejection and fallback, so the accepted branch was not separately re-simulated. Judge mainly from whether the strategy text would fit the role.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Ordinary-plan AC target is about 24.0°C. This sample realized the rejection/fallback branch; actual VPP-window electricity is about 3.26 kWh, with estimated shed about 12.49 kWh. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 3: VPP-003
User persona ID: `role_a_price_cooperative_commuter`

### User persona Profile

Role: Regular Commuter – Price-Cooperative
Role description: Away from home during weekday daytime; returns around 18:30. Energy use concentrated in the evening. Willing to reschedule household tasks to save money, but wants to know how much will be saved. Moderate temperature requirements; accepts small adjustments with confirmation. The most common cooperative user prototype encountered by the agent.
Routine: usually leaves home around 08:30, returns around 18:30, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, dishwasher, water heater.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 19:00, allowed 08:00-22:00; dishwasher preferred at 21:00, allowed 09:00-23:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 25/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy explains savings and makes hot water, chores, and return-home comfort feel reliable. You become less willing if it only talks about helping the grid or leaves evening arrangements uncertain.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 26.5°C; washer starts at 19:00; dishwasher starts at 21:00; water heater preheats 14:00-18:00 to about 65.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 1.77 kWh, with estimated shed about 2.51 kWh. Result note: No major risk is obvious; participants should focus on whether this fits the role's priorities. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 4: VPP-004
User persona ID: `role_a_price_cooperative_commuter`

### User persona Profile

Role: Regular Commuter – Price-Cooperative
Role description: Away from home during weekday daytime; returns around 18:30. Energy use concentrated in the evening. Willing to reschedule household tasks to save money, but wants to know how much will be saved. Moderate temperature requirements; accepts small adjustments with confirmation. The most common cooperative user prototype encountered by the agent.
Routine: usually leaves home around 08:30, returns around 18:30, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, dishwasher, water heater.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 19:00, allowed 08:00-22:00; dishwasher preferred at 21:00, allowed 09:00-23:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 25/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy explains savings and makes hot water, chores, and return-home comfort feel reliable. You become less willing if it only talks about helping the grid or leaves evening arrangements uncertain.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; dishwasher starts at 12:00; water heater preheats 01:30-05:30 to about 63.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 1.05 kWh, with estimated shed about 0.01 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 5: VPP-005
User persona ID: `role_a_price_cooperative_commuter`

### User persona Profile

Role: Regular Commuter – Price-Cooperative
Role description: Away from home during weekday daytime; returns around 18:30. Energy use concentrated in the evening. Willing to reschedule household tasks to save money, but wants to know how much will be saved. Moderate temperature requirements; accepts small adjustments with confirmation. The most common cooperative user prototype encountered by the agent.
Routine: usually leaves home around 08:30, returns around 18:30, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, dishwasher, water heater.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 19:00, allowed 08:00-22:00; dishwasher preferred at 21:00, allowed 09:00-23:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 25/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy explains savings and makes hot water, chores, and return-home comfort feel reliable. You become less willing if it only talks about helping the grid or leaves evening arrangements uncertain.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 27.5°C; washer starts at 08:00; dishwasher starts at 21:29; water heater preheats 13:07-16:07 to about 74.8°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 1.08 kWh, with estimated shed about 4.85 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 6: VPP-006
User persona ID: `role_b_home_comfort_gated`

### User persona Profile

Role: Stay-at-Home – Comfort-Gated
Role description: Home all day, possibly working remotely or caring for a family member. Sensitive to temperature changes; unwilling to sacrifice comfort for small savings. Can tolerate very short and very small thermostat adjustments, but only with prior confirmation. Agent challenge: protect comfort while finding minimal flexibility.
Routine: usually at home all day; wakes around 07:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.3 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 24.5-25.5°C, with tolerance around 0.5°C.
Device routine: washer preferred at 10:00, allowed 09:00-20:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 12/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy barely changes perceived comfort, keeps the impact brief, and protects work/rest, hot water, and chores. You become less willing if it creates stuffiness, distraction, or manual fixes.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 09:00; water heater preheats 04:00-06:00 to about 63.0°C. System explanation: these changes apply only during 18:00-19:00, with normal control restored afterward; it explicitly protects hot water before the shower, completion of required chores; you can cancel the plan and restore the usual setting if uncomfortable; the estimated shifted load is about 0.0 kW, with savings determined by the actual tariff or VPP rules.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 0.40 kWh, with estimated shed about 1.05 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 7: VPP-007
User persona ID: `role_b_home_comfort_gated`

### User persona Profile

Role: Stay-at-Home – Comfort-Gated
Role description: Home all day, possibly working remotely or caring for a family member. Sensitive to temperature changes; unwilling to sacrifice comfort for small savings. Can tolerate very short and very small thermostat adjustments, but only with prior confirmation. Agent challenge: protect comfort while finding minimal flexibility.
Routine: usually at home all day; wakes around 07:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.3 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 24.5-25.5°C, with tolerance around 0.5°C.
Device routine: washer preferred at 10:00, allowed 09:00-20:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 12/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy barely changes perceived comfort, keeps the impact brief, and protects work/rest, hot water, and chores. You become less willing if it creates stuffiness, distraction, or manual fixes.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 24.0°C; washer starts at 19:30; water heater preheats 16:15-19:00 to about 57.2°C. Ordinary daily-plan AC setpoint is about 25.5°C for comparison. System explanation: to support this peak event, the plan will keep AC within a reasonable comfort range and return to normal operation afterward.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system would execute the VPP strategy above. In the full experiment, this sample's realized branch was rejection and fallback, so the accepted branch was not separately re-simulated. Judge mainly from whether the strategy text would fit the role.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Ordinary-plan AC target is about 25.5°C. This sample realized the rejection/fallback branch; actual VPP-window electricity is about 3.34 kWh, with estimated shed about 2.72 kWh. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 8: VPP-008
User persona ID: `role_b_home_comfort_gated`

### User persona Profile

Role: Stay-at-Home – Comfort-Gated
Role description: Home all day, possibly working remotely or caring for a family member. Sensitive to temperature changes; unwilling to sacrifice comfort for small savings. Can tolerate very short and very small thermostat adjustments, but only with prior confirmation. Agent challenge: protect comfort while finding minimal flexibility.
Routine: usually at home all day; wakes around 07:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.3 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 24.5-25.5°C, with tolerance around 0.5°C.
Device routine: washer preferred at 10:00, allowed 09:00-20:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 12/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy barely changes perceived comfort, keeps the impact brief, and protects work/rest, hot water, and chores. You become less willing if it creates stuffiness, distraction, or manual fixes.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 26.5°C; washer starts at 10:00; water heater preheats 18:00-20:00 to about 65.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 4.59 kWh, with estimated shed about 0.00 kWh. Result note: Main risks: comfort may be weak; VPP peak reduction may be unreliable. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 9: VPP-009
User persona ID: `role_b_home_comfort_gated`

### User persona Profile

Role: Stay-at-Home – Comfort-Gated
Role description: Home all day, possibly working remotely or caring for a family member. Sensitive to temperature changes; unwilling to sacrifice comfort for small savings. Can tolerate very short and very small thermostat adjustments, but only with prior confirmation. Agent challenge: protect comfort while finding minimal flexibility.
Routine: usually at home all day; wakes around 07:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.3 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 24.5-25.5°C, with tolerance around 0.5°C.
Device routine: washer preferred at 10:00, allowed 09:00-20:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 12/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy barely changes perceived comfort, keeps the impact brief, and protects work/rest, hot water, and chores. You become less willing if it creates stuffiness, distraction, or manual fixes.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 09:00; water heater preheats 05:30-07:30 to about 63.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 0.40 kWh, with estimated shed about 0.63 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 10: VPP-010
User persona ID: `role_b_home_comfort_gated`

### User persona Profile

Role: Stay-at-Home – Comfort-Gated
Role description: Home all day, possibly working remotely or caring for a family member. Sensitive to temperature changes; unwilling to sacrifice comfort for small savings. Can tolerate very short and very small thermostat adjustments, but only with prior confirmation. Agent challenge: protect comfort while finding minimal flexibility.
Routine: usually at home all day; wakes around 07:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.3 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 24.5-25.5°C, with tolerance around 0.5°C.
Device routine: washer preferred at 10:00, allowed 09:00-20:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 12/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy barely changes perceived comfort, keeps the impact brief, and protects work/rest, hot water, and chores. You become less willing if it creates stuffiness, distraction, or manual fixes.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 27.3°C; washer starts at 10:00; water heater preheats 13:16-16:16 to about 75.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 0.99 kWh, with estimated shed about 1.04 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 11: VPP-011
User persona ID: `role_c_irregular_cautious`

### User persona Profile

Role: Irregular Schedule – High-Confirmation
Role description: Highly variable routine; frequently works late, makes unplanned outings, or travels. Plans change often and historical patterns are unreliable. Open to energy-saving suggestions but needs clear benefit explanations and confirmation before each action. Agent challenge: real-time re-planning and avoiding over-reliance on historical data.
Routine: usually leaves home around 08:30, returns around 19:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 3.0 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 19:00, allowed 08:00-22:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 10/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy is conservative, clear, reversible, and does not rely on fixed schedule assumptions. You become less willing if it assumes you are away or moves hot water/chores/AC without confirmation.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; water heater preheats 03:30-05:30 to about 63.0°C. System explanation: these changes apply only during 18:00-19:00, with normal control restored afterward; it explicitly protects hot water before the shower, completion of required chores; you can cancel the plan and restore the usual setting if uncomfortable; the estimated shifted load is about 0.0 kW, with savings determined by the actual tariff or VPP rules.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 0.40 kWh, with estimated shed about 0.83 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 12: VPP-012
User persona ID: `role_c_irregular_cautious`

### User persona Profile

Role: Irregular Schedule – High-Confirmation
Role description: Highly variable routine; frequently works late, makes unplanned outings, or travels. Plans change often and historical patterns are unreliable. Open to energy-saving suggestions but needs clear benefit explanations and confirmation before each action. Agent challenge: real-time re-planning and avoiding over-reliance on historical data.
Routine: usually leaves home around 08:30, returns around 19:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 3.0 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 19:00, allowed 08:00-22:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 10/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy is conservative, clear, reversible, and does not rely on fixed schedule assumptions. You become less willing if it assumes you are away or moves hot water/chores/AC without confirmation.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 26.0°C; washer starts at 19:30; water heater preheats 18:00-19:00 to about 57.2°C. Ordinary daily-plan AC setpoint is about 26.0°C for comparison. System explanation: to support this peak event, the plan will keep AC within a reasonable comfort range and return to normal operation afterward.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system would execute the VPP strategy above. In the full experiment, this sample's realized branch was rejection and fallback, so the accepted branch was not separately re-simulated. Judge mainly from whether the strategy text would fit the role.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Ordinary-plan AC target is about 26.0°C. This sample realized the rejection/fallback branch; actual VPP-window electricity is about 3.69 kWh, with estimated shed about 0.16 kWh. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 13: VPP-013
User persona ID: `role_c_irregular_cautious`

### User persona Profile

Role: Irregular Schedule – High-Confirmation
Role description: Highly variable routine; frequently works late, makes unplanned outings, or travels. Plans change often and historical patterns are unreliable. Open to energy-saving suggestions but needs clear benefit explanations and confirmation before each action. Agent challenge: real-time re-planning and avoiding over-reliance on historical data.
Routine: usually leaves home around 08:30, returns around 19:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 3.0 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 19:00, allowed 08:00-22:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 10/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy is conservative, clear, reversible, and does not rely on fixed schedule assumptions. You become less willing if it assumes you are away or moves hot water/chores/AC without confirmation.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 26.5°C; washer starts at 19:00; water heater preheats 18:00-20:00 to about 65.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 5.10 kWh, with estimated shed about 0.00 kWh. Result note: Main risks: VPP peak reduction may be unreliable. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 14: VPP-014
User persona ID: `role_c_irregular_cautious`

### User persona Profile

Role: Irregular Schedule – High-Confirmation
Role description: Highly variable routine; frequently works late, makes unplanned outings, or travels. Plans change often and historical patterns are unreliable. Open to energy-saving suggestions but needs clear benefit explanations and confirmation before each action. Agent challenge: real-time re-planning and avoiding over-reliance on historical data.
Routine: usually leaves home around 08:30, returns around 19:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 3.0 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 19:00, allowed 08:00-22:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 10/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy is conservative, clear, reversible, and does not rely on fixed schedule assumptions. You become less willing if it assumes you are away or moves hot water/chores/AC without confirmation.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; water heater preheats 01:00-03:00 to about 63.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 1.06 kWh, with estimated shed about 0.00 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 15: VPP-015
User persona ID: `role_c_irregular_cautious`

### User persona Profile

Role: Irregular Schedule – High-Confirmation
Role description: Highly variable routine; frequently works late, makes unplanned outings, or travels. Plans change often and historical patterns are unreliable. Open to energy-saving suggestions but needs clear benefit explanations and confirmation before each action. Agent challenge: real-time re-planning and avoiding over-reliance on historical data.
Routine: usually leaves home around 08:30, returns around 19:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 3.0 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 19:00, allowed 08:00-22:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 10/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy is conservative, clear, reversible, and does not rely on fixed schedule assumptions. You become less willing if it assumes you are away or moves hot water/chores/AC without confirmation.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 27.1°C; washer starts at 19:00; water heater preheats 13:16-16:16 to about 75.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 1.68 kWh, with estimated shed about 0.00 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 16: VPP-016
User persona ID: `role_d_ideal_dr_participant`

### User persona Profile

Role: Regular Commuter – Ideal DR Candidate
Role description: Regular schedule with moderate temperature tolerance; willing to let the agent schedule automatically within preset bounds. Household tasks can be freely rescheduled. Price-sensitive and eager to earn demand-response rewards. The most reliable DR resource type; ideal for aggregator recruitment.
Routine: usually leaves home around 08:30, returns around 18:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.3 h.
Controllable devices: AC, washer, dishwasher, water heater.
Comfort boundary: AC comfort range about 23.0-27.0°C, with tolerance around 2.0°C.
Device routine: washer preferred at 13:00, allowed 08:00-23:00; dishwasher preferred at 22:00, allowed 09:00-23:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 45/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy clearly reduces peak load and still completes tasks at reasonable times. You become less willing if it misses hot water, EV, required chores, or uses clearly unreasonable extreme temperatures.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; dishwasher starts at 12:30; water heater preheats 01:00-06:00 to about 63.0°C. System explanation: these changes apply only during 18:00-19:00, with normal control restored afterward; it explicitly protects hot water before the shower, completion of required chores; you can cancel the plan and restore the usual setting if uncomfortable; the estimated shifted load is about 4.7 kW, with savings determined by the actual tariff or VPP rules.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 1.06 kWh, with estimated shed about 0.95 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 17: VPP-017
User persona ID: `role_d_ideal_dr_participant`

### User persona Profile

Role: Regular Commuter – Ideal DR Candidate
Role description: Regular schedule with moderate temperature tolerance; willing to let the agent schedule automatically within preset bounds. Household tasks can be freely rescheduled. Price-sensitive and eager to earn demand-response rewards. The most reliable DR resource type; ideal for aggregator recruitment.
Routine: usually leaves home around 08:30, returns around 18:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.3 h.
Controllable devices: AC, washer, dishwasher, water heater.
Comfort boundary: AC comfort range about 23.0-27.0°C, with tolerance around 2.0°C.
Device routine: washer preferred at 13:00, allowed 08:00-23:00; dishwasher preferred at 22:00, allowed 09:00-23:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 45/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy clearly reduces peak load and still completes tasks at reasonable times. You become less willing if it misses hot water, EV, required chores, or uses clearly unreasonable extreme temperatures.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 23.3°C; washer starts at 19:30; dishwasher starts at 20:30; water heater preheats 17:00-18:00 to about 57.2°C. Ordinary daily-plan AC setpoint is about 24.0°C for comparison. System explanation: to support this peak event, the plan will keep AC within a reasonable comfort range and return to normal operation afterward.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 2.88 kWh, with estimated shed about 13.53 kWh. Result note: No major risk is obvious; participants should focus on whether this fits the role's priorities. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Ordinary-plan AC target is about 24.0°C. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 18: VPP-018
User persona ID: `role_d_ideal_dr_participant`

### User persona Profile

Role: Regular Commuter – Ideal DR Candidate
Role description: Regular schedule with moderate temperature tolerance; willing to let the agent schedule automatically within preset bounds. Household tasks can be freely rescheduled. Price-sensitive and eager to earn demand-response rewards. The most reliable DR resource type; ideal for aggregator recruitment.
Routine: usually leaves home around 08:30, returns around 18:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.3 h.
Controllable devices: AC, washer, dishwasher, water heater.
Comfort boundary: AC comfort range about 23.0-27.0°C, with tolerance around 2.0°C.
Device routine: washer preferred at 13:00, allowed 08:00-23:00; dishwasher preferred at 22:00, allowed 09:00-23:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 45/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy clearly reduces peak load and still completes tasks at reasonable times. You become less willing if it misses hot water, EV, required chores, or uses clearly unreasonable extreme temperatures.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 27.5°C; washer starts at 13:00; dishwasher starts at 21:30; water heater preheats 13:00-18:00 to about 65.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 1.28 kWh, with estimated shed about 4.46 kWh. Result note: No major risk is obvious; participants should focus on whether this fits the role's priorities. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 19: VPP-019
User persona ID: `role_d_ideal_dr_participant`

### User persona Profile

Role: Regular Commuter – Ideal DR Candidate
Role description: Regular schedule with moderate temperature tolerance; willing to let the agent schedule automatically within preset bounds. Household tasks can be freely rescheduled. Price-sensitive and eager to earn demand-response rewards. The most reliable DR resource type; ideal for aggregator recruitment.
Routine: usually leaves home around 08:30, returns around 18:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.3 h.
Controllable devices: AC, washer, dishwasher, water heater.
Comfort boundary: AC comfort range about 23.0-27.0°C, with tolerance around 2.0°C.
Device routine: washer preferred at 13:00, allowed 08:00-23:00; dishwasher preferred at 22:00, allowed 09:00-23:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 45/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy clearly reduces peak load and still completes tasks at reasonable times. You become less willing if it misses hot water, EV, required chores, or uses clearly unreasonable extreme temperatures.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; dishwasher starts at 09:30; water heater preheats 02:30-07:30 to about 63.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 0.47 kWh, with estimated shed about 0.95 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 20: VPP-020
User persona ID: `role_d_ideal_dr_participant`

### User persona Profile

Role: Regular Commuter – Ideal DR Candidate
Role description: Regular schedule with moderate temperature tolerance; willing to let the agent schedule automatically within preset bounds. Household tasks can be freely rescheduled. Price-sensitive and eager to earn demand-response rewards. The most reliable DR resource type; ideal for aggregator recruitment.
Routine: usually leaves home around 08:30, returns around 18:00, needs hot water around 21:00, sleeps around 23:00; schedule variability about 0.3 h.
Controllable devices: AC, washer, dishwasher, water heater.
Comfort boundary: AC comfort range about 23.0-27.0°C, with tolerance around 2.0°C.
Device routine: washer preferred at 13:00, allowed 08:00-23:00; dishwasher preferred at 22:00, allowed 09:00-23:00; hot water must be ready by 21:00.
VPP acceptance anchor: about 45/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy clearly reduces peak load and still completes tasks at reasonable times. You become less willing if it misses hot water, EV, required chores, or uses clearly unreasonable extreme temperatures.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 27.6°C; washer starts at 08:00; dishwasher starts at 21:30; water heater preheats 13:04-16:04 to about 74.6°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 1.06 kWh, with estimated shed about 4.87 kWh. Result note: No major risk is obvious; participants should focus on whether this fits the role's priorities. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 21: VPP-021
User persona ID: `role_e_caregiver_low_dr`

### User persona Profile

Role: Family Caregiver – Low DR Value
Role description: Elderly or young children at home; comfort, safety, and stability have the highest priority. Not suitable as a demand-response target. The agent should focus on energy-saving tips and anomaly alerts, not proactively push DR or make adjustments. A key role for testing the agent's safety fallback strategy.
Routine: usually at home all day; wakes around 06:30, needs hot water around 20:00, sleeps around 22:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 22.0-25.0°C, with tolerance around 0.5°C.
Device routine: washer preferred at 10:00, allowed 09:00-18:00; hot water must be ready by 20:00.
VPP acceptance anchor: about 8/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become somewhat more willing if the strategy is very mild, barely changes temperature or routine, and clearly protects caregiving, hot water, showers, and chores. You become less willing if it affects care, delays hot water, raises AC, or creates extra work.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 09:00; water heater preheats 04:00-06:00 to about 63.0°C. System explanation: these changes apply only during 18:00-19:00, with normal control restored afterward; it explicitly protects hot water before the shower, completion of required chores; you can cancel the plan and restore the usual setting if uncomfortable; the estimated shifted load is about 0.0 kW, with savings determined by the actual tariff or VPP rules.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 0.40 kWh, with estimated shed about 1.05 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 22: VPP-022
User persona ID: `role_e_caregiver_low_dr`

### User persona Profile

Role: Family Caregiver – Low DR Value
Role description: Elderly or young children at home; comfort, safety, and stability have the highest priority. Not suitable as a demand-response target. The agent should focus on energy-saving tips and anomaly alerts, not proactively push DR or make adjustments. A key role for testing the agent's safety fallback strategy.
Routine: usually at home all day; wakes around 06:30, needs hot water around 20:00, sleeps around 22:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 22.0-25.0°C, with tolerance around 0.5°C.
Device routine: washer preferred at 10:00, allowed 09:00-18:00; hot water must be ready by 20:00.
VPP acceptance anchor: about 8/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become somewhat more willing if the strategy is very mild, barely changes temperature or routine, and clearly protects caregiving, hot water, showers, and chores. You become less willing if it affects care, delays hot water, raises AC, or creates extra work.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 23.3°C; washer starts at 19:30; water heater preheats 17:50-19:00 to about 57.2°C. Ordinary daily-plan AC setpoint is about 25.0°C for comparison. System explanation: to support this peak event, the plan will keep AC within a reasonable comfort range and return to normal operation afterward.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system would execute the VPP strategy above. In the full experiment, this sample's realized branch was rejection and fallback, so the accepted branch was not separately re-simulated. Judge mainly from whether the strategy text would fit the role.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Ordinary-plan AC target is about 25.0°C. This sample realized the rejection/fallback branch; actual VPP-window electricity is about 3.68 kWh, with estimated shed about 4.54 kWh. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 23: VPP-023
User persona ID: `role_e_caregiver_low_dr`

### User persona Profile

Role: Family Caregiver – Low DR Value
Role description: Elderly or young children at home; comfort, safety, and stability have the highest priority. Not suitable as a demand-response target. The agent should focus on energy-saving tips and anomaly alerts, not proactively push DR or make adjustments. A key role for testing the agent's safety fallback strategy.
Routine: usually at home all day; wakes around 06:30, needs hot water around 20:00, sleeps around 22:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 22.0-25.0°C, with tolerance around 0.5°C.
Device routine: washer preferred at 10:00, allowed 09:00-18:00; hot water must be ready by 20:00.
VPP acceptance anchor: about 8/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become somewhat more willing if the strategy is very mild, barely changes temperature or routine, and clearly protects caregiving, hot water, showers, and chores. You become less willing if it affects care, delays hot water, raises AC, or creates extra work.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 26.5°C; washer starts at 10:00; water heater preheats 17:00-19:00 to about 65.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 3.27 kWh, with estimated shed about 1.86 kWh. Result note: Main risks: comfort may be weak; VPP peak reduction may be unreliable. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 24: VPP-024
User persona ID: `role_e_caregiver_low_dr`

### User persona Profile

Role: Family Caregiver – Low DR Value
Role description: Elderly or young children at home; comfort, safety, and stability have the highest priority. Not suitable as a demand-response target. The agent should focus on energy-saving tips and anomaly alerts, not proactively push DR or make adjustments. A key role for testing the agent's safety fallback strategy.
Routine: usually at home all day; wakes around 06:30, needs hot water around 20:00, sleeps around 22:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 22.0-25.0°C, with tolerance around 0.5°C.
Device routine: washer preferred at 10:00, allowed 09:00-18:00; hot water must be ready by 20:00.
VPP acceptance anchor: about 8/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become somewhat more willing if the strategy is very mild, barely changes temperature or routine, and clearly protects caregiving, hot water, showers, and chores. You become less willing if it affects care, delays hot water, raises AC, or creates extra work.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 09:00; water heater preheats 02:30-04:30 to about 63.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 0.56 kWh, with estimated shed about 1.33 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 25: VPP-025
User persona ID: `role_e_caregiver_low_dr`

### User persona Profile

Role: Family Caregiver – Low DR Value
Role description: Elderly or young children at home; comfort, safety, and stability have the highest priority. Not suitable as a demand-response target. The agent should focus on energy-saving tips and anomaly alerts, not proactively push DR or make adjustments. A key role for testing the agent's safety fallback strategy.
Routine: usually at home all day; wakes around 06:30, needs hot water around 20:00, sleeps around 22:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, water heater.
Comfort boundary: AC comfort range about 22.0-25.0°C, with tolerance around 0.5°C.
Device routine: washer preferred at 10:00, allowed 09:00-18:00; hot water must be ready by 20:00.
VPP acceptance anchor: about 8/100 in ordinary cases, a very low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become somewhat more willing if the strategy is very mild, barely changes temperature or routine, and clearly protects caregiving, hot water, showers, and chores. You become less willing if it affects care, delays hot water, raises AC, or creates extra work.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 27.3°C; washer starts at 10:00; water heater preheats 13:16-16:16 to about 75.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 0.98 kWh, with estimated shed about 1.08 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 26: VPP-026
User persona ID: `role_f_ev_commuter_optimizer`

### User persona Profile

Role: Regular Commuter – EV Optimiser
Role description: EV owner who charges at home in the evening and leaves in the morning. Willing to let the system automatically schedule charging at off-peak tariffs, provided the required SOC for the next day's trip is always guaranteed. Price-sensitive and trusts the system to act autonomously. Typical user combining EV flexibility with time-of-use pricing.
Routine: usually leaves home around 07:30, returns around 18:30, needs hot water around 20:00, sleeps around 23:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, water heater, EV.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 20:00, allowed 08:00-22:00; hot water must be ready by 20:00; EV plugs in around 18:30 and must reach target charge before 07:30 next day.
VPP acceptance anchor: about 25/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy avoids the VPP peak, explicitly guarantees EV charge, completes hot water/chores, and explains price benefit. You become less willing if EV charging is uncertain or next-day travel may be affected.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; water heater preheats 01:00-05:00 to about 63.0°C. System explanation: these changes apply only during 18:00-19:00, with normal control restored afterward; it explicitly protects hot water before the shower, target EV charge before departure, completion of required chores; you can cancel the plan and restore the usual setting if uncomfortable; the estimated shifted load is about 10.9 kW, with savings determined by the actual tariff or VPP rules.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 1.06 kWh, with estimated shed about 1.67 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 27: VPP-027
User persona ID: `role_f_ev_commuter_optimizer`

### User persona Profile

Role: Regular Commuter – EV Optimiser
Role description: EV owner who charges at home in the evening and leaves in the morning. Willing to let the system automatically schedule charging at off-peak tariffs, provided the required SOC for the next day's trip is always guaranteed. Price-sensitive and trusts the system to act autonomously. Typical user combining EV flexibility with time-of-use pricing.
Routine: usually leaves home around 07:30, returns around 18:30, needs hot water around 20:00, sleeps around 23:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, water heater, EV.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 20:00, allowed 08:00-22:00; hot water must be ready by 20:00; EV plugs in around 18:30 and must reach target charge before 07:30 next day.
VPP acceptance anchor: about 25/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy avoids the VPP peak, explicitly guarantees EV charge, completes hot water/chores, and explains price benefit. You become less willing if EV charging is uncertain or next-day travel may be affected.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 24.0°C; EV charging 19:00-23:00. Ordinary daily-plan AC setpoint is about 24.0°C for comparison. Note: some appliance timing may still conflict with the VPP window. System explanation: to support this peak event, the plan will keep AC within a reasonable comfort range and return to normal operation afterward.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system would execute the VPP strategy above. In the full experiment, this sample's realized branch was rejection and fallback, so the accepted branch was not separately re-simulated. Judge mainly from whether the strategy text would fit the role.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Ordinary-plan AC target is about 24.0°C. This sample realized the rejection/fallback branch; actual VPP-window electricity is about 3.26 kWh, with estimated shed about 12.49 kWh. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 28: VPP-028
User persona ID: `role_f_ev_commuter_optimizer`

### User persona Profile

Role: Regular Commuter – EV Optimiser
Role description: EV owner who charges at home in the evening and leaves in the morning. Willing to let the system automatically schedule charging at off-peak tariffs, provided the required SOC for the next day's trip is always guaranteed. Price-sensitive and trusts the system to act autonomously. Typical user combining EV flexibility with time-of-use pricing.
Routine: usually leaves home around 07:30, returns around 18:30, needs hot water around 20:00, sleeps around 23:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, water heater, EV.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 20:00, allowed 08:00-22:00; hot water must be ready by 20:00; EV plugs in around 18:30 and must reach target charge before 07:30 next day.
VPP acceptance anchor: about 25/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy avoids the VPP peak, explicitly guarantees EV charge, completes hot water/chores, and explains price benefit. You become less willing if EV charging is uncertain or next-day travel may be affected.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 26.5°C; washer starts at 20:00; water heater preheats 15:00-19:00 to about 65.0°C; EV charging 18:30-07:30. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 2.48 kWh, with estimated shed about 2.33 kWh. Result note: Main risks: VPP peak reduction may be unreliable. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 29: VPP-029
User persona ID: `role_f_ev_commuter_optimizer`

### User persona Profile

Role: Regular Commuter – EV Optimiser
Role description: EV owner who charges at home in the evening and leaves in the morning. Willing to let the system automatically schedule charging at off-peak tariffs, provided the required SOC for the next day's trip is always guaranteed. Price-sensitive and trusts the system to act autonomously. Typical user combining EV flexibility with time-of-use pricing.
Routine: usually leaves home around 07:30, returns around 18:30, needs hot water around 20:00, sleeps around 23:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, water heater, EV.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 20:00, allowed 08:00-22:00; hot water must be ready by 20:00; EV plugs in around 18:30 and must reach target charge before 07:30 next day.
VPP acceptance anchor: about 25/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy avoids the VPP peak, explicitly guarantees EV charge, completes hot water/chores, and explains price benefit. You become less willing if EV charging is uncertain or next-day travel may be affected.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: temporarily pause cooling during the VPP window (simulation-equivalent setpoint about 40.0°C); washer starts at 08:00; water heater preheats 04:30-08:30 to about 63.0°C. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 1.05 kWh, with estimated shed about 0.00 kWh. Result note: Main risks: comfort may be weak. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.

## Case 30: VPP-030
User persona ID: `role_f_ev_commuter_optimizer`

### User persona Profile

Role: Regular Commuter – EV Optimiser
Role description: EV owner who charges at home in the evening and leaves in the morning. Willing to let the system automatically schedule charging at off-peak tariffs, provided the required SOC for the next day's trip is always guaranteed. Price-sensitive and trusts the system to act autonomously. Typical user combining EV flexibility with time-of-use pricing.
Routine: usually leaves home around 07:30, returns around 18:30, needs hot water around 20:00, sleeps around 23:00; schedule variability about 0.5 h.
Controllable devices: AC, washer, water heater, EV.
Comfort boundary: AC comfort range about 24.0-26.0°C, with tolerance around 1.0°C.
Device routine: washer preferred at 20:00, allowed 08:00-22:00; hot water must be ready by 20:00; EV plugs in around 18:30 and must reach target charge before 07:30 next day.
VPP acceptance anchor: about 25/100 in ordinary cases, a low baseline; adjust up or down based on comfort, schedule fit, and explanation quality.
Judgment cue: You become more willing if the strategy avoids the VPP peak, explicitly guarantees EV charge, completes hot water/chores, and explains price benefit. You become less willing if EV charging is uncertain or next-day travel may be affected.
Please judge as this role, not as your own real household.
Context: Region: tianjin. Day 1. The VPP event is 18:00-19:00. The grid asks this home to use about 3.08 kWh less electricity during this 1-hour window.
Strategy suggestion: Real generated VPP strategy: AC setpoint about 27.2°C; washer starts at 08:00; water heater preheats 12:55-15:55 to about 74.9°C; EV charging 20:00-06:25. This method did not generate a personalized user-facing explanation; only the real control actions are translated.

Stage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; select up to three key factors and explain in 1-3 sentences why the strategy moved you away from the role-card baseline; then rate explanation helpfulness from 1-5.

Accepted outcome: If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about 1.38 kWh, with estimated shed about 4.74 kWh. Result note: No major risk is obvious; participants should focus on whether this fits the role's priorities. Please judge satisfaction from the role card.

Rejected outcome: If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window. Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation.

Stage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.
