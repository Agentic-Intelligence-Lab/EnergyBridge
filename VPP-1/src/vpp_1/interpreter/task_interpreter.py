# :::
# 本文件用于将 VPP-1 接收到的上游市场/调度任务翻译为建筑侧能力查询命令。
# 当前输出的是标准 JSON 风格 FlexibilityQuery。
# 该查询命令只用于询问建筑侧 Agent 在目标时段内可提供多少负荷削减能力，
# 不直接指定单体建筑削减目标，不执行控制，不通知用户。
# :::
"""Translate upstream tasks into building-side JSON capability queries."""

from __future__ import annotations

from vpp_1.core.schemas import FlexibilityQuery, MarketDispatchTask, TargetBuildingGroup


class TaskInterpreter:
    """Builds FlexibilityQuery from upstream task and target group."""

    def build_flexibility_query(
        self,
        task: MarketDispatchTask,
        target_group: TargetBuildingGroup,
    ) -> FlexibilityQuery:
        """Translate an upstream task into a JSON-style capability query."""

        return FlexibilityQuery(
            query_id=f"query_{task.task_id}_{target_group.group_id}",
            query_type="capacity_assessment",
            source_task={
                "task_id": task.task_id,
                "city": task.city,
                "source_platform": task.source.name,
                "task_type": task.task_type.name,
                "time_scale": task.time_scale.name,
                "trigger_reason": task.trigger_reason.name,
                "publish_time": task.publish_time,
                "start_time": task.start_time,
                "end_time": task.end_time,
                "notice_minutes": task.notice_minutes,
                "required_capacity_kw": task.required_capacity_kw,
                "target_query_capacity_kw": task.target_query_capacity_kw,
                "description": task.description,
            },
            target_building_group={
                "group_id": target_group.group_id,
                "building_archetype": target_group.building_archetype.name,
                "representative_mode": target_group.representative_mode,
                "estimated_user_count": target_group.estimated_user_count,
                "main_flexible_asset": target_group.main_flexible_asset,
                "participation_mode": target_group.participation_mode.name,
            },
            query_window={
                "start_time": task.start_time,
                "end_time": task.end_time,
                "duration_minutes": task.duration_minutes,
            },
            requested_assessment={
                "assessment_target": "available_flexibility_capacity",
                "response_direction": "load_reduction",
                "power_unit": "kW",
                "energy_unit": "kWh",
                "suggested_reduction_kw_per_building": None,
                "required_outputs": [
                    "estimated_reduction_kw_per_building",
                    "estimated_reduction_kwh_per_building",
                    "confidence",
                    "local_comfort_upper_bound_c",
                    "expected_max_temperature_c",
                    "comfort_risk",
                    "estimated_acceptance_probability",
                    "response_reliability",
                    "minimum_reward_required_yuan",
                    "requires_user_confirmation",
                ],
            },
            query_constraints={
                "privacy_mode": "summary_only",
                "allow_raw_sensor_upload": False,
                "allow_direct_device_control": False,
                "comfort_constraint_source": "local_building_agent",
                "requires_user_confirmation": True,
            },
            local_evaluation_instruction={
                "instruction": (
                    "请建筑侧 Agent 在本地评估该小型办公建筑在目标时段内可提供的"
                    "负荷削减能力。评估过程应调用本地建筑状态、历史运行信息、"
                    "用户约束、舒适边界和物理仿真工具。当前阶段仅进行能力评估，"
                    "不执行控制动作，也不直接通知用户参与。"
                ),
                "allowed_local_tools": [
                    "building_state_reader",
                    "energyplus_engine",
                    "comfort_constraint_checker",
                    "user_profile_model",
                ],
                "do_not_execute_control": True,
                "do_not_notify_user_yet": True,
            },
            response_schema={
                "expected_response_type": "FlexibilitySummary",
                "fields": [
                    "query_id",
                    "target_group_id",
                    "estimated_reduction_kw_per_building",
                    "estimated_reduction_kwh_per_building",
                    "confidence",
                    "local_comfort_upper_bound_c",
                    "expected_max_temperature_c",
                    "comfort_risk",
                    "estimated_acceptance_probability",
                    "response_reliability",
                    "minimum_reward_required_yuan",
                    "requires_user_confirmation",
                    "privacy_note",
                ],
            },
        )
