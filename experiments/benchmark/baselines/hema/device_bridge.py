"""Bridge EnergyPlus state to HEMA device config and back."""
import math
from typing import Any, Dict, List, Optional

from .path_utils import ensure_hema_imports

ensure_hema_imports()

from agents.tools.control_tools.device_state import (
    reset_device_state,
    load_device_config,
    DEFAULT_DEVICE_CONFIG,
)


def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def _parse_hod(time_str) -> Optional[float]:
    if time_str is None:
        return None
    try:
        return float(time_str)
    except (TypeError, ValueError):
        pass
    try:
        h, m = str(time_str).split(":")
        return float(h) + float(m) / 60.0
    except Exception:
        return None


def _fmt_clock_h(hour: float) -> str:
    """Format an hour-of-day float as HH:MM."""
    h = float(hour) % 24.0
    hh = int(h)
    mm = int(round((h - hh) * 60))
    if mm >= 60:
        hh = (hh + 1) % 24
        mm = 0
    return f"{hh:02d}:{mm:02d}"


class EnergyBridgeToHEMA:
    """Synchronize EnergyBridge simulation state into HEMA's in-memory device config."""

    def __init__(self, city: str, persona_id: str):
        self.city = city
        self.persona_id = persona_id

    def ensure_device_config(self, appliance_config: Dict[str, Any], eplus_state: Dict[str, Any]):
        """Build a minimal HEMA device config from EB appliance config and live state."""
        reset_device_state()

        temp_c = eplus_state.get("zone_air_temp_c", 24.0)
        sp_c = eplus_state.get("current_setpoint_c", 24.0)

        ac_cfg = (appliance_config or {}).get("ac", {})
        ac_mode_from_persona = ac_cfg.get("mode", "")

        devices: Dict[str, Any] = {}

        hvac_mode = "cool" if ac_mode_from_persona == "cooling" else "auto"

        devices["hvac"] = {
            "display_name": "HVAC Thermostat",
            "device_type": "thermostat",
            "manufacturer": "EnergyBridge",
            "model": "Simulated",
            "connection_status": "online",
            "smart_enabled": True,
            "capabilities": ["temperature_control", "mode_control", "scheduling", "heating", "cooling"],
            "current_state": {
                "power": "on",
                "mode": hvac_mode,
                "current_temperature_f": round(_c_to_f(temp_c), 1),
                "target_temperature_f": round(_c_to_f(sp_c), 1),
            },
            "settings": {
                "temperature_range_f": {"min": 60, "max": 85},
                "modes": ["cool", "heat", "auto", "off"],
            },
            "control_actions": [
                {"action": "set_temperature", "params": ["temperature"], "description": "Set target temperature in °F"},
                {"action": "set_mode", "params": ["mode"], "description": "Change HVAC mode"},
                {"action": "on", "params": [], "description": "Turn on"},
                {"action": "off", "params": [], "description": "Turn off"},
                {"action": "away", "params": [], "description": "Enable away mode"},
                {"action": "disable_away", "params": [], "description": "Disable away mode"},
            ],
        }

        # --- Washer ---
        if appliance_config.get("washer", {}).get("present"):
            cfg = appliance_config["washer"]
            devices["washing_machine"] = {
                "display_name": "Washing Machine",
                "device_type": "washer",
                "connection_status": "online",
                "smart_enabled": True,
                "capabilities": ["scheduling", "power_control"],
                "current_state": {"power": "off", "status": "idle", "scheduled_start_time": None},
                "settings": {},
                "control_actions": [
                    {"action": "on", "params": [], "description": "Start washing"},
                    {"action": "off", "params": [], "description": "Stop"},
                    {"action": "set_schedule", "params": ["time"], "description": "Schedule start time HH:MM"},
                ],
                "energy_info": {"rated_power_kw": cfg.get("power_kw", 2.0)},
            }

        # --- Dishwasher ---
        if appliance_config.get("dishwasher", {}).get("present"):
            cfg = appliance_config["dishwasher"]
            devices["dishwasher"] = {
                "display_name": "Dishwasher",
                "device_type": "dishwasher",
                "connection_status": "online",
                "smart_enabled": True,
                "capabilities": ["scheduling", "power_control"],
                "current_state": {"power": "off", "status": "idle", "scheduled_start_time": None},
                "settings": {},
                "control_actions": [
                    {"action": "on", "params": [], "description": "Start dishwasher"},
                    {"action": "off", "params": [], "description": "Stop"},
                    {"action": "set_schedule", "params": ["time"], "description": "Schedule start time HH:MM"},
                ],
                "energy_info": {"rated_power_kw": cfg.get("power_kw", 1.2)},
            }

        # --- Dryer ---
        if appliance_config.get("dryer", {}).get("present"):
            cfg = appliance_config["dryer"]
            devices["clothes_dryer"] = {
                "display_name": "Clothes Dryer",
                "device_type": "dryer",
                "connection_status": "online",
                "smart_enabled": True,
                "capabilities": ["scheduling", "power_control"],
                "current_state": {"power": "off", "status": "idle", "scheduled_start_time": None},
                "settings": {},
                "control_actions": [
                    {"action": "on", "params": [], "description": "Start dryer"},
                    {"action": "off", "params": [], "description": "Stop"},
                    {"action": "set_schedule", "params": ["time"], "description": "Schedule start time HH:MM"},
                ],
                "energy_info": {"rated_power_kw": cfg.get("power_kw", 3.0)},
            }

        # --- Water Heater ---
        if appliance_config.get("water_heater", {}).get("present"):
            cfg = appliance_config["water_heater"]
            devices["water_heater"] = {
                "display_name": "Electric Water Heater",
                "device_type": "water_heater",
                "connection_status": "online",
                "smart_enabled": True,
                "capabilities": ["temperature_control", "scheduling"],
                "current_state": {
                    "power": "on",
                    "mode": "auto",
                },
                "settings": {
                    "temperature_range_f": {"min": 95, "max": 140},
                },
                "control_actions": [
                    {"action": "set_temperature", "params": ["temperature"], "description": "Set tank temperature in °F"},
                    {"action": "set_mode", "params": ["mode"], "description": "Set mode"},
                    {"action": "set_schedule", "params": ["time"], "description": "Schedule heating window start HH:MM"},
                    {"action": "on", "params": [], "description": "Start heating"},
                    {"action": "off", "params": [], "description": "Stop heating"},
                ],
                "energy_info": {"rated_power_kw": cfg.get("rated_kw", 3.0)},
            }

        # --- EV Charger ---
        if appliance_config.get("ev", {}).get("present"):
            cfg = appliance_config["ev"]
            devices["ev_charger"] = {
                "display_name": "EV Charger",
                "device_type": "ev_charger",
                "connection_status": "online",
                "smart_enabled": True,
                "capabilities": ["charging_control", "mode_control", "scheduling"],
                "current_state": {
                    "power": "standby",
                    "vehicle_connected": True,
                    "charging_status": "scheduled",
                    "max_charge_rate_kw": cfg.get("charger_kw", 7.0),
                },
                "settings": {},
                "control_actions": [
                    {"action": "start_charging", "params": [], "description": "Start charging now"},
                    {"action": "stop_charging", "params": [], "description": "Stop charging"},
                    {"action": "set_charge_limit", "params": ["limit"], "description": "Set charge limit %"},
                    {"action": "set_schedule", "params": ["time"], "description": "Schedule charging start HH:MM"},
                ],
                "energy_info": {"rated_power_kw": cfg.get("charger_kw", 7.0)},
            }

        # Overwrite global HEMA device state
        import agents.tools.control_tools.device_state as _ds
        _ds._device_state = {
            "home_id": "energybridge",
            "home_name": "EnergyBridge Family Home",
            "devices": devices,
            "automation_rules": [],
            "tou_integration": {},
        }
        _ds._config_path = DEFAULT_DEVICE_CONFIG

    def build_query(
            self,
            eplus_state: Dict[str, Any],
            vpp_event: Optional[Dict[str, Any]],
            price_context: Optional[Dict[str, Any]],
            current_time: Dict[str, Any],
            persona_config: Optional[Dict[str, Any]] = None,
            user_pref: str = "",
    ) -> str:
        """
        Construct a natural-language prompt for the HEMA Control Agent.
        Uses only observable structured persona fields, not role-play prompts.
        """
        temp_c = eplus_state.get("zone_air_temp_c", 24.0)
        out_t_c = eplus_state.get("outdoor_temp_c", 30.0)
        sp_c = eplus_state.get("current_setpoint_c", 24.0)
        sim_h = current_time.get("sim_h", 0.0)
        hod = current_time.get("hod", 0.0)
        day = int(sim_h // 24) + 1

        # ── Parse persona config with full schema ──
        persona_config = persona_config or {}
        tags = persona_config.get("tags", {}) or {}
        preferences = persona_config.get("preferences", {}) or {}
        schedule = persona_config.get("schedule", {}) or {}
        appliances = persona_config.get("appliances", {}) or {}
        # Scoring weights
        scoring_weights = preferences.get("scoring_weights", {})

        # AC config from persona
        ac_cfg = appliances.get("ac", {}) or {}

        lines: List[str] = []

        # ═══════════════════════════════════════════════════════
        # SECTION 2: Quantitative Preferences & Weights
        # ═══════════════════════════════════════════════════════
        lines += [
            "[Observable User Context]",
            f"Simulation time: Day {day}, {int(hod):02d}:00 (sim_h={sim_h:.1f}).",
            f"Indoor temperature: {temp_c:.1f}°C ({_c_to_f(temp_c):.1f}°F).",
            f"Outdoor temperature: {out_t_c:.1f}°C ({_c_to_f(out_t_c):.1f}°F).",
            f"Current AC setpoint: {sp_c:.1f}°C ({_c_to_f(sp_c):.1f}°F).",
        ]

        if scoring_weights:
            comfort_w = float(scoring_weights.get("comfort", 0.33))
            energy_w = float(scoring_weights.get("energy", 0.33))
            vpp_w = float(scoring_weights.get("vpp", 0.34))
            lines += [
                f"Comfort priority: {comfort_w:.2f} (higher = never sacrifice comfort)",
                f"Energy saving priority: {energy_w:.2f} (higher = actively seek savings)",
                f"VPP / grid support priority: {vpp_w:.2f} (higher = willing to curtail for grid)",
            ]
        else:
            lines += ["(No explicit weights provided; use moderate balanced approach.)"]

        # ═══════════════════════════════════════════════════════
        # SECTION 3: Behavioral Tags (structured with descriptions)
        # ═══════════════════════════════════════════════════════
        lines += ["[User Behavioral Tags]"]
        tag_descriptions = {
            "schedule": {
                "regular_commuter": "Regular weekday schedule; away during day, home evenings",
                "stay_at_home": "Home most of the day; occupancy is high",
                "irregular": "Highly variable routine; do not rely on historical patterns",
                "caregiver": "Caregiving responsibilities; stability and safety critical",
            },
            "comfort": {
                "temp_sensitive": "Highly sensitive to temperature changes (±0.5°C noticeable)",
                "normal_comfort": "Standard comfort expectations",
                "temp_tolerant": "Willing to accept wider temperature ranges for savings",
            },
            "task": {
                "rigid": "Task timing is fixed; do NOT reschedule household tasks",
                "semi_rigid": "Some flexibility but prefer to keep routine",
                "flexible": "Tasks can be freely rescheduled within bounds",
                "ev_constrained": "EV charging has hard SOC/departure deadlines",
            },
            "price": {
                "price_sensitive": "Motivated by cost savings; quantify benefits",
                "low_incentive": "Small savings do NOT motivate behavior change",
                "price_indifferent": "Cost is not a decision factor",
                "needs_explanation": "Wants clear justification before any action",
            },
            "control": {
                "high_trust_auto": "Fully delegates to automation; do NOT ask for confirmation",
                "suggestion_first": "Present suggestion but let user decide",
                "confirm_required": "MUST ask before every adjustment",
                "low_auto_accept": "Minimal automation; prefers manual control",
                "privacy_sensitive": "Avoids data sharing; minimal intrusion",
            },
            "grid_value": {
                "stable_flex": "Reliable DR resource; good for aggregation",
                "evening_peak": "High evening peak contribution; good shift target",
                "short_peak_cut": "Only brief peak cuts are acceptable",
                "low_value": "Not suitable as DR target; focus on comfort",
                "uncertain_flex": "Flexibility is unpredictable; plan conservatively",
            },
        }

        for tag_key in ("schedule", "comfort", "task", "price", "control", "grid_value"):
            tag_val = tags.get(tag_key)
            if tag_val:
                desc = tag_descriptions.get(tag_key, {}).get(tag_val, "")
                lines.append(f"{tag_key}: {tag_val}" + (f" — {desc}" if desc else ""))
        lines.append("")

        # ═══════════════════════════════════════════════════════
        # SECTION 4: Detailed Schedule Context
        # ═══════════════════════════════════════════════════════
        lines += ["[Daily Schedule Context]"]
        sched_parts = []
        wake_h = schedule.get("wake_h")
        leaves_h = schedule.get("leaves_home_h")
        returns_h = schedule.get("returns_home_h")
        bath_h = schedule.get("bath_shower_h")
        sleep_h = schedule.get("sleep_h")

        if wake_h is not None:
            sched_parts.append(f"wake {_fmt_clock_h(wake_h)}")
        if leaves_h is not None:
            sched_parts.append(f"leaves home {_fmt_clock_h(leaves_h)}")
        if returns_h is not None:
            sched_parts.append(f"returns home {_fmt_clock_h(returns_h)}")
        if bath_h is not None:
            sched_parts.append(f"bath/shower {_fmt_clock_h(bath_h)}")
        if sleep_h is not None:
            sched_parts.append(f"sleep {_fmt_clock_h(sleep_h)}")

        if sched_parts:
            lines.append("  " + ", ".join(sched_parts))

        # Extended schedule info
        occ_pattern = schedule.get("occupancy_pattern")
        if occ_pattern:
            lines.append(f"  Occupancy pattern: {occ_pattern}")

        variability = schedule.get("schedule_variability_h")
        if variability is not None:
            lines.append(f"  Schedule variability: ±{float(variability):.1f}h (higher = less predictable)")

        wfh_days = schedule.get("work_from_home_days_per_week")
        if wfh_days is not None:
            lines.append(f"  Work-from-home days: {int(wfh_days)}/week")

        travel_days = schedule.get("travel_days_per_month")
        if travel_days is not None:
            lines.append(f"  Travel days: {int(travel_days)}/month (may be away unexpectedly)")

        weekend_leaves = schedule.get("weekend_leaves_h")
        weekend_returns = schedule.get("weekend_returns_h")
        if weekend_leaves is not None and weekend_returns is not None:
            lines.append(f"  Weekend: leaves {_fmt_clock_h(weekend_leaves)}, returns {_fmt_clock_h(weekend_returns)}")

        lines.append("")

        # Vulnerable members
        vulnerable = schedule.get("vulnerable_members", [])
        if vulnerable:
            lines += [
                f"[Vulnerable Members] {', '.join(vulnerable)}.",
                "SAFETY AND COMFORT TAKE ABSOLUTE PRIORITY over energy savings.",
                "Any action that could affect vulnerable members requires explicit user confirmation.",
                "",
            ]

        # ═══════════════════════════════════════════════════════
        # SECTION 5: AC Comfort Bounds (from persona appliances.ac)
        # ═══════════════════════════════════════════════════════
        if ac_cfg:
            min_c = float(ac_cfg.get("setpoint_preferred_min_c", 24.0))
            max_c = float(ac_cfg.get("setpoint_preferred_max_c", 26.0))
            tol_c = float(ac_cfg.get("temp_tolerance_c", 1.0))
            ac_mode = ac_cfg.get("mode", "cooling")

            lines += [
                "[AC Comfort Configuration]",
                f"  Mode: {ac_mode}",
                f"  STRICT comfort bounds: {min_c:.1f}°C – {max_c:.1f}°C",
                f"  Temperature tolerance: ±{tol_c:.1f}°C",
                f"  NEVER set thermostat below {min_c:.1f}°C or above {max_c:.1f}°C.",
            ]

            # Mode-specific guidance
            if ac_mode == "cooling":
                lines.append("  Current season: COOLING. Setpoint adjustments should RAISE temperature to save energy.")

            lines.append("")

        # ═══════════════════════════════════════════════════════
        # SECTION 6: VPP Event Context
        # ═══════════════════════════════════════════════════════
        lines += [
            "",
            "You are the home energy controller. Optimize energy while respecting the user profile above.",
        ]

        if vpp_event:
            vpp_start = float(vpp_event.get("trigger_h", 18.0)) % 24.0
            vpp_end = float(vpp_event.get("end_h", 19.0)) % 24.0
            demand_kw = eplus_state.get("vpp_demand_kw", 0.0)

            # Check if VPP overlaps with return-home or occupancy
            return_home_sensitive = False
            if returns_h is not None:
                ret_h = float(returns_h) % 24.0
                if (vpp_start - 0.5) <= ret_h <= (vpp_end + 0.5):
                    return_home_sensitive = True

            vpp_lines = [
                "",
                f"═══════════════════════════════════════════════════════",
                f"VPP EVENT: Window {int(vpp_start):02d}:00-{int(vpp_end):02d}:00 (Day {day})",
                f"Grid target: reduce load by ~{demand_kw:.2f} kW during this window.",
            ]

            if return_home_sensitive:
                vpp_lines += [
                    "⚠️  CRITICAL: This VPP window overlaps with user return-home time.",
                    "   → PRIORITIZE comfort restoration immediately after VPP ends.",
                    "   → Do NOT let indoor temperature drift uncomfortably high before arrival.",
                ]

            vpp_lines += [
                "Actions available: set_temperature (°F), set_schedule (HH:MM), set_mode.",
                f"═══════════════════════════════════════════════════════",
            ]
            lines += vpp_lines
        else:
            lines += [
                "",
                "No active VPP event. Maintain normal comfort and efficiency.",
            ]

        # ═══════════════════════════════════════════════════════
        # SECTION 7: Appliances — Full Detail with Controllability
        # ═══════════════════════════════════════════════════════
        lines += ["", "[Appliances — Present and Controllable]"]

        for name in ("washer", "dishwasher", "dryer", "water_heater", "ev"):
            dev = appliances.get(name, {}) or {}
            if not dev.get("present"):
                continue

            shift = dev.get("shiftable", True)
            dr = dev.get("dr_adjustable", True)
            power_kw = dev.get("power_kw", dev.get("rated_kw", dev.get("charger_kw", 0.0)))

            if name in ("washer", "dishwasher", "dryer"):
                earliest = dev.get("earliest_h")
                latest = dev.get("latest_h")
                preferred = dev.get("preferred_h")
                duration = dev.get("duration_h")
                time_info = f" preferred={_fmt_clock_h(preferred)}, duration={duration}h" if preferred else ""
                bounds = f" [{_fmt_clock_h(earliest)}–{_fmt_clock_h(latest)}]" if earliest is not None and latest is not None else ""

                if not shift and not dr:
                    lines += [f"- {name}: {power_kw}kW, FIXED schedule (do not reschedule){time_info}{bounds}"]
                elif not dr:
                    lines += [f"- {name}: {power_kw}kW, not DR-adjustable (do not reschedule){time_info}{bounds}"]
                else:
                    lines += [f"- {name}: {power_kw}kW, CONTROLLABLE — can shift to avoid VPP{time_info}{bounds}"]

            elif name == "water_heater":
                bath_req = dev.get("bath_required_h")
                ph_start = dev.get("pre_heat_window_start_h")
                ph_end = dev.get("pre_heat_window_end_h")
                bath_info = f", must be ready by {_fmt_clock_h(bath_req)}" if bath_req else ""
                ph_info = f", preheat window [{_fmt_clock_h(ph_start)}–{_fmt_clock_h(ph_end)}]" if ph_start and ph_end else ""

                if not dr:
                    lines += [f"- {name}: {power_kw}kW, FIXED preheat schedule{bath_info}{ph_info}"]
                else:
                    lines += [f"- {name}: {power_kw}kW, CONTROLLABLE — can adjust preheat timing{bath_info}{ph_info}"]

            elif name == "ev":
                charger_kw = dev.get("charger_kw", 7.0)
                capacity = dev.get("capacity_kwh", 60.0)
                target_soc = dev.get("target_soc", 0.8)
                min_soc = dev.get("min_soc", 0.2)
                arrival = dev.get("arrival_h")
                departure = dev.get("departure_h")
                daily_drive = dev.get("daily_drive_kwh", 0.0)

                lines += [
                    f"- {name}: {charger_kw}kW charger, {capacity}kWh battery",
                    f"  Target SOC: {target_soc:.0%}, minimum: {min_soc:.0%}, daily consumption: {daily_drive}kWh",
                ]
                if arrival is not None and departure is not None:
                    lines += [f"  Arrival: {_fmt_clock_h(arrival)}, Departure: {_fmt_clock_h(departure)}"]
                lines += [f"  CONTROLLABLE — smart/delay/normal modes available"]

        lines.append("")

        # ═══════════════════════════════════════════════════════
        # SECTION 8: User Real-Time Input
        # ═══════════════════════════════════════════════════════
        if user_pref:
            lines += ["", f"[User says NOW] {user_pref}", ""]

        # ═══════════════════════════════════════════════════════
        # SECTION 9: Price Context
        # ═══════════════════════════════════════════════════════
        if price_context and price_context.get("has_price"):
            pt = price_context.get("price_text", "")
            if pt:
                lines += [pt, ""]
            else:
                lines += ["Day-ahead price data is available.", ""]

        return "\n".join(lines)

    def _list_active_devices(self) -> List[str]:
        config = load_device_config()
        return [d.get("display_name", k) for k, d in config.get("devices", {}).items()]

    def extract_actions(self, agent_result: Dict[str, Any]) -> Dict[str, Any]:
        """Parse HEMA agent tool calls into EnergyBridge control intent."""
        from langchain_core.messages import AIMessage

        setpoint_f = None
        washer_start = None
        dishwasher_start = None
        dryer_start = None
        wh_preheat = False
        wh_start = None
        wh_end = None
        wh_temp_f = None
        ev_mode = "smart"
        ev_charge_start = None
        ev_charge_end = None

        for msg in agent_result.get("messages", []):
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("name", "")
                    args = tc.get("args", {})
                    action = str(args.get("action", "")).lower().replace(" ", "_")
                    value = args.get("value", "")
                    time_str = args.get("time", "")

                    _raw = str(args.get("device_name", "")).lower().replace(" ", "_")
                    if "water" in _raw and "heater" in _raw:
                        dev = "water_heater"
                    elif "dishwasher" in _raw:
                        dev = "dishwasher"
                    elif "washer" in _raw or "washing" in _raw or "laundry" in _raw:
                        dev = "washer"
                    elif "dryer" in _raw:
                        dev = "dryer"
                    elif "ev" in _raw or "charger" in _raw or "tesla" in _raw or "car" in _raw:
                        dev = "ev"
                    elif "hvac" in _raw or "thermostat" in _raw or _raw in ("ac", "heat", "heater"):
                        dev = "hvac"
                    else:
                        dev = _raw

                    if name == "control_device":
                        if dev in ("hvac", "thermostat", "ac", "heat", "heater"):
                            if action in ("set_temperature", "set_temp", "temperature"):
                                try:
                                    setpoint_f = float(
                                        str(value).replace("°", "").replace("F", "").replace("f", "").replace("C","").replace("c", "").strip())
                                except Exception:
                                    pass

                        elif dev in ("washing_machine", "washer", "laundry", "Washing Machine"):
                            if action in ("set_schedule", "schedule", "on", "start"):
                                try:
                                    washer_start = float(
                                        str(value).replace("°", "").replace("F", "").replace("f", "").replace("C","").replace("c", "").strip())
                                except Exception:
                                    pass

                        elif dev in ("dishwasher", "Dishwasher"):
                            if action in ("set_schedule", "schedule", "on", "start"):
                                try:
                                    dishwasher_start = float(
                                        str(value).replace("°", "").replace("F", "").replace("f", "").replace("C","").replace("c", "").strip())
                                except Exception:
                                    pass

                        elif dev in ("clothes_dryer", "dryer"):
                            if action in ("set_schedule", "schedule", "on", "start"):
                                try:
                                    dryer_start = float(
                                        str(value).replace("°", "").replace("F", "").replace("f", "").replace("C","").replace("c", "").strip())
                                except Exception:
                                    pass

                        elif dev in ("water_heater", "hot_water", "boiler", "Electric Water Heater"):
                            if action in ("set_temperature", "set_temp", "temperature"):
                                try:
                                    wh_temp_f = float(
                                        str(value).replace("°", "").replace("F", "").replace("f", "").replace("C","").replace("c", "").strip())
                                    wh_preheat = True
                                except Exception:
                                    pass
                            elif action in ("set_schedule", "schedule"):
                                try:
                                    wh_start = float(
                                        str(value).replace("°", "").replace("F", "").replace("f", "").replace("C","").replace("c", "").strip())
                                    wh_end = (wh_start + 4.0) % 24.0 if wh_start is not None else None
                                    wh_preheat = True
                                except Exception:
                                    pass
                            elif action in ("on", "start"):
                                time_val = value or time_str
                                wh_start = _parse_hod(time_val)
                                wh_end = (wh_start + 4.0) % 24.0 if wh_start is not None else None
                                wh_preheat = True

                    elif name == "schedule_device_action":
                        if dev in ("washing_machine", "washer", "laundry", "Washing Machine"):
                            washer_start = _parse_hod(time_str)
                        elif dev in ("dishwasher", "Dishwasher"):
                            dishwasher_start = _parse_hod(time_str)
                        elif dev in ("clothes_dryer", "dryer", "Clothes Dryer"):
                            dryer_start = _parse_hod(time_str)


                        elif dev in ("water_heater", "hot_water", "boiler", "Electric Water Heater"):
                            action_str = str(action).lower().replace(" ", "_")
                            if action_str in ("start", "begin", "on"):
                                wh_start = _parse_hod(time_str)
                                wh_preheat = True
                            elif action_str in ("stop", "end", "off"):
                                wh_end = _parse_hod(time_str)
                                wh_preheat = True
                            elif action_str in ("set_temperature", "set_temp", "temperature"):
                                try:
                                    if value is None:
                                        wh_temp_f = float(time_str)
                                    else:
                                        temp_str = str(value).replace("°", "").replace("F", "").replace("f","").replace("C","").replace("c", "").strip()
                                        wh_temp_f = float(temp_str)
                                    wh_preheat = True
                                except (ValueError, TypeError):
                                    pass

                        elif dev in ("ev_charger", "electric_vehicle", "ev", "tesla_charger", "car_charger", "EV Charger"):
                            action_str = str(action).lower().replace(" ", "_")
                            if action_str in ("start", "start_charging", "begin_charging"):
                                ev_charge_start = _parse_hod(time_str)
                            elif action_str in ("stop", "stop_charging", "end_charging"):
                                ev_charge_end = _parse_hod(time_str)
                            elif action_str in ("set_schedule", "schedule"):
                                sched_time = _parse_hod(time_str)
                                if sched_time is not None:
                                    value_str = str(value).lower().replace(" ", "_")
                                    if value_str in ("start", "start_charging", "begin_charging"):
                                        ev_charge_start = sched_time
                                    elif value_str in ("stop", "stop_charging", "end_charging"):
                                        ev_charge_end = sched_time
                                    else:
                                        ev_charge_start = sched_time

        return {
            "setpoint_f": setpoint_f,
            "washer_start_h": washer_start,
            "dishwasher_start_h": dishwasher_start,
            "dryer_start_h": dryer_start,
            "water_heater_preheat": wh_preheat,
            "water_heater_preheat_start_h": wh_start,
            "water_heater_preheat_end_h": wh_end if wh_end is not None else (wh_start + 4.0) % 24.0 if wh_start else 18.0,
            "water_heater_preheat_temp_c": round(_f_to_c(wh_temp_f), 1) if wh_temp_f is not None else 65.0,
            "ev_mode": ev_mode,
            "ev_charge_start_h": ev_charge_start,
            "ev_charge_end_h": ev_charge_end,
        }
