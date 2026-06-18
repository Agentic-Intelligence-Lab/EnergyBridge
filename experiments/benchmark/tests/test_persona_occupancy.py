from __future__ import annotations

import json
import shutil
from pathlib import Path

from energybridge.roleplay.calendar import hourly_occupancy_from_persona, occupancy_fraction_at_sim_hour
from experiments.benchmark.run_persona_json import _write_persona_occupancy_idf


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PERSONA_DIR = PROJECT_ROOT / "energybridge" / "roleplay" / "personas"
FAMILY_IDF = PROJECT_ROOT / "experiments" / "models" / "family_home" / "family_simple_7day.idf"


def _persona_with_calendar(persona_id: str) -> dict:
    persona = json.loads((PERSONA_DIR / f"{persona_id}.json").read_text(encoding="utf-8"))
    calendar = json.loads(
        (PERSONA_DIR / "calendars" / persona_id / "calendar_7day.json").read_text(encoding="utf-8")
    )
    persona["calendar"] = calendar
    return persona


def test_commuter_calendar_marks_office_hours_unoccupied() -> None:
    persona = _persona_with_calendar("atom_comfort_sensitive")

    hourly = hourly_occupancy_from_persona(persona, days=7)

    assert hourly is not None
    # Day 2 is Monday in the synthetic calendar.  The user is in office at 10:00
    # and back home during dinner/VPP time.
    assert hourly[1][10] == 0.0
    assert hourly[1][18] > 0.5
    assert occupancy_fraction_at_sim_hour(persona, 24.0 + 10.0) == 0.0


def test_caregiver_calendar_stays_occupied_without_away_events() -> None:
    persona = _persona_with_calendar("basic_role_e_caregiver_low_dr")

    hourly = hourly_occupancy_from_persona(persona, days=1)

    assert hourly is not None
    assert hourly[0][10] == 1.0
    assert hourly[0][18] == 1.0


def test_persona_occupancy_idf_replaces_people_schedule_and_controls_hvac(tmp_path: Path) -> None:
    persona = _persona_with_calendar("atom_comfort_sensitive")
    idf_copy = tmp_path / "family_simple_7day_occupancy.idf"
    shutil.copy2(FAMILY_IDF, idf_copy)

    assert _write_persona_occupancy_idf(idf_copy, persona, days=7)

    text = idf_copy.read_text(encoding="utf-8")
    assert "PersonaOccupancy,        !- Number of People Schedule Name" in text
    assert "PersonaOccupancyDay2" in text
    assert "Zone People Occupant Count" in text
    assert "HVAC_Availability_Control" in text
    assert "HVAC_Availability_Control;  !- Schedule Name" in text
