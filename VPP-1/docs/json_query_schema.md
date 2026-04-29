# JSON 查询结构说明

VPP-1 生成的 `FlexibilityQuery` 是能力查询命令，不是控制命令。

顶层结构：

```json
{
  "query_id": "...",
  "query_type": "capacity_assessment",
  "source_task": {},
  "target_building_group": {},
  "query_window": {},
  "requested_assessment": {},
  "query_constraints": {},
  "local_evaluation_instruction": {},
  "response_schema": {}
}
```

关键规则：

- `query_type = "capacity_assessment"`：只做能力评估。
- `response_direction = "load_reduction"`：当前是削峰场景。
- `suggested_reduction_kw_per_building = null`：VPP 不给单体建筑硬性削减目标。
- `comfort_constraint_source = "local_building_agent"`：舒适约束由建筑侧本地决定。
- `allow_raw_sensor_upload = false`：不允许上传原始传感器数据。
- `allow_direct_device_control = false`：不允许 VPP 直接控制设备。
- `do_not_execute_control = true`：当前不执行控制。
- `do_not_notify_user_yet = true`：当前不通知用户。

`required_capacity_kw` 是上游平台要求的 VPP 级调节能力，不是单体建筑削减目标。
