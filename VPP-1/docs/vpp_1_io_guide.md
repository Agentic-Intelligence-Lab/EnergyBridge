# VPP-1 输入输出与通信说明

## 1. VPP-1 的作用

VPP-1 是一个轻量化虚拟电厂信号发生器。它用于模拟 VPP 从上游电网或市场侧接收到任务，然后把这个任务转化为建筑侧 Agent 可以读取的标准 JSON 查询命令。

VPP-1 当前只负责生成和发布查询命令，不负责建筑侧计算、不负责控制执行、不负责结算。

## 2. VPP-1 的输入

VPP-1 的输入是上游任务 `MarketDispatchTask`。

它表示 VPP 从电网、调度中心、负荷管理平台或虚拟电厂管理平台接收到的任务。

主要字段：

- `task_id`：任务编号。
- `city`：任务所在城市或区域。
- `task_type`：任务类型，例如邀约型需求响应或紧急型需求响应。
- `time_scale`：任务时间尺度，例如日前、日内或实时。
- `trigger_reason`：任务触发原因，例如区域负荷高峰、局部过载或电力短缺。
- `publish_time`：任务发布时间。
- `start_time`：响应开始时间。
- `end_time`：响应结束时间。
- `notice_minutes`：提前通知时间。
- `duration_minutes`：响应持续时间。
- `required_capacity_kw`：上游任务需要的总调节容量，单位 kW。注意，这不是单体建筑的削减目标。
- `safety_margin_factor`：安全裕度系数。
- `target_query_capacity_kw`：目标查询容量，等于 `required_capacity_kw × safety_margin_factor`。
- `declaration_deadline`：参与申报/声明截止时间（若适用）。
- `baseline_method`：如何定义削减基线（文本说明，结算相关）。
- `performance_rule`：绩效/结算规则说明（文本，占位）。
- `reward_description`：补偿/激励说明（文本，占位）。
- `description`：可读的任务描述。

## 3. VPP-1 的输出

VPP-1 的输出是 `FlexibilityQuery`，也就是发给建筑侧 Agent 的标准 JSON 查询命令。

它不是控制命令，只是能力查询命令。

主要字段：

- `query_id`：查询编号，用来关联任务和建筑侧反馈。
- `query_type`：查询类型，当前为 `capacity_assessment`。
- `source_task`：上游任务摘要。
- `target_building_group`：被查询的建筑类型，当前为小型办公建筑 HVAC。
- `query_window`：建筑侧需要评估的时间窗口。
- `requested_assessment`：VPP 希望建筑侧评估的内容。
- `query_constraints`：查询边界，例如只返回摘要、不上传原始传感器数据、不允许 VPP 直接控制设备。
- `local_evaluation_instruction`：给建筑侧 Agent 的本地评估说明。
- `response_schema`：建筑侧 Agent 应返回的字段格式。

重点约束：

- `response_direction = "load_reduction"` 表示当前是削峰场景。
- `suggested_reduction_kw_per_building = null` 表示 VPP 不给单体建筑硬性削减目标。
- `comfort_constraint_source = "local_building_agent"` 表示舒适边界由建筑侧本地决定。
- `do_not_execute_control = true` 表示当前只做能力评估，不执行控制。
- `do_not_notify_user_yet = true` 表示当前不直接通知用户。

## 4. VPP-1 与建筑侧 Agent / 物理模型的通信方式

通信链路：

```text
MarketDispatchTask
→ VPP-1
→ FlexibilityQuery
→ 建筑侧 Agent
→ EnergyPlus / 物理模型
→ FlexibilitySummary
```

VPP-1 生成 `FlexibilityQuery` 后，建筑侧 Agent 读取该 JSON。建筑侧 Agent 根据其中的 `query_window`、`response_direction`、`requested_assessment` 和 `query_constraints`，在本地调用 EnergyPlus 或其他物理模型，评估建筑在目标时段内能够提供多少削峰能力。

VPP-1 不直接调用 EnergyPlus。EnergyPlus 由建筑侧 Agent 或建筑侧系统调用。

## 5. 建筑侧 Agent 应返回什么

建筑侧 Agent 后续应返回 `FlexibilitySummary`。

建议字段：

- `query_id`：对应 VPP-1 发出的查询编号。
- `target_group_id`：目标建筑群编号。
- `estimated_reduction_kw_per_building`：该建筑预计可削减功率，单位 kW。
- `estimated_reduction_kwh_per_building`：该建筑在查询窗口内预计可削减电量，单位 kWh。
- `confidence`：评估置信度。
- `local_comfort_upper_bound_c`：建筑侧本地采用的舒适温度上限。
- `expected_max_temperature_c`：预计最高室内温度。
- `comfort_risk`：舒适风险。
- `estimated_acceptance_probability`：预计用户或管理者接受概率。
- `response_reliability`：预计响应可靠性。
- `minimum_reward_required_yuan`：最低收益要求。
- `requires_user_confirmation`：是否需要用户确认。
- `privacy_note`：隐私说明。

## 6. 最小示例

简化输入 `MarketDispatchTask`：

```json
{
  "task_id": "INV_001",
  "city": "广州",
  "task_type": "INVITATION_DEMAND_RESPONSE",
  "start_time": "2026-07-16 18:00",
  "end_time": "2026-07-16 20:00",
  "required_capacity_kw": 50000,
  "safety_margin_factor": 1.4,
  "target_query_capacity_kw": 70000
}
```

简化输出 `FlexibilityQuery`：

```json
{
  "query_id": "query_INV_001_small_office_group_A",
  "query_type": "capacity_assessment",
  "query_window": {
    "start_time": "2026-07-16 18:00",
    "end_time": "2026-07-16 20:00"
  },
  "requested_assessment": {
    "response_direction": "load_reduction",
    "suggested_reduction_kw_per_building": null
  },
  "query_constraints": {
    "comfort_constraint_source": "local_building_agent",
    "allow_direct_device_control": false
  }
}
```

建筑侧可能返回的 `FlexibilitySummary`：

```json
{
  "query_id": "query_INV_001_small_office_group_A",
  "target_group_id": "small_office_group_A",
  "estimated_reduction_kw_per_building": 12.5,
  "estimated_reduction_kwh_per_building": 25.0,
  "confidence": 0.82,
  "local_comfort_upper_bound_c": 27.5,
  "expected_max_temperature_c": 26.8,
  "comfort_risk": "LOW",
  "requires_user_confirmation": true,
  "privacy_note": "仅返回摘要，不上传原始传感器数据。"
}
```

## 7. 如何运行

运行 demo：

```bash
python run_demo.py
```

如果当前环境需要指定 Python，可以使用项目约定环境：

```bash
F:\anaconda\envs\myenv\python.exe run_demo.py
```
