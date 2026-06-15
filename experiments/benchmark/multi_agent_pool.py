"""Multi-agent discussion pool for EnergyBridge family benchmark.

N persona agents discuss VPP strategy and event scoring in configurable rounds.
Supports arbitrary number of personas (>=2) passed at runtime.
"""
from __future__ import annotations

import json
from typing import List, Tuple


# ---------------------------------------------------------------------------
# PersonaAgent - one family member with LLM roleplay
# ---------------------------------------------------------------------------
class PersonaAgent:
    """Wraps a persona JSON dict; speaks in discussion via LLM."""

    def __init__(self, persona: dict) -> None:
        self.persona = persona
        # Persistent cross-event memory: list of {"label": str, "text": str}
        self.memory: list[dict] = []

    def update_memory(self, label: str, text: str) -> None:
        """Record a past-event summary into this agent's long-term memory.

        Context kept: consensus strategy text and event outcome (setpoint + score).
        Context NOT kept: raw round-by-round dialogue — only the synthesized
        summary is stored, keeping the prompt short and signal-dense.
        """
        self.memory.append({"label": label, "text": text})
        # Keep last 10 entries to avoid context overflow
        if len(self.memory) > 10:
            self.memory = self.memory[-10:]

    @property
    def name(self) -> str:
        return self.persona.get("display_name", self.persona.get("id", "household member"))

    @property
    def system_prompt(self) -> str:
        return self.persona.get("llm_prompts", {}).get(
            "system_prompt",
            "I am a family member with average preferences for comfort and energy saving.",
        )

    @property
    def comfort_priority(self) -> float:
        return self.persona.get("preferences", {}).get(
            "scoring_weights", {}
        ).get("comfort", 0.5)

    @property
    def energy_priority(self) -> float:
        return self.persona.get("preferences", {}).get(
            "scoring_weights", {}
        ).get("energy", 0.3)

    def speak(self, topic: str, history: List[dict], context: str = "") -> str:
        """One LLM call from this persona's perspective.

        Args:
            topic:   The question / topic being discussed.
            history: List of {"name": str, "text": str} entries spoken so far.
            context: Background situation text (shared for all agents).

        Returns:
            A natural-language English reply (1-3 sentences).
        """
        from energybridge.llm.client import LLMClient

        sys_prompt = (
            f"You are household member \"{self.name}\" discussing home energy strategy with your family.\n"
            f"Your personal preference background: {self.system_prompt}\n"
            f"Reply in English in 1-3 sentences. State your view directly and do not repeat others."
        )
        parts: list[str] = []
        if self.memory:
            parts.append("[Your memory from real past events]")
            for m in self.memory:
                parts.append(f"  {m['label']}: {m['text']}")
        if context:
            parts.append(f"[Current situation] {context}")
        if history:
            parts.append("[Existing statements]")
            for h in history:
                parts.append(f"  {h['name']}: {h['text']}")
        parts.append(f"[Topic] {topic}")
        parts.append(f"Speak as \"{self.name}\":")

        user_msg = "\n".join(parts)
        try:
            r = LLMClient().chat_with_metrics(
                sys_prompt, user_msg, max_retries=2, retry_base_delay=1.0
            )
            return r["text"].strip()
        except Exception as e:
            return f"({self.name} failed to speak: {e})"


