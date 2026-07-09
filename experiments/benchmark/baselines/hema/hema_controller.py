"""HEMA Control Agent baseline for EnergyBridge — Native HEMA ReAct."""
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_HEMA_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent / "HEMA"
_HEMA_STR = str(_HEMA_ROOT)

if _HEMA_STR not in sys.path:
    sys.path.insert(0, _HEMA_STR)

if 'agents' in sys.modules:
    _agents_file = str(getattr(sys.modules['agents'], '__file__', ''))
    if 'HEMA' not in _agents_file.replace('\\', '/'):
        for _k in list(sys.modules.keys()):
            if _k == 'agents' or _k.startswith('agents.'):
                del sys.modules[_k]

from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from agents.prompts import CONTROL_AGENT_SYSTEM_PROMPT
from agents.tools.control_tools import (
    control_device,
    schedule_device_action,
    get_device_status,
    get_available_actions,
    get_device_list,
    get_automation_rules,
    get_device_energy,
    get_all_devices_energy,
)
from agents.tools.analysis_tools import get_utility_rate
from agents.tools.knowledge_tools import get_current_weather
from .device_bridge import EnergyBridgeToHEMA

# ------------------------------------------------------------------
# Read EnergyBridge .env config (fixes base_url and model)
# ------------------------------------------------------------------
_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY") or ""
if not _API_KEY:
    _pool = os.getenv("LLM_API_KEY_POOL", "")
    if _pool:
        _API_KEY = _pool.split(",")[0]

