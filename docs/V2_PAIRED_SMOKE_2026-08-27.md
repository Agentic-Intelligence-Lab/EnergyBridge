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
