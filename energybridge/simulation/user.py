"""User simulation object for role-play evaluation."""

from __future__ import annotations

from energybridge.llm.roleplay_user import RoleplayUserSimulator


class SimulatedUser:
    """A single LLM role-play persona used across multiple simulation turns."""

    def __init__(self, roleplay: RoleplayUserSimulator | None = None) -> None:
        self.roleplay = roleplay or RoleplayUserSimulator()
        self.persona_trace = self.roleplay.create_persona()
        self.persona = self.persona_trace["data"]

    @property
    def persona_id(self) -> str:
        return str(self.persona.get("persona_id", "sim_user"))

    def generate_input(
        self,
        turn_index: int,
        scenario: dict,
        memory_snapshot: dict,
        history_summary: list[dict],
    ) -> dict:
        return self.roleplay.generate_user_input(
            persona=self.persona,
            turn_index=turn_index,
            scenario=scenario,
            memory_snapshot=memory_snapshot,
            history_summary=history_summary,
        )

    def choose_strategy(self, turn_index: int, scenario: dict, strategy_options: list[dict]) -> dict:
        return self.roleplay.choose_strategy(
            persona=self.persona,
            turn_index=turn_index,
            scenario=scenario,
            strategy_options=strategy_options,
        )

    def give_feedback(
        self,
        turn_index: int,
        selected_strategy: dict,
        projected_control_plan: dict,
        projected_safety_report: dict,
    ) -> dict:
        return self.roleplay.generate_feedback(
            persona=self.persona,
            turn_index=turn_index,
            selected_strategy=selected_strategy,
            projected_control_plan=projected_control_plan,
            projected_safety_report=projected_safety_report,
        )
