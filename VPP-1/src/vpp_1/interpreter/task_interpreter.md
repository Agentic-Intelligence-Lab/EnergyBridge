# task_interpreter.py 参数说明

## 1. 文件作用

`task_interpreter.py` 用于将 VPP-1 接收到的上游市场/调度任务 `MarketDispatchTask` 翻译为建筑侧能力查询命令 `FlexibilityQuery`。

该文件只做任务翻译，不调用 EnergyPlus，不执行控制，不通知用户，不做收益结算。

## 2. 总体链路

```text
MarketDispatchTask
→ TaskInterpreter
→ FlexibilityQuery
→ 建筑侧 Agent / EnergyPlus
→ FlexibilitySummary
```

## 3. 顶层字段说明

- `query_id`：查询命令 ID，由上游任务 ID 和目标建筑组 ID 组合生成。
- `query_type`：查询类型，当前固定为 `capacity_assessment`，表示能力评估查询。

## 4. `source_task` 字段说明

- `task_id`：上游任务 ID。
- `city`：任务所在城市。
- `source_platform`：任务来源平台。
- `task_type`：任务类型，当前为邀约型或紧急型需求响应。
- `time_scale`：任务时间尺度，例如日前、日内或实时。
- `trigger_reason`：触发原因，例如区域尖峰负荷、电力缺口或局部重载。
- `publish_time`：任务发布时间。
- `start_time`：响应窗口开始时间。
- `end_time`：响应窗口结束时间。
- `notice_minutes`：通知提前量，单位分钟。
- `required_capacity_kw`：上游平台需要的 VPP 级调节能力，不是单体建筑削减目标。
- `target_query_capacity_kw`：考虑安全裕度后的 VPP 查询目标能力。
- `description`：上游任务描述。

## 5. `target_building_group` 字段说明

- `group_id`：目标建筑组 ID。
- `building_archetype`：建筑类型，当前为小型商业 HVAC。
- `representative_mode`：是否代表性样本模式。
- `estimated_user_count`：样本数量，当前为 1。
- `main_flexible_asset`：主要柔性资源，当前为 HVAC。
- `participation_mode`：参与模式，当前为人工确认。

## 6. `query_window` 字段说明

- `start_time`：建筑侧 Agent 需要评估的开始时间。
- `end_time`：建筑侧 Agent 需要评估的结束时间。
- `duration_minutes`：评估窗口持续时间。

## 7. `requested_assessment` 字段说明

- `assessment_target`：评估目标，当前为 `available_flexibility_capacity`。
- `response_direction`：响应方向。`load_reduction` 表示削峰场景。
- `power_unit`：功率单位，当前为 `kW`。
- `energy_unit`：电量单位，当前为 `kWh`。
- `suggested_reduction_kw_per_building`：VPP 给单体建筑的建议削减目标。当前为 `null`，表示 VPP 不给单体建筑硬性目标。
- `required_outputs`：建筑侧未来需要返回的摘要字段。

## 8. `query_constraints` 字段说明

- `privacy_mode`：隐私模式，当前为 `summary_only`。
- `allow_raw_sensor_upload`：是否允许上传原始传感器数据，当前为 `false`。
- `allow_direct_device_control`：是否允许 VPP 直接控制设备，当前为 `false`。
- `comfort_constraint_source`：舒适约束来源。`local_building_agent` 表示舒适约束由建筑侧本地决定。
- `requires_user_confirmation`：后续是否需要用户确认，当前为 `true`。

## 9. `local_evaluation_instruction` 字段说明

- `instruction`：给建筑侧 Agent 的中文评估说明。
- `allowed_local_tools`：允许建筑侧本地调用的工具，例如状态读取、EnergyPlus、舒适约束检查和用户画像。
- `do_not_execute_control`：当前不执行控制。
- `do_not_notify_user_yet`：当前不通知用户。

## 10. `response_schema` 字段说明

- `query_id`：原查询 ID。
- `target_group_id`：目标建筑组 ID。
- `estimated_reduction_kw_per_building`：单体建筑预计可削减功率。
- `estimated_reduction_kwh_per_building`：单体建筑预计可削减电量。
- `confidence`：评估置信度。
- `local_comfort_upper_bound_c`：建筑侧本地舒适温度上限。
- `expected_max_temperature_c`：预计最高室温。
- `comfort_risk`：舒适风险。
- `estimated_acceptance_probability`：预计接受概率。
- `response_reliability`：响应可靠性。
- `minimum_reward_required_yuan`：最低收益要求。
- `requires_user_confirmation`：是否需要用户确认。
- `privacy_note`：隐私说明。

## 11. 当前设计核心原则

```text
required_capacity_kw 是 VPP 级别任务需求，不是单体建筑削减目标。
suggested_reduction_kw_per_building = null 表示 VPP 不给单体建筑硬性目标。
response_direction = load_reduction 表示削峰场景。
comfort_constraint_source = local_building_agent 表示舒适约束由建筑侧本地决定。
VPP-1 不执行控制，不通知用户，不上传原始传感器数据。
```
