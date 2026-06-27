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

# ------------------------------------------------------------------
# HEMA native framework imports
# ------------------------------------------------------------------
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

    method_id = "hema"

    def __init__(self, city: str, persona_id: str, persona_config: Optional[Dict[str, Any]] = None):
        self.city = city
        self.persona_id = persona_id
        self.persona_config = persona_config or {}
        self._agent = None
        self._bridge = None

    def _lazy_init(self):
        """Create HEMA ReAct Agent with correct API endpoint from .env."""
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

        # HEMA native Control Agent — ALL 10 tools registered
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
        """Single-step decision using HEMA native ReAct Agent."""
        self._lazy_init()

        self._bridge.ensure_device_config(appliance_config or {}, eplus_state)
        query = self._bridge.build_query(
            eplus_state, vpp_event, price_context, current_time,
            persona_config=self.persona_config,
            user_pref=user_pref,
        )

        # Allow the agent to use query tools, but encourage single-round execution
        query += (
            "\n\nCRITICAL: You have access to all HEMA control tools. "
            "Use get_device_list / get_device_status / get_available_actions to query device state if needed. "
            "Use get_device_energy / get_all_devices_energy for energy data. "
            "Use get_automation_rules / get_current_weather / get_utility_rate for context. "
            "Call control_device and schedule_device_action to EXECUTE control actions. "
        )

        import time
        start_time = time.time()
        try:
            result = self._agent.invoke({"messages": [HumanMessage(content=query)]})
            latency = time.time() - start_time

            # 从 LangChain 消息中提取 token usage（若模型支持）
            prompt_tokens = 0
            completion_tokens = 0
            for msg in result.get("messages", []):
                if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                    um = msg.usage_metadata
                    prompt_tokens += um.get("input_tokens", 0)
                    completion_tokens += um.get("output_tokens", 0)

            parsed = self._bridge.extract_actions(result)
            energybridge_actions = self._to_energybridge(parsed, eplus_state, vpp_event)

            energybridge_actions["llm_metrics"] = {
                "latency_seconds": round(latency, 3),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }
            return energybridge_actions
        except Exception as e:
            print(f"  [HEMA ReAct] error at h={current_time.get('sim_h', 0):.1f}: {e}")
            # Fallback to safe defaults
            try:
                from family_runner import (
                    _vpp_safe_appliance_actions,
                    _default_explicit_appliance_actions,
                )
                fallback_actions = (
                    _vpp_safe_appliance_actions(appliance_config, vpp_event)
                    if vpp_event else _default_explicit_appliance_actions(appliance_config)
                )
            except Exception:
                fallback_actions = {}

            return {
                "setpoint": eplus_state.get("current_setpoint_c", 26.0),
                "next_check_hour": None,
                "reason": f"HEMA fallback: {str(e)[:60]}",
                "appliance_actions": fallback_actions,
            }

    def _to_energybridge(
            self,
            parsed: Dict[str, Any],
            eplus_state: Dict[str, Any],
            vpp_event: Optional[Dict[str, Any]],
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
        if parsed.get("water_heater_preheat"):
            appl["water_heater_preheat"] = True
            appl["water_heater_preheat_start_h"] = parsed.get("water_heater_preheat_start_h", 14.0)
            appl["water_heater_preheat_end_h"] = parsed.get("water_heater_preheat_end_h", 18.0)
            appl["water_heater_preheat_temp_c"] = parsed.get("water_heater_preheat_temp_c", 65.0)
        appl["ev_mode"] = str(parsed.get("ev_mode", "smart"))

        next_check = None
        if vpp_event:
            next_check = float(vpp_event.get("end_h", 0.0)) + 0.5

        return {
            "setpoint": setpoint,
            "next_check_hour": next_check,
            "reason": "HEMA Agent",
            "appliance_actions": appl,
        }