"""Small, dependency-free helpers for preserving HEMA assistant explanations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _message_role(message: Any) -> str:
    return str(
        getattr(message, "type", "")
        or getattr(message, "role", "")
        or (message.get("role", "") if isinstance(message, Mapping) else "")
    ).strip().lower()


def _message_tool_calls(message: Any) -> Any:
    if isinstance(message, Mapping):
        return message.get("tool_calls")
    return getattr(message, "tool_calls", None)


def extract_assistant_explanation(result: Mapping[str, Any] | None, *, limit: int = 1200) -> str:
    """Return the last non-empty assistant text from a LangGraph result.

    HEMA expresses device commands as tool calls and then writes a concise
    natural-language explanation.  The control adapter must preserve that final
    text instead of replacing it with a method label.
    """
    messages = (result or {}).get("messages") or []
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return ""

    last_tool_index = -1
    for index, message in enumerate(messages):
        if _message_role(message) == "tool" or _message_tool_calls(message):
            last_tool_index = index

    # A final explanation must follow the last tool interaction. If execution
    # stopped at a tool-call preamble, do not promote that technical aside.
    for message in reversed(messages[last_tool_index + 1 :]):
        role = _message_role(message)
        class_name = message.__class__.__name__.lower()
        if role not in {"ai", "assistant"} and "aimessage" not in class_name:
            continue
        if _message_tool_calls(message):
            continue
        content = (
            getattr(message, "content", None)
            if not isinstance(message, Mapping)
            else message.get("content")
        )
        pieces: list[str] = []
        if isinstance(content, str):
            pieces.append(content)
        elif isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
            for block in content:
                if isinstance(block, str):
                    pieces.append(block)
                elif isinstance(block, Mapping) and str(block.get("type", "")).lower() in {
                    "text",
                    "output_text",
                }:
                    pieces.append(str(block.get("text") or block.get("content") or ""))
        text = " ".join(" ".join(pieces).split())
        if text:
            return text[: max(1, int(limit))].rstrip()
    return ""


def explanation_output_fields(explanation: str, *, adaptive_v2: bool) -> dict[str, Any]:
    """Keep frozen HEMA output in legacy and expose native text only in V2."""
    if not adaptive_v2:
        return {"reason": "HEMA Agent"}
    text = " ".join(str(explanation or "").split())
    if not text:
        return {"reason": "The listed household device schedule is ready for review."}
    return {
        "reason": text,
        "strategy_explanation": {"natural_language": text},
    }


def schedule_prompt_fields(
    vpp_event: Mapping[str, Any] | None,
    *,
    adaptive_v2: bool,
) -> dict[str, str]:
    """Return profile-isolated HEMA scheduling guidance.

    Paper/legacy runs retain their historical prompt verbatim.  V2 uses the
    actual event window and describes missing commands as an observable plan
    gap, never as a predetermined satisfaction score.
    """
    if not adaptive_v2:
        return {
            "water_heater": (
                "For ordinary day-ahead planning, keep hot water ready through the early evening rather than "
                "minimizing cost. For active VPP requests, try to finish before 18:00 only if it does not require "
                "a disruptive rebuild."
            ),
            "ev": (
                "Choose a time outside the VPP window (18:00-19:00)."
                "   start charging as soon as VPP_WINDOW ends."
            ),
            "missing_commands": (
                "If you fail to emit commands for any present device, the system will report failure and user "
                "satisfaction will be 1/5."
            ),
            "event_check": "",
        }

    neutral = {
        "water_heater": (
            "For ordinary day-ahead planning, keep hot water ready through the early evening rather than "
            "minimizing cost."
        ),
        "ev": "Choose a charging interval within the supplied arrival and departure constraints.",
        "missing_commands": (
            "If a present device has no explicit command, the returned plan is incomplete; report that gap "
            "instead of claiming full appliance coverage."
        ),
        "event_check": "",
    }
    if not vpp_event:
        return neutral

    try:
        start = float(vpp_event.get("trigger_h", 18.0)) % 24.0
        end = float(vpp_event.get("end_h", 19.0)) % 24.0
    except (TypeError, ValueError):
        start, end = 18.0, 19.0
    window = f"[{start:.2f}, {end:.2f})"
    neutral.update({
        "water_heater": (
            "For this active request, either stop adjustable water-heater preheating by "
            f"{start:.2f} or begin it at/after {end:.2f}, choosing the option that still meets hot-water readiness."
        ),
        "ev": (
            f"Choose a charging interval that does not overlap the half-open VPP window {window}. "
            f"When service constraints allow, begin at/after {end:.2f}."
        ),
        "event_check": (
            "For every adjustable device, the executable interval must not overlap the half-open VPP window "
            f"{window}. If a conflict cannot be resolved while preserving service, state that truthfully instead "
            "of claiming the device was moved outside the event."
        ),
    })
    return neutral
