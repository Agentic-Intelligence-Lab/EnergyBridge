# Persona Calendars

Each approved persona can have a paired synthetic calendar:

```text
calendars/<persona_id>/calendar_7day.json
```

The calendar layer uses synthetic events to infer
user-side constraints such as commute deadlines, return-home comfort, hot-water
needs, EV departure readiness, and appliance task deadlines.  These files are
offline and deterministic so benchmark role-play remains reproducible.

Day 1 is Sunday.  The current 3-day family benchmark therefore uses Sunday,
Monday, and Tuesday, while the file still covers a full week of weekday and
weekend behavior.

The role-play scorer automatically attaches the matching calendar when loading a
persona.  Calendar context is injected into:

- VPP strategy candidate generation
- role-play LLM strategy choice
- post-event satisfaction scoring
