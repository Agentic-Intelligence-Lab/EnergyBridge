# Persona Library - Schema v2.0

This folder stores the residential user personas used by EnergyBridge role-play
evaluation. The persona design follows a six-dimensional behavioral tag
framework:

```text
persona = schedule x comfort x task x price x control x grid_value
```

The goal is to provide stable, reproducible virtual users so benchmark results
can be compared across versions.

## Persona Layers

- `atom_*`: single-factor regression personas. One dimension is stressed while
  the remaining dimensions stay close to neutral.
- `basic_role_*`: richer composite personas used as the main evaluation set.
- `archetype_*` and `derived_*`: historical composite personas kept for
  backward-compatible comparison.

## Tag Dimensions

| Dimension | Meaning | Main system behavior affected |
| --- | --- | --- |
| `schedule` | when the user is home, away, or asleep | shiftable-load windows and pre-cooling/preheating timing |
| `comfort` | thermal sensitivity | acceptable DR temperature drift and duration |
| `task` | whether chores or EV charging can move | washer, dishwasher, hot-water, and EV scheduling |
| `price` | response to economic incentives | DR framing, price explanation, and offer threshold |
| `control` | trust in automation | auto-execution vs. confirmation vs. notification-only behavior |
| `grid_value` | value of flexible grid contribution | aggregator-side dispatch priority |

Valid enum values are defined in `energybridge/roleplay/schema.py`.

## Main Evaluation Personas

The compact main set contains six `basic_role_*` personas and a few high-value
`atom_*` regression points. Additional personas that are not part of the default
evaluation set live under `_not_run/`; the loader only scans this root folder, so
files under `_not_run/` are not loaded unless moved back here.

Recommended starting points:

- `basic_role_a_commuter_price_cooperative`: cooperative commuter, price-aware,
  and task-flexible.
- `basic_role_b_home_comfort_gated`: work-from-home user who needs comfort and
  explicit confirmation.
- `basic_role_c_irregular_cautious`: irregular schedule with cautious control
  expectations.
- `basic_role_d_commuter_ideal_dr`: ideal DR user with stable routine and high
  automation trust.
- `basic_role_e_caregiver_low_dr`: caregiver household with low DR suitability.
- `basic_role_f_commuter_ev_optimizer`: commuter with EV charging constraints.

## Calendar Files

`calendars/<persona_id>/calendar_7day.json` stores an offline synthetic weekly
calendar generated inside EnergyBridge. It does not depend on external calendar
APIs. Day 1 is fixed as Sunday, so the common 3-day benchmark corresponds to
Sunday, Monday, and Tuesday; the 7-day benchmark covers the full week.

Calendar context is injected into:

- the three VPP strategy candidates before each event;
- role-play LLM selection among A/B/C strategies;
- post-event satisfaction scoring.

The calendar includes daily events, VPP-window conflicts, return-home time,
hot-water deadlines, EV departure requirements, and chore deadlines.

## Loading Examples

```python
from energybridge.roleplay.loader import load_personas

all_personas = load_personas()
atoms = [p for p in all_personas if p["meta"]["persona_type"] == "atom"]
basic = [
    p for p in all_personas
    if p["meta"]["persona_type"] == "archetype" and p["id"].startswith("basic_")
]
```