# ---------------------------------------------------------------------------
# DiscussionPool - orchestrates N agents over M rounds + synthesis
# ---------------------------------------------------------------------------
class DiscussionPool:
    """Run a household discussion among N PersonaAgents.

    Two discussion phases:
      1. discuss_strategy: before AC agent acts on a VPP event.
         Returns a consensus user_pref string injected into the AC agent prompt.
      2. discuss_score: after VPP event ends.
         Returns a consensus score (1.0-5.0) + reason.
    """

    def __init__(self, agents: List[PersonaAgent], max_rounds: int = 3) -> None:
        if not agents:
            raise ValueError("At least one PersonaAgent required.")
        self.agents = agents
        self.max_rounds = max_rounds

    # -- Public API -----------------------------------------------------------

    def discuss_strategy(
        self,
        event_index: int,
        vpp_context: dict,
        past_events: list,
    ) -> Tuple[str, list]:
        """Household discussion on VPP strategy preference BEFORE agent acts.

        Returns: (consensus_user_pref_text, transcript)
        """
        sim_h   = float(vpp_context.get("hour", 18.0))
        clock_h = int(sim_h % 24)
        day     = int(sim_h // 24) + 1
        dur     = float(vpp_context.get("duration_h", 1.0))

        context_lines = [
            f"VPP demand-response event {event_index}: Day {day} {clock_h:02d}:00-{int(clock_h+dur):02d}:00.",
            f"Duration: {int(dur*60)} minutes.",
            "The grid asks the household to reduce electricity use during this window.",
            "The AC setpoint may need to rise, and shiftable appliances such as washer or dishwasher should avoid this window.",
        ]
        if past_events:
            prev_parts = []
            for pe in past_events[-2:]:
                sc  = pe.get("score")
                cmt = pe.get("comment", pe.get("reason", ""))[:50]
                if sc is not None:
                    prev_parts.append(f"event {pe.get('id','?')} score {sc:.1f}: {cmt}")
            if prev_parts:
                context_lines.append("Past VPP event outcomes: " + "; ".join(prev_parts))

        context = "\n".join(context_lines)
        topic = (
            "What strategy do you want the smart control system to take for the upcoming VPP event? "
            "For example: AC setpoint, appliance timing, and whether comfort or savings should take priority."
        )

        transcript = self._run_rounds(topic, context, label_prefix=f"strategy discussion event {event_index}")
        consensus  = self._synthesize_strategy(transcript, context, event_index)
        # Store synthesized consensus (not raw dialogue) in each agent's memory
        mem_text = f"Consensus strategy: {consensus[:80]}"
        for agent in self.agents:
            agent.update_memory(label=f"event {event_index} strategy discussion", text=mem_text)
        return consensus, transcript

    def discuss_score(
        self,
        event_outcome: dict,
        event_index: int,
    ) -> Tuple[float, str, list]:
        """Household discussion on satisfaction score AFTER VPP event ends.

        Returns: (score 1.0-5.0, reason_str, transcript)
        """
        sp           = float(event_outcome.get("setpoint", 26.5))
        mean_t       = float(event_outcome.get("mean_temp_c", sp))
        e_day        = event_outcome.get("energy_kwh_per_day", "?")
        target       = event_outcome.get("target_kwh", "?")
        agent_reason = str(event_outcome.get("agent_reason", ""))[:80]

        context_lines = [
            f"VPP event {event_index} has ended.",
            f"AC setpoint: {sp:.1f}°C. VPP-window mean indoor temperature: {mean_t:.1f}°C.",
        ]
        if isinstance(e_day, (int, float)):
            context_lines.append(f"Today's cumulative electricity use was about {e_day:.2f} kWh.")
        if target not in ("?", None):
            context_lines.append(f"VPP demand target: <= {target} kWh.")
        if agent_reason:
            context_lines.append(f"Agent decision rationale: {agent_reason}")

        context = "\n".join(context_lines)
        topic = (
            "Are you satisfied with how this VPP event was handled? "
            "Give a 1-5 score (1=very dissatisfied, 5=very satisfied) and briefly explain why."
        )

        transcript = self._run_rounds(topic, context, label_prefix=f"score discussion event {event_index}")
        score, reason = self._synthesize_score(transcript, context, event_index)
        # Store event outcome (setpoint + consensus score) in each agent's memory
        mem_text = f"AC {sp:.1f}°C, consensus score {score:.1f}: {reason}"
        for agent in self.agents:
            agent.update_memory(label=f"event {event_index} outcome", text=mem_text)
        return score, reason, transcript

    # -- Internal helpers -----------------------------------------------------

    def _run_rounds(self, topic: str, context: str, label_prefix: str = "discussion") -> list:
        """Run up to self.max_rounds discussion rounds with consensus check after each round.

        After each round (except the last), a neutral synthesis LLM judges whether
        the household has reached sufficient consensus.  If yes, break early.
        If max_rounds is exhausted without consensus, force-summarize anyway.
        """
        history: list[dict] = []
        for round_i in range(self.max_rounds):
            label = "initial opinions" if round_i == 0 else f"round {round_i + 1}"
            print(f"  ┌─[Multi-user {label_prefix} {label}]{'─'*28}")
            for agent in self.agents:
                text = agent.speak(topic, history, context)
                print(f"  │  [{agent.name}] {text}")
                history.append({"name": agent.name, "text": text})
            print(f"  └{'─'*56}")

            # After the last round: skip check, force-summarize
            if round_i == self.max_rounds - 1:
                print(f"  [Synthesis LLM] reached max rounds ({self.max_rounds}); forcing summary")
                break

            # Consensus check — synthesis LLM observes, does NOT participate
            reached, reason = self._check_consensus(history, context, round_i + 1)
            if reached:
                print(f"  [Synthesis LLM] consensus after round {round_i + 1} -> {reason}")
                break
            else:
                print(f"  [Synthesis LLM] disagreement after round {round_i + 1}; entering next round -> {reason}")

        return history

    def _check_consensus(self, history: list, context: str, round_num: int) -> tuple:
        """Neutral synthesis LLM judges if the family reached sufficient consensus.

        The LLM is explicitly told NOT to add opinions — only to observe and judge.
        Returns: (consensus_reached: bool, reason: str)
        Fail-safe: on any error, returns (True, "judgment failed") to avoid infinite loops.
        """
        from energybridge.llm.client import LLMClient

        sys_prompt = (
            "You are a neutral household facilitator observing (NOT participating in) "
            "a family discussion. Your ONLY job: judge whether the family members have "
            "reached sufficient consensus to make a collective decision. "
            "Do NOT add your own opinions, suggestions, or solutions. "
            'Return JSON only, no markdown: {"consensus_reached": true, "reason": "<max 40 chars>"} '
            'or {"consensus_reached": false, "reason": "<max 40 chars>"}'
        )
        discussion_text = "\n".join(f"{h['name']}: {h['text']}" for h in history)
        user_msg = (
            f"Round {round_num} just completed.\n"
            f"Context: {context}\n\n"
            f"Discussion so far:\n{discussion_text}\n\n"
            "Has the family reached sufficient consensus (general agreement on direction) "
            "to make a household energy decision? "
            'JSON only: {"consensus_reached": true/false, "reason": "..."}'
        )
        try:
            r = LLMClient().chat_with_metrics(
                sys_prompt, user_msg, max_retries=2, retry_base_delay=1.0
            )
            text = r["text"].strip()
            if text.startswith("```"):
                text = "\n".join(
                    l for l in text.splitlines()
                    if not l.strip().startswith("```")
                ).strip()
            data = json.loads(text)
            reached = bool(data.get("consensus_reached", False))
            reason  = str(data.get("reason", ""))[:40]
            return reached, reason
        except Exception as e:
            return True, f"judgment failed ({str(e)[:20]})"

    def _synthesize_strategy(self, transcript: list, context: str, event_index: int) -> str:
        """Synthesize discussion transcript -> one user_pref string for AC agent."""
        from energybridge.llm.client import LLMClient

        names = " / ".join(a.name for a in self.agents)
        sys_prompt = (
            "You are a household facilitator summarizing a family discussion about a VPP energy event. "
            "Based on the discussion, write ONE concise preference statement in English (<=80 words) "
            "for an AC control agent. Capture the household consensus or compromise. "
            "Be specific: mention acceptable temperature range, appliance timing preferences, "
            "and whether comfort or savings takes priority. "
            "This text will be injected directly into the AC agent's prompt."
        )
        discussion_text = "\n".join(f"{h['name']}: {h['text']}" for h in transcript)
        user_msg = (
            f"Household members ({names}) discussed event {event_index}:\n"
            f"Context: {context}\n\n"
            f"Discussion:\n{discussion_text}\n\n"
            f"Synthesize into a single user_pref statement:"
        )
        try:
            r = LLMClient().chat_with_metrics(
                sys_prompt, user_msg, max_retries=2, retry_base_delay=1.0
            )
            result = r["text"].strip()
            if result.startswith("```"):
                result = "\n".join(
                    l for l in result.splitlines()
                    if not l.strip().startswith("```")
                ).strip()
            print(f"  [Consensus Strategy | event {event_index}] {result[:120]}")
            return result
        except Exception as e:
            fallback = self.agents[0].system_prompt if self.agents else (
                "Please balance comfort and energy savings."
            )
            print(f"  [Consensus Strategy | synthesis failed] {e} - using the first member preference")
            return fallback

    def _synthesize_score(self, transcript: list, context: str, event_index: int) -> Tuple[float, str]:
        """Synthesize discussion transcript -> (score 1-5, reason)."""
        from energybridge.llm.client import LLMClient

        names = " / ".join(a.name for a in self.agents)
        sys_prompt = (
            "You are a household facilitator. Family members have discussed a VPP event outcome. "
            "Synthesize their opinions into ONE final satisfaction score and reason. "
            'Return JSON only, no markdown: {"score": X.X, "reason": "<=60 chars"}\n'
            "Score scale: 1=very dissatisfied, 2=dissatisfied, 3=neutral, 4=satisfied, 5=very satisfied."
        )
        discussion_text = "\n".join(f"{h['name']}: {h['text']}" for h in transcript)
        user_msg = (
            f"Household members ({names}) rated event {event_index}:\n"
            f"Context: {context}\n\n"
            f"Discussion:\n{discussion_text}\n\n"
            f"Return JSON score:"
        )
        try:
            r = LLMClient().chat_with_metrics(
                sys_prompt, user_msg, max_retries=2, retry_base_delay=1.0
            )
            text = r["text"].strip()
            if text.startswith("```"):
                text = "\n".join(
                    l for l in text.splitlines()
                    if not l.strip().startswith("```")
                ).strip()
            data = json.loads(text)
            score = float(data.get("score", 3.0))
            score = max(1.0, min(5.0, round(score * 2) / 2))  # round to nearest 0.5
            reason = str(data.get("reason", ""))[:60]
            print(f"  [Consensus Score | event {event_index}] {score}/5 - {reason}")
            return score, reason
        except Exception as e:
            print(f"  [Consensus Score | synthesis failed] {e} - falling back to 3.0")
            return 3.0, f"synthesis error: {e}"
