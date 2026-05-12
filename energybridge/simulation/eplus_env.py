"""EnergyPlus environment wrapper for event-driven EnergyBridge agent integration.

Architecture
------------
EnergyPlus runs continuously via pyenergyplus.  At each zone timestep the
``_timestep_callback`` fires.  If a VPP event is waiting in the event queue
the callback:

1. reads the current building state via StateReader
2. runs the full EnergyBridge agent loop (graph.invoke)
3. writes the resulting control_plan back via ActuatorWriter
4. stores the agent result for the caller to inspect

The EnergyBridge agent graph and all its nodes are completely unchanged.

Usage
-----
::

    from energybridge.simulation.eplus_env import EplusEnv

    env = EplusEnv(
        idf_path="Family_Model/Family_Simple.idf",
        epw_path="Family_Model/Weather/Tianjin/CHN_Tianjin...epw",
        output_dir="logs/eplus_run",
        eplus_root="/home/ha_agent/EnergyPlus-24-1-0",
    )
    env.inject_vpp_event(vpp_context, user_input="配合削峰")
    env.run()                          # blocks until EnergyPlus finishes
    results = env.agent_results        # list of per-event agent outputs
"""

from __future__ import annotations

import queue
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# EnergyPlus path bootstrap (mirrors control_model.py convention)
# ---------------------------------------------------------------------------

_DEFAULT_EPLUS_ROOT = Path("/home/ha_agent/EnergyPlus-24-1-0")


def _ensure_eplus_on_path(eplus_root: Path) -> None:
    if str(eplus_root) not in sys.path:
        sys.path.insert(0, str(eplus_root))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class VPPEvent:
    """A VPP/DR event injected into the simulation."""

    vpp_context: dict[str, Any]
    user_input: str = "我希望尽量舒服，但如果电网有需求，也可以短时间配合削峰。"
    # Simulation time (hour of year, 0-based) at which to trigger the event.
    # None means "trigger at the next available timestep".
    trigger_hour: Optional[float] = None


@dataclass
class AgentResult:
    """Outcome of one agent loop invocation triggered by a VPP event."""

    sim_hour: float
    vpp_context: dict[str, Any]
    home_state: dict[str, Any]
    control_plan: dict[str, Any]
    safety_report: dict[str, Any]
    execution_result: dict[str, Any]
    final_response: str
    trajectory: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main environment class
# ---------------------------------------------------------------------------


