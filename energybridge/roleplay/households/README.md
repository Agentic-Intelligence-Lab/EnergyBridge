# Fixed Multi-User Households

This folder stores reproducible multi-user household scenarios. Each JSON file
defines one household as a "large user" made by overlaying existing persona
roles and their paired 7-day calendars.

Run one household with independent member role-play:

```bash
python experiments/benchmark/run_multi_user_household.py \
  --household household_s1_dual_commuter_standard \
  --city Germany --days 7 --start-date 2025-06-01 \
  --price-csv experiments/real_data/germany_2025_price.csv
```

Design rules:

- Members reuse existing persona JSON files under `roleplay/personas/`.
- Occupancy is deterministic: home is occupied if any member calendar is home.
- Appliances are household-level shared devices, not a sum of member devices.
- The first benchmark set uses the maximal controllable device set:
  `ac`, `washer`, `dryer`, `dishwasher`, `water_heater`, and `ev`.
- Strategy selection and satisfaction scoring are independent per member:
  each member comments from their own persona context before and after each
  event, then the benchmark stores member-level feedback and reports the mean
  household score.

Available fixed households:

| Household ID | Short description |
| --- | --- |
| `household_s1_dual_commuter_standard` | Standard dual-commuter family with child and comfort-sensitive elder |
| `household_s2_multigeneration_caregiver` | Multi-generation caregiver household with low DR tolerance |
| `household_s3_hybrid_work_from_home` | Work-from-home mixed household with irregular schedule member |
| `household_s4_ev_commuter_flexible` | EV-centered flexible commuter household |
| `household_s5_shared_roommates_irregular` | Shared roommates with conflicting incentives and routines |
