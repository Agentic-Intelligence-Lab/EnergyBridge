"""
Shiftable task load models for EnergyBridge benchmark.

First device: WashingMachine
  - Fixed-duration task load (binary start)
  - Once started, runs for `duration` hours continuously (non-interruptible)
  - Per-day state: IDLE -> RUNNING -> DONE
  - Tracks whether task ran during a VPP window (penalises VPP score)
  - Energy is Python-side only (not in EnergyPlus)

Mathematical model (from flexible_load_modeling reference):
  y_a(s) in {0,1}: start decision at hour s
  sum_{s=E_a}^{L_a} y_a(s) = 1      (must start exactly once per day)
  P_a(t) = p_a * sum_{d=0}^{D_a-1} y_a(t-d)   (power profile during operation)
"""
from __future__ import annotations
from typing import Optional


class WashingMachine:
    """Shiftable washing-machine task load.

    Attributes
    ----------
    earliest : float   Earliest allowed start (simulation hour, e.g. 10.0)
    latest   : float   Latest allowed start (task must START by this hour)
    preferred: float   User-preferred start hour
    duration : float   Fixed cycle duration in hours (default 2.0)
    power_kw : float   Average power during cycle (default 1.5 kW)
    """

    def __init__(
        self,
        earliest: float,
        latest: float,
        preferred: float,
        duration: float = 2.0,
        power_kw: float = 1.5,
    ) -> None:
        self.earliest = earliest
        self.latest = latest
        self.preferred = preferred
        self.duration = duration
        self.power_kw = power_kw

        # Mutable state (reset per simulation day)
        self._state: str = "IDLE"   # IDLE | RUNNING | DONE
        self._start_h: Optional[float] = None
        self._end_h: Optional[float] = None
        self._energy_kwh: float = 0.0

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    @property
    def start_h(self) -> Optional[float]:
        return self._start_h

    @property
    def energy_kwh(self) -> float:
        return round(self._energy_kwh, 3)

    # ------------------------------------------------------------------
    # Control interface
    # ------------------------------------------------------------------
    def can_start(self, sim_h: float) -> bool:
        """Return True if the washer can be started at this simulation hour."""
        return (
            self._state == "IDLE"
            and self.earliest <= sim_h <= self.latest
        )

    def start(self, sim_h: float) -> bool:
        """Start the washer at the given simulation hour.

        Returns True on success, False if start is not allowed.
        """
        if not self.can_start(sim_h):
            return False
        self._state = "RUNNING"
        self._start_h = sim_h
        self._end_h = sim_h + self.duration
        return True

    def tick(self, sim_h: float, dt_h: float) -> float:
        """Advance simulation by dt_h hours.

        Returns the electrical energy consumed (kWh) in this time step.
        Call this every simulation timestep whether or not washer is running.
        """
        if self._state == "RUNNING":
            if self._end_h is not None and sim_h >= self._end_h:
                self._state = "DONE"
                return 0.0
            energy = self.power_kw * dt_h
            self._energy_kwh += energy
            return energy
        return 0.0

    def reset_for_day(self) -> None:
        """Reset state for a new simulation day (call at day boundary)."""
        self._state = "IDLE"
        self._start_h = None
        self._end_h = None
        self._energy_kwh = 0.0

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------
    def is_running_during(self, window_start_h: float, window_end_h: float) -> bool:
        """Return True if washer overlaps with the given time window."""
        if self._start_h is None:
            return False
        return not (
            (self._end_h or 0.0) <= window_start_h
            or self._start_h >= window_end_h
        )

    def deadline_missed(self, sim_h: float) -> bool:
        """Return True if task is still IDLE and has passed the latest start hour."""
        return self._state == "IDLE" and sim_h > self.latest

    def get_status_dict(self) -> dict:
        """Return a serialisable status snapshot for logging and LLM prompts."""
        return {
            "state": self._state,
            "start_h": round(self._start_h, 1) if self._start_h is not None else None,
            "end_h": round(self._end_h, 1) if self._end_h is not None else None,
            "energy_kwh": self.energy_kwh,
            "window": f"{self.earliest:.0f}:00–{self.latest:.0f}:00",
            "preferred_h": self.preferred,
        }

    def prompt_line(self, sim_h: float) -> str:
        """One-line description for injection into LLM agent prompt."""
        st = self._state
        if st == "IDLE":
            window = f"{int(self.earliest):02d}:00–{int(self.latest % 24):02d}:00"
            pref_hh = int(self.preferred % 24)
            can = "can start now" if self.can_start(sim_h) else "not in window yet"
            return (
                f"Washing machine: IDLE | window {window} | preferred {pref_hh:02d}:00 | "
                f"duration {self.duration:.0f}h | {self.power_kw}kW | {can}"
            )
        elif st == "RUNNING":
            rem = max(0.0, (self._end_h or 0.0) - sim_h)
            return f"Washing machine: RUNNING (finishes in {rem:.1f}h)"
        else:
            return f"Washing machine: DONE (ran {self._start_h:.0f}:00–{self._end_h:.0f}:00, {self.energy_kwh:.2f} kWh)"


def make_washer_from_persona(persona: dict) -> WashingMachine:
    """Construct a WashingMachine from a persona definition dict."""
    cfg = persona.get("washer", {})
    return WashingMachine(
        earliest=float(cfg.get("earliest", 10.0)),
        latest=float(cfg.get("latest", 22.0)),
        preferred=float(cfg.get("preferred", 14.0)),
        duration=float(cfg.get("duration", 2.0)),
        power_kw=float(cfg.get("power_kw", 1.5)),
    )