class EplusEnv:
    """Wraps EnergyPlus and connects it to the EnergyBridge agent loop."""

    def __init__(
        self,
        idf_path: str | Path,
        epw_path: str | Path,
        output_dir: str | Path = "logs/eplus_run",
        eplus_root: str | Path = _DEFAULT_EPLUS_ROOT,
        memory_path: str = "logs/memory.json",
        log_dir: str = "logs",
    ) -> None:
        self.idf_path = Path(idf_path)
        self.epw_path = Path(epw_path)
        self.output_dir = Path(output_dir)
        self.eplus_root = Path(eplus_root)
        self.memory_path = memory_path
        self.log_dir = log_dir

        # Thread-safe event queue
        self._event_queue: queue.Queue[VPPEvent] = queue.Queue()

        # Results accumulated during the run
        self.agent_results: list[AgentResult] = []

        # Internal state (populated during run)
        self._state_reader = None
        self._actuator_writer = None
        self._api = None
        self._handles_ready = False

        # Lock to prevent concurrent agent invocations (shouldn't happen in
        # single-threaded EnergyPlus, but be safe)
        self._agent_lock = threading.Lock()

        # Track simulation start day so trigger_hour is relative (cumulative)
        self._sim_start_day: int | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def inject_vpp_event(
        self,
        vpp_context: dict[str, Any],
        user_input: str = "我希望尽量舒服，但如果电网有需求，也可以短时间配合削峰。",
        trigger_hour: Optional[float] = None,
    ) -> None:
        """Queue a VPP event to be processed during the simulation.

        Can be called before or during env.run().
        """
        self._event_queue.put(
            VPPEvent(
                vpp_context=vpp_context,
                user_input=user_input,
                trigger_hour=trigger_hour,
            )
        )

    def run(self) -> int:
        """Start EnergyPlus and block until it finishes.

        Returns the EnergyPlus exit code (0 = success).
        """
        _ensure_eplus_on_path(self.eplus_root)

        from pyenergyplus.api import EnergyPlusAPI  # type: ignore[import]

        from energybridge.simulation.actuator_writer import ActuatorWriter
        from energybridge.simulation.state_reader import StateReader

        self._state_reader = StateReader()
        self._actuator_writer = ActuatorWriter()

        api = EnergyPlusAPI()
        self._api = api
        state = api.state_manager.new_state()

        # Request output variables BEFORE run_energyplus is called.
        # pyenergyplus requires request_variable to be called before the
        # simulation starts; calling it inside a callback is too late.
        self._state_reader.request_variables(api.exchange, state)

        # Main control callback – fires every zone timestep
        api.runtime.callback_begin_system_timestep_before_predictor(
            state, self._make_timestep_callback(api, state)
        )

        self.output_dir.mkdir(parents=True, exist_ok=True)

        exit_code = api.runtime.run_energyplus(
            state,
            [
                "-w", str(self.epw_path),
                "-d", str(self.output_dir),
                str(self.idf_path),
            ],
        )

        api.state_manager.delete_state(state)
        return exit_code

    # ------------------------------------------------------------------
    # Callbacks (returned as closures so they capture self)
    # ------------------------------------------------------------------

    def _make_timestep_callback(self, api, state):
        """Return the main timestep callback."""

        def _callback(s) -> None:
            # Initialise handles on first ready timestep
            if not self._handles_ready:
                reader_ok = self._state_reader.init_handles(api.exchange, s)
                writer_ok = self._actuator_writer.init_handles(api.exchange, s)
                if reader_ok and writer_ok:
                    self._handles_ready = True
                else:
                    return

            # Compute cumulative simulation hour relative to run-period start.
            # api.exchange.current_time() returns only the hour of day (0-24),
            # so we combine it with day_of_year to get an absolute offset.
            day = api.exchange.day_of_year(s)
            if self._sim_start_day is None:
                self._sim_start_day = day
            sim_hour = (day - self._sim_start_day) * 24.0 + api.exchange.current_time(s)

            # Check if any queued event should fire now
            event = self._peek_event(sim_hour)
            if event is None:
                return

            # Run the agent loop (non-reentrant)
            if not self._agent_lock.acquire(blocking=False):
                return
            try:
                self._run_agent(api, s, event, sim_hour)
            finally:
                self._agent_lock.release()

        return _callback

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _peek_event(self, sim_hour: float) -> Optional[VPPEvent]:
        """Return the next event if it should fire at sim_hour, else None."""
        try:
            event = self._event_queue.get_nowait()
        except queue.Empty:
            return None

        # If the event has a specific trigger hour and we haven't reached it yet,
        # put it back and wait.
        if event.trigger_hour is not None and sim_hour < event.trigger_hour:
            self._event_queue.put(event)
            return None

        return event

    def _run_agent(self, api, state, event: VPPEvent, sim_hour: float) -> None:
        """Execute the full EnergyBridge agent loop for one VPP event."""
        # Import here to avoid circular imports and to keep the module
        # importable even without langgraph installed.
        from energybridge.agent.graph import build_energybridge_graph
        from energybridge.skills.grid_signal_translator import (
            translate_vpp_context_to_grid_demand,
        )

        # 1. Read current building state from EnergyPlus
        home_state = self._state_reader.read(api.exchange, state)
        # Reflect the last written setpoint so mock_mpc has a baseline
        home_state["hvac_setpoint"] = self._actuator_writer.last_cooling_setpoint

        # 2. Translate VPP context to internal grid demand format
        translated_grid_signal = translate_vpp_context_to_grid_demand(
            event.vpp_context
        )

        # 3. Build initial agent state
        initial_state: dict[str, Any] = {
            "user_input": event.user_input,
            "grid_demand": translated_grid_signal,
            "grid_demand_source": "eplus_env_injection",
            "vpp_context": event.vpp_context,
            "home_state": home_state,
            "translated_grid_signal": translated_grid_signal,
            "memory_path": self.memory_path,
            "log_dir": self.log_dir,
            "trajectory": [],
        }

        # 4. Run the agent graph
        app = build_energybridge_graph()
        result: dict[str, Any] = app.invoke(initial_state)

        # 5. Write control plan back to EnergyPlus
        control_plan = result.get("control_plan", {})
        execution_result = self._actuator_writer.apply(api.exchange, state, control_plan)

        # 6. Store result for the caller
        agent_result = AgentResult(
            sim_hour=sim_hour,
            vpp_context=event.vpp_context,
            home_state=home_state,
            control_plan=control_plan,
            safety_report=result.get("safety_report", {}),
            execution_result=execution_result,
            final_response=result.get("final_response", ""),
            trajectory=result.get("trajectory", []),
        )
        self.agent_results.append(agent_result)

        print(
            f"\n[EplusEnv] VPP event processed at sim_hour={sim_hour:.2f}\n"
            f"  home_state : indoor={home_state.get('indoor_temp')}°C  "
            f"outdoor={home_state.get('outdoor_temp')}°C  "
            f"hvac={home_state.get('hvac_power_kw')} kW\n"
            f"  control    : setpoint={control_plan.get('setpoint')}°C  "
            f"action={control_plan.get('action')}\n"
            f"  execution  : {execution_result.get('status')}  "
            f"written={execution_result.get('written', {})}\n"
            f"  response   : {result.get('final_response', '')[:120]}\n"
        )