_BASE_URL = (
        os.getenv("LLM_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
)
_MODEL = (
        os.getenv("LLM_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "gpt-5.4-mini"
)


class HEMAControlBaseline:
    """EnergyBridge baseline using HEMA's native ReAct Control Agent."""
    def __init__(self, city: str, persona_id: str, persona_config: Optional[Dict[str, Any]] = None):
        self.city = city
        self.persona_id = persona_id
        self.persona_config = persona_config or {}
        self._agent = None
        self._bridge = None
        self._max_retries = 5

    def _lazy_init(self):
        if self._agent is not None:
            return

        # Re-verify agents cache points to HEMA
        if 'agents' in sys.modules:
            _af = str(getattr(sys.modules['agents'], '__file__', ''))
            if 'HEMA' not in _af.replace('\\', '/'):
                for _k in list(sys.modules.keys()):
                    if _k == 'agents' or _k.startswith('agents.'):
                        del sys.modules[_k]

        llm = ChatOpenAI(
            api_key=_API_KEY,
            base_url=_BASE_URL,
            model=_MODEL,
            temperature=0.2,
            timeout=300,
        )

        # HEMA native Control Agent
        tools = [
            control_device,
            schedule_device_action,
            get_device_status,
            get_available_actions,
            get_device_list,
            get_automation_rules,
            get_device_energy,
            get_all_devices_energy,
            get_utility_rate,
            get_current_weather,
        ]

        # HEMA native ReAct agent (LangGraph)
        self._agent = create_react_agent(
            llm,
            tools,
            prompt=CONTROL_AGENT_SYSTEM_PROMPT,
        )

        self._bridge = EnergyBridgeToHEMA(self.city, self.persona_id)

    def decide(
            self,
            current_time: Dict[str, Any],
            eplus_state: Dict[str, Any],
            vpp_event: Optional[Dict[str, Any]] = None,
            price_context: Optional[Dict[str, Any]] = None,
            appliance_config: Optional[Dict[str, Any]] = None,
            user_pref: str = "",
    ) -> Dict[str, Any]:
        self._lazy_init()
        self._bridge.ensure_device_config(appliance_config or {}, eplus_state)

        return self._decide_with_retry(
            current_time=current_time,
            eplus_state=eplus_state,
            vpp_event=vpp_event,
            price_context=price_context,
            appliance_config=appliance_config,
            user_pref=user_pref,
            retry_count=0,
            missing_appliances=[],
        )

    def _decide_with_retry(
            self,
            current_time, eplus_state, vpp_event, price_context,
            appliance_config, user_pref,
            retry_count: int,
            missing_appliances: list,
    ):

        query = self._bridge.build_query(
            eplus_state, vpp_event, price_context, current_time,
            persona_config=self.persona_config,
            user_pref=user_pref,
        )

        query += (
            "\n\nCRITICAL: You have access to all HEMA control tools. "
            "Use get_device_list / get_device_status / get_available_actions to query device state if needed. "
            "Use get_device_energy / get_all_devices_energy for energy data. "
            "Use get_automation_rules / get_current_weather / get_utility_rate for context. "
            "Call control_device and schedule_device_action to EXECUTE control actions. "
        )
        query += (
            "\n\nCRITICAL MANDATORY ACTIONS: "
            "If the following appliances are present in this home and "
            "MUST each receive an explicit control command in your response. You CANNOT skip any of them:\n"
            "- washing_machine: MUST schedule a start time today using schedule_device_action. "
            "   Choose a time outside the VPP window (18:00-19:00). Use 24-hour format like '08:00'.\n"
            "- dishwasher: MUST schedule a start time today using schedule_device_action. "
            "   Choose a time outside the VPP window (18:00-19:00). Use 24-hour format like '09:00'.\n"
            "- clothes_dryer: MUST schedule a start time today using schedule_device_action. "
            "   Choose a time outside the VPP window (18:00-19:00). Use 24-hour format like '10:00'.\n"
            "- water_heater: MUST set a preheat schedule using schedule_device_action. "
            "   Set start time with action 'start', end time with action 'stop', and temperature with action 'set_temperature'. Must finish end time before the VPP window (18:00-19:00)\n"
            "- ev_charger: MUST set the charging schedule. "
            "   Ensure the charging window greater or equal to Minimum charging hours to reach target_soc. "
            "  EV CHARGING TIME CALCULATION GUIDE:\n"
            "  - Battery capacity: {capacity_kwh} kWh\n"
            "  - Charger power: {charger_kw} kW\n"
            "  - Target SOC: {target_soc}\n"
            "  - Required charge = target_soc * capacity\n"
            "  - Minimum charging hours = required_charge / charger_power\n"
            "   Use schedule_device_action to set start time with action 'start' and stop time with action 'stop' separately. Choose a time outside the VPP window (18:00-19:00)."
            "   start charging as soon as VPP_WINDOW ends."
            "   start time and stop time must be after arrival_h of the first day and before departure_h of the second day. \n"
            "If you fail to emit commands for any present device, the system will report failure and user satisfaction will be 1/5."
        )

        if missing_appliances:
            query += (
                f"Retry {retry_count}/{self._max_retries}. You MUST emit tool calls for these devices if present."
            )

        import time
        start_time = time.time()

        result = self._agent.invoke({"messages": [HumanMessage(content=query)]})
        latency = time.time() - start_time

        for msg in result.get("messages", []):
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                print(f"\n  [HEMA RAW TOOL_CALLS] sim_h={current_time.get('sim_h', 0):.1f}")
                for tc in msg.tool_calls:
                    print(f"    -> name={tc.get('name')}, args={tc.get('args')}")

        prompt_tokens = 0
        completion_tokens = 0
        for msg in result.get("messages", []):
            if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                um = msg.usage_metadata
                prompt_tokens += um.get("input_tokens", 0)
                completion_tokens += um.get("output_tokens", 0)

        parsed = self._bridge.extract_actions(result)

        cfg = appliance_config or {}
        present = set()
        for name in ("washer", "dishwasher", "dryer", "water_heater", "ev"):
            if cfg.get(name, {}).get("present"):
                present.add(name)

        missing = []
        for name in ("washer", "dishwasher", "dryer"):
            if name in present:
                start_key = f"{name}_start_h"
                skip_key = f"{name}_skip"
                if parsed.get(start_key) is None:
                    missing.append(name)

        if "water_heater" in present:
            if parsed.get("water_heater_preheat_start_h") is None or parsed.get("water_heater_preheat_end_h") is None:
                missing.append("water_heater")
        if "ev" in present:
            if parsed.get("ev_charge_start_h") is None or parsed.get("ev_charge_end_h") is None:
                missing.append("ev")

        if missing and retry_count < self._max_retries:
            print(f"  [HEMA Retry {retry_count + 1}/{self._max_retries}] Missing: {missing}")
            return self._decide_with_retry(
                current_time=current_time,
                eplus_state=eplus_state,
                vpp_event=vpp_event,
                price_context=price_context,
                appliance_config=appliance_config,
                user_pref=user_pref,
                retry_count=retry_count + 1,
                missing_appliances=missing,
            )
        elif missing:
            print(f"  [HEMA Retry] Max retries reached. Still missing: {missing}")

        energybridge_actions = self._to_energybridge(parsed, eplus_state, vpp_event, appliance_config)
        energybridge_actions["llm_metrics"] = {
            "latency_seconds": round(latency, 3),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
        return energybridge_actions

    def _to_energybridge(
            self,
            parsed: Dict[str, Any],
            eplus_state: Dict[str, Any],
            vpp_event: Optional[Dict[str, Any]],
            appliance_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Convert parsed HEMA actions to EnergyBridge format."""
        default_sp = eplus_state.get("current_setpoint_c", 24.0)
        sp_f = parsed.get("setpoint_f")
        if sp_f is not None and isinstance(sp_f, (int, float)) and not math.isnan(sp_f):
            setpoint = round((sp_f - 32.0) * 5.0 / 9.0, 1)
        else:
            setpoint = default_sp

        appl: Dict[str, Any] = {}
        if parsed.get("washer_start_h") is not None:
            appl["washer_start_h"] = float(parsed["washer_start_h"])
            appl["washer_skip"] = False
        if parsed.get("dishwasher_start_h") is not None:
            appl["dishwasher_start_h"] = float(parsed["dishwasher_start_h"])
            appl["dishwasher_skip"] = False
        if parsed.get("dryer_start_h") is not None:
            appl["dryer_start_h"] = float(parsed["dryer_start_h"])
            appl["dryer_skip"] = False
        if parsed.get("water_heater_preheat") is not None:
            appl["water_heater_preheat"] = True
            appl["water_heater_preheat_start_h"] = float(parsed["water_heater_preheat_start_h"])
            appl["water_heater_preheat_end_h"] = float(parsed["water_heater_preheat_end_h"])
            appl["water_heater_preheat_temp_c"] = float(parsed["water_heater_preheat_temp_c"])
        if parsed.get("ev_charge_start_h") is not None:
            appl["ev_charge_start_h"] = float(parsed["ev_charge_start_h"])
            appl["ev_mode"] = "smart"
        if parsed.get("ev_charge_end_h") is not None:
            appl["ev_charge_end_h"] = float(parsed["ev_charge_end_h"])

        next_check = None
        if vpp_event:
            next_check = float(vpp_event.get("end_h", 0.0)) + 0.5

        return {
            "setpoint": setpoint,
            "next_check_hour": next_check,
            "reason": "HEMA Agent",
            "appliance_actions": appl,
        }