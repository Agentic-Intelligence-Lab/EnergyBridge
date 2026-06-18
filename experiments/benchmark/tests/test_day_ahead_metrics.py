from __future__ import annotations

import pytest

from energybridge.data.day_ahead import read_facility_meter_steps


def test_facility_meter_steps_detect_dynamic_meter_code(tmp_path) -> None:
    (tmp_path / "eplusout.mtr").write_text(
        "\n".join(
            [
                "Program Version,EnergyPlus",
                "1,5,Environment Title[],Latitude[deg],Longitude[deg],Time Zone[],Elevation[m]",
                "2,8,Day of Simulation[],Month[],Day of Month[],DST Indicator[1=yes 0=no],Hour[],StartMinute[],EndMinute[],DayType",
                "9,1,InteriorEquipment:Electricity [J] !TimeStep",
                "10,1,Electricity:Facility [J] !TimeStep",
                "End of Data Dictionary",
                "2,1, 6, 1, 0, 1, 0.00,10.00,Sunday",
                "9,9999999.0",
                "10,3600000.0",
                "2,1, 6, 1, 0, 1,10.00,20.00,Sunday",
                "10,1800000.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    steps = read_facility_meter_steps(tmp_path)

    assert len(steps) == 2
    assert steps[0]["start_h"] == pytest.approx(0.0)
    assert steps[0]["end_h"] == pytest.approx(1 / 6)
    assert steps[0]["kwh"] == pytest.approx(1.0)
    assert steps[1]["kwh"] == pytest.approx(0.5)
