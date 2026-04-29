from __future__ import annotations

from vpp_1.interpreter.task_interpreter import TaskInterpreter
from vpp_1.market.city_task_templates import TaskGenerationConfig, generate_invitation_dr_task
from vpp_1.simulation.scenario_config import create_default_scenario_config


def test_task_interpreter_builds_required_query_structure() -> None:
    config = create_default_scenario_config()
    task = generate_invitation_dr_task(TaskGenerationConfig(random_seed=3))
    target_group = config.create_target_group()

    query = TaskInterpreter().build_flexibility_query(task, target_group)
    query_dict = query.to_dict()

    assert query.query_type == "capacity_assessment"
    assert query_dict["requested_assessment"]["response_direction"] == "load_reduction"
    assert query_dict["requested_assessment"]["suggested_reduction_kw_per_building"] is None
    assert query_dict["query_constraints"]["comfort_constraint_source"] == "local_building_agent"
    assert query_dict["query_constraints"]["allow_direct_device_control"] is False
    assert query_dict["query_constraints"]["allow_raw_sensor_upload"] is False
    assert query_dict["local_evaluation_instruction"]["do_not_execute_control"] is True
    assert query_dict["local_evaluation_instruction"]["do_not_notify_user_yet"] is True
    assert query_dict["source_task"]["required_capacity_kw"] == task.required_capacity_kw
