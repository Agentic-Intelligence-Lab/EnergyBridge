# EnergyBridge V2 paired one-day smoke — 2026-08-27

This is a paired integration smoke test, not a statistical benchmark claim.
Both runs used the same Tianjin family, date, weather, tariff, VPP event, and
`gpt-5.4-mini` controller/role-play model configuration.

| Metric | EnergyBridge V2 | HEMA | EB relative to HEMA |
|---|---:|---:|---:|
| Total electricity | 30.4977 kWh | 49.0157 kWh | -37.8% |
| 18:00–19:00 VPP electricity | 0.9952 kWh | 2.2937 kWh | -56.6% |
| Normalized TOU cost | 19.8432 | 32.3386 | -38.6% |
| Mean acceptance probability | 0.78 | 0.17 | +0.61 |
| Final household rating | 4/5 | 2/5 | +2 |
| Flexible-device VPP avoidance | 100% | 66.7% | +33.3 pp |
| Physical service completion | 100% | 100% | equal |
| Total model tokens | 53,926 | 120,958 | -55.4% |

EnergyBridge generated one accepted commitment, retained it when the hard
state was unchanged, completed all services, and kept all flexible devices out
of the event. HEMA repeatedly queried and rescheduled devices; its water-heater
schedule still crossed the event and the household rejected the changed offer.

Run inputs:

- persona: `basic_role_a_commuter_price_cooperative`
- city: Tianjin
- simulation date: 2025-06-01
- VPP event: `[18:00, 19:00)`
- price: `experiments/real_data/tianjin_tou_price_normalized.csv`
- EnergyBridge result: `generated_results/v2_decision_epochs_1day_eb`
- HEMA result: `generated_results/v2_decision_epochs_1day_hema`

Limitations: one household-day is too small for a paper-level ranking; role-play
and controller sampling remain stochastic. The result is evidence that the
same-model harness path can outperform HEMA in a controlled smoke case. A
frozen multi-household, multi-day paired holdout with confidence intervals is
still required.

## EV-constrained household follow-up

A second paired smoke used the EV-constrained commuter household. All four
methods saw the same Tianjin day, tariff, `[18:00, 19:00)` event, and
`gpt-5.4-mini` role-play evaluator. EnergyBridge and HEMA also used that model
as controller. The conventional MPC and PPO controllers do not call a
controller LLM.

| Metric | EnergyBridge V2 | HEMA | MPC | PPO RL |
|---|---:|---:|---:|---:|
| Total electricity | 63.1588 kWh | 72.3237 kWh | 45.2438 kWh | 66.4841 kWh |
| 18:00–19:00 VPP electricity | 1.2789 kWh | 2.1853 kWh | 2.0107 kWh | 1.9931 kWh |
| Normalized TOU cost | 63.0043 | 54.3391 | 33.7898 | 65.3179 |
| Weighted normalized price | 1.1699/kWh | 1.0653/kWh | 0.9491/kWh | 1.1811/kWh |
| Acceptance probability | 0.68 | 0.69 | 0.18 | 0.17 |
| Accepted | yes | yes | no | no |
| Final household rating | 4/5 | 4/5 | 2/5 | 2/5 |
| Flexible-device VPP avoidance | 100% | 100% | 66.7% | 66.7% |
| Physical service completion | 100% | 100% | 100% | 100% |
| Controller-path model tokens | 60,280 | 81,870 | 0 | 0 |

EnergyBridge used a method-blind cyclic interval validator and a bounded,
convergence-aware semantic-repair loop. The model then selected an EV window
starting after the event and disabled a conflicting water-heater preheat. No
advisor candidate was substituted for the model's selection. Relative to
HEMA, EnergyBridge used 12.7% less total electricity, 41.5% less VPP-window
electricity, and 26.4% fewer controller-path tokens. HEMA achieved a lower
weighted tariff price by placing more EV energy in off-peak hours, so this
case does not support a claim that EnergyBridge dominates every objective.

HEMA's acceptance was not low in this run. Its event-start plan moved the
water heater outside the event and preserved a native household-facing
explanation, producing `p=0.69`. The trace also exposes a remaining evaluator
variance issue: the role-play response credited inherited EV readiness as a
positive offer adjustment even though the checked plan diff identified only
the water heater as changed. That is a prompt/evidence-grounding calibration
issue, not a reason to add a HEMA-specific probability rule.

Follow-up run inputs:

- persona: `basic_role_f_commuter_ev_optimizer`
- simulation date: 2025-06-03
- EnergyBridge: `generated_results/v2_paired_role_f_1day_eb`
- HEMA: `generated_results/v2_paired_role_f_1day_hema`
- MPC: `generated_results/v2_paired_role_f_1day_mpc`
- PPO RL: `generated_results/v2_paired_role_f_1day_rl`

These are single stochastic household-day observations. Continuous
probability is the useful smoke signal; a one-event binary acceptance rate is
necessarily zero or one and must not be presented as a population estimate.
