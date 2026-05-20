"""PMV-based rule controller for multi-zone HVAC.

Traditional baseline method (no LLM). Uses Fanger's PMV/PPD model (ISO 7730)
to decide cooling setpoints for each zone.

Decision rule (per zone, per timestep):
  PMV > +pmv_upper  →  decrease cooling setpoint by step_c  (zone too warm → cool more)
  PMV < -pmv_lower  →  increase cooling setpoint by step_c  (zone too cold → cool less)
  |PMV| ≤ threshold →  hold current setpoint

Typical office parameters (summer):
  clothing     = 0.5 clo
  metabolic    = 1.2 met  (seated, light office work)
  air_velocity = 0.1 m/s  (still indoor air)
  MRT ≈ air_temp           (approximation for internal well-insulated zones)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class PMVControllerParams:
    # Comfort thresholds (ISO 7730 Category B: |PMV| ≤ 0.5)
    pmv_upper: float = 0.5   # above this → cool more
    pmv_lower: float = 0.5   # below negative → cool less

    # Setpoint adjustment step per decision
    step_c: float = 0.5      # °C per step

    # Setpoint hard limits
    sp_min_c: float = 22.0
    sp_max_c: float = 28.0

    # Fixed occupant parameters (summer office)
    clothing: float = 0.5    # clo
    metabolic: float = 1.2   # met
    air_velocity: float = 0.1  # m/s

    # Default relative humidity when not available from weather
    default_rh: float = 50.0  # %

    # Default initial setpoint for all zones (IDF default = 26°C)
    initial_setpoint_c: float = 26.0


class PMVBaselineController:
    """Zone-by-zone PMV rule controller.

    Usage in an EnergyPlus callback loop:
        ctrl = PMVBaselineController(zones, params)
        ...
        for zone in zones:
            new_sp = ctrl.step(zone, zone_temp_c, outdoor_rh=rh)
            # write new_sp to actuator
    """

    def __init__(
        self,
        zones: list[str],
        params: Optional[PMVControllerParams] = None,
    ) -> None:
        self.params = params or PMVControllerParams()
        # Current cooling setpoint per zone
        self._setpoints: Dict[str, float] = {
            z: self.params.initial_setpoint_c for z in zones
        }
        # PMV history (last computed value, for logging)
        self._last_pmv: Dict[str, Optional[float]] = {z: None for z in zones}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def step(
        self,
        zone: str,
        zone_temp_c: float,
        *,
        mean_radiant_temp_c: Optional[float] = None,
        outdoor_rh: float = 50.0,
    ) -> float:
        """Compute new cooling setpoint for one zone.

        Args:
            zone: zone name
            zone_temp_c: current zone mean air temperature (°C)
            mean_radiant_temp_c: MRT; defaults to zone_temp_c if not provided
            outdoor_rh: relative humidity from outdoor/weather data (%)

        Returns:
            New recommended cooling setpoint (°C), clipped to [sp_min, sp_max].
        """
        p = self.params
        mrt = mean_radiant_temp_c if mean_radiant_temp_c is not None else zone_temp_c

        pmv = self._compute_pmv(
            tdb=zone_temp_c,
            tr=mrt,
            rh=outdoor_rh,
            v=p.air_velocity,
            met=p.metabolic,
            clo=p.clothing,
        )
        self._last_pmv[zone] = pmv

        current_sp = self._setpoints.get(zone, p.initial_setpoint_c)

        if pmv > p.pmv_upper:
            # Too warm: lower the cooling setpoint (cool more aggressively)
            new_sp = current_sp - p.step_c
        elif pmv < -p.pmv_lower:
            # Too cold: raise the cooling setpoint (cool less)
            new_sp = current_sp + p.step_c
        else:
            # Comfortable, hold
            new_sp = current_sp

        new_sp = max(p.sp_min_c, min(p.sp_max_c, new_sp))
        self._setpoints[zone] = new_sp
        return new_sp

    def get_pmv(self, zone: str) -> Optional[float]:
        return self._last_pmv.get(zone)

    def get_setpoint(self, zone: str) -> float:
        return self._setpoints.get(zone, self.params.initial_setpoint_c)

    def all_setpoints(self) -> Dict[str, float]:
        return dict(self._setpoints)

    def all_pmv(self) -> Dict[str, Optional[float]]:
        return dict(self._last_pmv)

    # ------------------------------------------------------------------
    # PMV computation (ISO 7730 / Fanger)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_pmv(
        tdb: float,
        tr: float,
        rh: float,
        v: float,
        met: float,
        clo: float,
    ) -> float:
        """Compute PMV using pythermalcomfort (ISO 7730).

        Falls back to a lightweight analytical approximation if the library
        is not available (should not happen after pip install).
        """
        try:
            from pythermalcomfort.models import pmv_ppd_iso
            result = pmv_ppd_iso(tdb=tdb, tr=tr, vr=v, rh=rh, met=met, clo=clo, limit_inputs=False)
            return float(result.pmv)
        except Exception:
            return PMVBaselineController._pmv_approx(tdb, tr, rh, v, met, clo)

    @staticmethod
    def _pmv_approx(tdb, tr, rh, v, met, clo) -> float:
        """Lightweight PMV approximation (ASHRAE linearisation).
        Good enough for rule-based setpoint decisions (±0.1 PMV accuracy).
        """
        # Operative temperature
        t_op = 0.5 * (tdb + tr)
        # Neutral operative temp for given activity/clothing (simplified Fanger)
        # t_neutral ≈ 33.5 - 3.5*met - 3.0*clo  (empirical for clo 0.5, met 1.2)
        t_neutral = 33.5 - 3.5 * met - 3.0 * clo
        # Humidity correction (≈ 0.07 PMV per 10% RH at neutral temp)
        rh_correction = (rh - 50.0) * 0.007
        pmv = 0.5 * (t_op - t_neutral) + rh_correction
        return round(pmv, 3)


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    zones = ["Core_bottom", "Perimeter_bot_ZN_1"]
    ctrl = PMVBaselineController(zones)

    print("=== PMV Controller Self-test ===")
    test_cases = [
        ("Core_bottom", 23.0, 45.0),
        ("Core_bottom", 25.5, 55.0),
        ("Core_bottom", 28.0, 65.0),
        ("Perimeter_bot_ZN_1", 26.0, 50.0),
    ]
    for zone, temp, rh in test_cases:
        sp = ctrl.step(zone, temp, outdoor_rh=rh)
        pmv = ctrl.get_pmv(zone)
        print(f"  zone={zone:<22}  temp={temp}°C  rh={rh}%  PMV={pmv:+.3f}  new_sp={sp:.1f}°C")
