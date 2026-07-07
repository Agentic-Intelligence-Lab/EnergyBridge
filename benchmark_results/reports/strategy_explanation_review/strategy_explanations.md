# VPP Strategy Explanation Review Data

Generated: 2026-07-07 11:51:49
Records: 18

## basic_role_a_commuter_price_cooperative / review_vpp1

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约0.50kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约0.50kW的可调负荷。 建议采用平衡方案：只在24.0-26.0°C内临时调整，窗口结束后恢复舒适设定；洗衣机安排在19:00开始，运行约2小时，避开18:00-19:00；洗碗机安排在21:00开始，运行约1.5小时，避开18:00-19:00；热水器在15:00-17:00预热，目标55°C，洗浴前保温，避开18:00-19:00。 保护边界：室温策略不得越过用户偏好舒适范围 24.0-26.0°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：本次参考目标约0.50kW，采用低干扰动作即可，重点是把可控非空调负荷移出窗口。 可选方案包括：保守方案 / 平衡方案 / 增强响应方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp1",
  "vpp_window": {
    "start_h": 18.0,
    "end_h": 19.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 26.0,
    "preferred_min_c": 24.0,
    "preferred_max_c": 26.0,
    "restore_after_h": 19.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 19.0,
      "skip": false,
      "dr_adjustable": true
    },
    "dishwasher": {
      "start_h": 21.0,
      "skip": false,
      "dr_adjustable": true
    },
    "water_heater": {
      "preheat_start_h": 15.0,
      "preheat_end_h": 17.0,
      "preheat_temp_c": 55.0,
      "preheat": true
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_a_commuter_price_cooperative / review_vpp2

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.00kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.00kW的可调负荷。 建议采用平衡方案：只在24.0-26.0°C内临时调整，窗口结束后恢复舒适设定；洗衣机安排在19:00开始，运行约2小时，避开18:00-19:00；洗碗机安排在21:00开始，运行约1.5小时，避开18:00-19:00；热水器在15:00-17:00预热，目标55°C，洗浴前保温，避开18:00-19:00。 保护边界：室温策略不得越过用户偏好舒适范围 24.0-26.0°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：本次参考削峰目标约1.00kW；计划优先转移约4.7kW的可控设备负荷。 可选方案包括：保守方案 / 平衡方案 / 增强响应方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp2",
  "vpp_window": {
    "start_h": 42.0,
    "end_h": 43.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 26.0,
    "preferred_min_c": 24.0,
    "preferred_max_c": 26.0,
    "restore_after_h": 43.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 19.0,
      "skip": false,
      "dr_adjustable": true
    },
    "dishwasher": {
      "start_h": 21.0,
      "skip": false,
      "dr_adjustable": true
    },
    "water_heater": {
      "preheat_start_h": 15.0,
      "preheat_end_h": 17.0,
      "preheat_temp_c": 55.0,
      "preheat": true
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_a_commuter_price_cooperative / review_vpp3

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.50kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.50kW的可调负荷。 建议采用平衡方案：只在24.0-26.0°C内临时调整，窗口结束后恢复舒适设定；洗衣机安排在19:00开始，运行约2小时，避开18:00-19:00；洗碗机安排在21:00开始，运行约1.5小时，避开18:00-19:00；热水器在15:00-17:00预热，目标55°C，洗浴前保温，避开18:00-19:00。 保护边界：室温策略不得越过用户偏好舒适范围 24.0-26.0°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：本次参考削峰目标约1.50kW；计划优先转移约4.7kW的可控设备负荷。 可选方案包括：保守方案 / 平衡方案 / 增强响应方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp3",
  "vpp_window": {
    "start_h": 66.0,
    "end_h": 67.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 26.0,
    "preferred_min_c": 24.0,
    "preferred_max_c": 26.0,
    "restore_after_h": 67.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 19.0,
      "skip": false,
      "dr_adjustable": true
    },
    "dishwasher": {
      "start_h": 21.0,
      "skip": false,
      "dr_adjustable": true
    },
    "water_heater": {
      "preheat_start_h": 15.0,
      "preheat_end_h": 17.0,
      "preheat_temp_c": 55.0,
      "preheat": true
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_b_home_comfort_gated / review_vpp1

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约0.50kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约0.50kW的可调负荷。 建议采用平衡方案：只在24.5-25.5°C内临时调整，窗口结束后恢复舒适设定；洗衣机安排在10:00开始，运行约2小时，避开18:00-19:00；热水器保持固定预热18:00-20:00，目标50°C，保障洗浴热水，不把该例程作为削峰资源。 保护边界：室温策略不得越过用户偏好舒适范围 24.5-25.5°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：当前没有可安全转移的可控设备负荷；收益主要来自提醒用户避免在窗口内新增非关键用电。 可选方案包括：保守方案 / 平衡方案 / 增强响应方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp1",
  "vpp_window": {
    "start_h": 18.0,
    "end_h": 19.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 25.5,
    "preferred_min_c": 24.5,
    "preferred_max_c": 25.5,
    "restore_after_h": 19.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 10.0,
      "skip": false,
      "dr_adjustable": false
    },
    "water_heater": {
      "preheat_start_h": 18.0,
      "preheat_end_h": 20.0,
      "preheat_temp_c": 50.0,
      "preheat": true
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_b_home_comfort_gated / review_vpp2

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.00kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.00kW的可调负荷。 建议采用平衡方案：只在24.5-25.5°C内临时调整，窗口结束后恢复舒适设定；洗衣机安排在10:00开始，运行约2小时，避开18:00-19:00；热水器保持固定预热18:00-20:00，目标50°C，保障洗浴热水，不把该例程作为削峰资源。 保护边界：室温策略不得越过用户偏好舒适范围 24.5-25.5°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：当前没有可安全转移的可控设备负荷；收益主要来自提醒用户避免在窗口内新增非关键用电。 可选方案包括：保守方案 / 平衡方案 / 增强响应方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp2",
  "vpp_window": {
    "start_h": 42.0,
    "end_h": 43.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 25.5,
    "preferred_min_c": 24.5,
    "preferred_max_c": 25.5,
    "restore_after_h": 43.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 10.0,
      "skip": false,
      "dr_adjustable": false
    },
    "water_heater": {
      "preheat_start_h": 18.0,
      "preheat_end_h": 20.0,
      "preheat_temp_c": 50.0,
      "preheat": true
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_b_home_comfort_gated / review_vpp3

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.50kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.50kW的可调负荷。 建议采用平衡方案：只在24.5-25.5°C内临时调整，窗口结束后恢复舒适设定；洗衣机安排在10:00开始，运行约2小时，避开18:00-19:00；热水器保持固定预热18:00-20:00，目标50°C，保障洗浴热水，不把该例程作为削峰资源。 保护边界：室温策略不得越过用户偏好舒适范围 24.5-25.5°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：当前没有可安全转移的可控设备负荷；收益主要来自提醒用户避免在窗口内新增非关键用电。 可选方案包括：保守方案 / 平衡方案 / 增强响应方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp3",
  "vpp_window": {
    "start_h": 66.0,
    "end_h": 67.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 25.5,
    "preferred_min_c": 24.5,
    "preferred_max_c": 25.5,
    "restore_after_h": 67.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 10.0,
      "skip": false,
      "dr_adjustable": false
    },
    "water_heater": {
      "preheat_start_h": 18.0,
      "preheat_end_h": 20.0,
      "preheat_temp_c": 50.0,
      "preheat": true
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_c_irregular_cautious / review_vpp1

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约0.50kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约0.50kW的可调负荷。 建议采用平衡方案：只在24.0-26.0°C内临时调整，窗口结束后恢复舒适设定；洗衣机安排在19:00开始，运行约2小时，避开18:00-19:00；热水器保持固定预热18:00-20:00，目标50°C，保障洗浴热水，不把该例程作为削峰资源。 保护边界：室温策略不得越过用户偏好舒适范围 24.0-26.0°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：当前没有可安全转移的可控设备负荷；收益主要来自提醒用户避免在窗口内新增非关键用电。 可选方案包括：保守方案 / 平衡方案 / 增强响应方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp1",
  "vpp_window": {
    "start_h": 18.0,
    "end_h": 19.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 26.0,
    "preferred_min_c": 24.0,
    "preferred_max_c": 26.0,
    "restore_after_h": 19.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 19.0,
      "skip": false,
      "dr_adjustable": false
    },
    "water_heater": {
      "preheat_start_h": 18.0,
      "preheat_end_h": 20.0,
      "preheat_temp_c": 50.0,
      "preheat": true
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_c_irregular_cautious / review_vpp2

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.00kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.00kW的可调负荷。 建议采用平衡方案：只在24.0-26.0°C内临时调整，窗口结束后恢复舒适设定；洗衣机安排在19:00开始，运行约2小时，避开18:00-19:00；热水器保持固定预热18:00-20:00，目标50°C，保障洗浴热水，不把该例程作为削峰资源。 保护边界：室温策略不得越过用户偏好舒适范围 24.0-26.0°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：当前没有可安全转移的可控设备负荷；收益主要来自提醒用户避免在窗口内新增非关键用电。 可选方案包括：保守方案 / 平衡方案 / 增强响应方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp2",
  "vpp_window": {
    "start_h": 42.0,
    "end_h": 43.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 26.0,
    "preferred_min_c": 24.0,
    "preferred_max_c": 26.0,
    "restore_after_h": 43.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 19.0,
      "skip": false,
      "dr_adjustable": false
    },
    "water_heater": {
      "preheat_start_h": 18.0,
      "preheat_end_h": 20.0,
      "preheat_temp_c": 50.0,
      "preheat": true
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_c_irregular_cautious / review_vpp3

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.50kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.50kW的可调负荷。 建议采用平衡方案：只在24.0-26.0°C内临时调整，窗口结束后恢复舒适设定；洗衣机安排在19:00开始，运行约2小时，避开18:00-19:00；热水器保持固定预热18:00-20:00，目标50°C，保障洗浴热水，不把该例程作为削峰资源。 保护边界：室温策略不得越过用户偏好舒适范围 24.0-26.0°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：当前没有可安全转移的可控设备负荷；收益主要来自提醒用户避免在窗口内新增非关键用电。 可选方案包括：保守方案 / 平衡方案 / 增强响应方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp3",
  "vpp_window": {
    "start_h": 66.0,
    "end_h": 67.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 26.0,
    "preferred_min_c": 24.0,
    "preferred_max_c": 26.0,
    "restore_after_h": 67.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 19.0,
      "skip": false,
      "dr_adjustable": false
    },
    "water_heater": {
      "preheat_start_h": 18.0,
      "preheat_end_h": 20.0,
      "preheat_temp_c": 50.0,
      "preheat": true
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_d_commuter_ideal_dr / review_vpp1

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约0.50kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约0.50kW的可调负荷。 建议采用平衡方案：只在23.0-27.0°C内临时调整，窗口结束后恢复舒适设定；洗衣机安排在13:00开始，运行约2小时，避开18:00-19:00；洗碗机安排在16:00开始，运行约1.5小时，避开18:00-19:00；热水器在15:00-17:00预热，目标55°C，洗浴前保温，避开18:00-19:00。 保护边界：室温策略不得越过用户偏好舒适范围 23.0-27.0°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：本次参考目标约0.50kW，采用低干扰动作即可，重点是把可控非空调负荷移出窗口。 可选方案包括：保守方案 / 平衡方案 / 增强响应方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp1",
  "vpp_window": {
    "start_h": 18.0,
    "end_h": 19.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 27.0,
    "preferred_min_c": 23.0,
    "preferred_max_c": 27.0,
    "restore_after_h": 19.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 13.0,
      "skip": false,
      "dr_adjustable": true
    },
    "dishwasher": {
      "start_h": 16.0,
      "skip": false,
      "dr_adjustable": true
    },
    "water_heater": {
      "preheat_start_h": 15.0,
      "preheat_end_h": 17.0,
      "preheat_temp_c": 55.0,
      "preheat": true
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_d_commuter_ideal_dr / review_vpp2

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.00kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.00kW的可调负荷。 建议采用平衡方案：只在23.0-27.0°C内临时调整，窗口结束后恢复舒适设定；洗衣机安排在13:00开始，运行约2小时，避开18:00-19:00；洗碗机安排在16:00开始，运行约1.5小时，避开18:00-19:00；热水器在15:00-17:00预热，目标55°C，洗浴前保温，避开18:00-19:00。 保护边界：室温策略不得越过用户偏好舒适范围 23.0-27.0°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：本次参考削峰目标约1.00kW；计划优先转移约4.7kW的可控设备负荷。 可选方案包括：保守方案 / 平衡方案 / 增强响应方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp2",
  "vpp_window": {
    "start_h": 42.0,
    "end_h": 43.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 27.0,
    "preferred_min_c": 23.0,
    "preferred_max_c": 27.0,
    "restore_after_h": 43.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 13.0,
      "skip": false,
      "dr_adjustable": true
    },
    "dishwasher": {
      "start_h": 16.0,
      "skip": false,
      "dr_adjustable": true
    },
    "water_heater": {
      "preheat_start_h": 15.0,
      "preheat_end_h": 17.0,
      "preheat_temp_c": 55.0,
      "preheat": true
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_d_commuter_ideal_dr / review_vpp3

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.50kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.50kW的可调负荷。 建议采用平衡方案：只在23.0-27.0°C内临时调整，窗口结束后恢复舒适设定；洗衣机安排在13:00开始，运行约2小时，避开18:00-19:00；洗碗机安排在16:00开始，运行约1.5小时，避开18:00-19:00；热水器在15:00-17:00预热，目标55°C，洗浴前保温，避开18:00-19:00。 保护边界：室温策略不得越过用户偏好舒适范围 23.0-27.0°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：本次参考削峰目标约1.50kW；计划优先转移约4.7kW的可控设备负荷。 可选方案包括：保守方案 / 平衡方案 / 增强响应方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp3",
  "vpp_window": {
    "start_h": 66.0,
    "end_h": 67.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 27.0,
    "preferred_min_c": 23.0,
    "preferred_max_c": 27.0,
    "restore_after_h": 67.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 13.0,
      "skip": false,
      "dr_adjustable": true
    },
    "dishwasher": {
      "start_h": 16.0,
      "skip": false,
      "dr_adjustable": true
    },
    "water_heater": {
      "preheat_start_h": 15.0,
      "preheat_end_h": 17.0,
      "preheat_temp_c": 55.0,
      "preheat": true
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_e_caregiver_low_dr / review_vpp1

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约0.50kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约0.50kW的可调负荷。 建议采用仅提醒方案：空调保持在25.0°C（22.0-25.0°C内），不为VPP越过护理/舒适边界；洗衣机安排在10:00开始，运行约2小时，避开18:00-19:00；热水器保持固定预热17:00-19:00，目标50°C，保障洗浴热水，不把该例程作为削峰资源。 保护边界：室温策略不得越过用户偏好舒适范围 22.0-25.0°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：当前没有可安全转移的可控设备负荷；收益主要来自提醒用户避免在窗口内新增非关键用电。 可选方案包括：保守方案 / 平衡方案 / 仅提醒方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp1",
  "vpp_window": {
    "start_h": 18.0,
    "end_h": 19.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 25.0,
    "preferred_min_c": 22.0,
    "preferred_max_c": 25.0,
    "restore_after_h": 19.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 10.0,
      "skip": false,
      "dr_adjustable": false
    },
    "water_heater": {
      "preheat_start_h": 17.0,
      "preheat_end_h": 19.0,
      "preheat_temp_c": 50.0,
      "preheat": true
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_e_caregiver_low_dr / review_vpp2

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.00kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.00kW的可调负荷。 建议采用仅提醒方案：空调保持在25.0°C（22.0-25.0°C内），不为VPP越过护理/舒适边界；洗衣机安排在10:00开始，运行约2小时，避开18:00-19:00；热水器保持固定预热17:00-19:00，目标50°C，保障洗浴热水，不把该例程作为削峰资源。 保护边界：室温策略不得越过用户偏好舒适范围 22.0-25.0°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：当前没有可安全转移的可控设备负荷；收益主要来自提醒用户避免在窗口内新增非关键用电。 可选方案包括：保守方案 / 平衡方案 / 仅提醒方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp2",
  "vpp_window": {
    "start_h": 42.0,
    "end_h": 43.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 25.0,
    "preferred_min_c": 22.0,
    "preferred_max_c": 25.0,
    "restore_after_h": 43.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 10.0,
      "skip": false,
      "dr_adjustable": false
    },
    "water_heater": {
      "preheat_start_h": 17.0,
      "preheat_end_h": 19.0,
      "preheat_temp_c": 50.0,
      "preheat": true
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_e_caregiver_low_dr / review_vpp3

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.50kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.50kW的可调负荷。 建议采用仅提醒方案：空调保持在25.0°C（22.0-25.0°C内），不为VPP越过护理/舒适边界；洗衣机安排在10:00开始，运行约2小时，避开18:00-19:00；热水器保持固定预热17:00-19:00，目标50°C，保障洗浴热水，不把该例程作为削峰资源。 保护边界：室温策略不得越过用户偏好舒适范围 22.0-25.0°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：当前没有可安全转移的可控设备负荷；收益主要来自提醒用户避免在窗口内新增非关键用电。 可选方案包括：保守方案 / 平衡方案 / 仅提醒方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp3",
  "vpp_window": {
    "start_h": 66.0,
    "end_h": 67.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 25.0,
    "preferred_min_c": 22.0,
    "preferred_max_c": 25.0,
    "restore_after_h": 67.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 10.0,
      "skip": false,
      "dr_adjustable": false
    },
    "water_heater": {
      "preheat_start_h": 17.0,
      "preheat_end_h": 19.0,
      "preheat_temp_c": 50.0,
      "preheat": true
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_f_commuter_ev_optimizer / review_vpp1

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约0.50kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约0.50kW的可调负荷。 建议采用平衡方案：只在24.0-26.0°C内临时调整，窗口结束后恢复舒适设定；洗衣机安排在20:00开始，运行约2小时，避开18:00-19:00；热水器在15:00-17:00预热，目标55°C，洗浴前保温，避开18:00-19:00；EV充电窗口设为19:00-23:54，避开18:00-19:00并保证07:30前达到80% SOC。 保护边界：室温策略不得越过用户偏好舒适范围 24.0-26.0°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：本次参考目标约0.50kW，采用低干扰动作即可，重点是把可控非空调负荷移出窗口。 可选方案包括：保守方案 / 平衡方案 / 增强响应方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp1",
  "vpp_window": {
    "start_h": 18.0,
    "end_h": 19.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 26.0,
    "preferred_min_c": 24.0,
    "preferred_max_c": 26.0,
    "restore_after_h": 19.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 20.0,
      "skip": false,
      "dr_adjustable": true
    },
    "water_heater": {
      "preheat_start_h": 15.0,
      "preheat_end_h": 17.0,
      "preheat_temp_c": 55.0,
      "preheat": true
    },
    "ev": {
      "charge_start_h": 19.0,
      "charge_end_h": 23.9,
      "target_soc": 0.8,
      "departure_h": 7.5
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_f_commuter_ev_optimizer / review_vpp2

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.00kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.00kW的可调负荷。 建议采用平衡方案：只在24.0-26.0°C内临时调整，窗口结束后恢复舒适设定；洗衣机安排在20:00开始，运行约2小时，避开18:00-19:00；热水器在15:00-17:00预热，目标55°C，洗浴前保温，避开18:00-19:00；EV充电窗口设为19:00-23:54，避开18:00-19:00并保证07:30前达到80% SOC。 保护边界：室温策略不得越过用户偏好舒适范围 24.0-26.0°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：本次参考削峰目标约1.00kW；计划优先转移约10.9kW的可控设备负荷。 可选方案包括：保守方案 / 平衡方案 / 增强响应方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp2",
  "vpp_window": {
    "start_h": 42.0,
    "end_h": 43.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 26.0,
    "preferred_min_c": 24.0,
    "preferred_max_c": 26.0,
    "restore_after_h": 43.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 20.0,
      "skip": false,
      "dr_adjustable": true
    },
    "water_heater": {
      "preheat_start_h": 15.0,
      "preheat_end_h": 17.0,
      "preheat_temp_c": 55.0,
      "preheat": true
    },
    "ev": {
      "charge_start_h": 19.0,
      "charge_end_h": 23.9,
      "target_soc": 0.8,
      "departure_h": 7.5
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```

## basic_role_f_commuter_ev_optimizer / review_vpp3

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.50kW的可调负荷。

18:00-19:00是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约1.50kW的可调负荷。 建议采用平衡方案：只在24.0-26.0°C内临时调整，窗口结束后恢复舒适设定；洗衣机安排在20:00开始，运行约2小时，避开18:00-19:00；热水器在15:00-17:00预热，目标55°C，洗浴前保温，避开18:00-19:00；EV充电窗口设为19:00-23:54，避开18:00-19:00并保证07:30前达到80% SOC。 保护边界：室温策略不得越过用户偏好舒适范围 24.0-26.0°C，18:00-19:00结束后自动恢复。 用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。 预期收益：本次参考削峰目标约1.50kW；计划优先转移约10.9kW的可控设备负荷。 可选方案包括：保守方案 / 平衡方案 / 增强响应方案。

Review dimensions:
- why_request: True
- concrete_device_actions: True
- comfort_or_service_constraints: True
- user_control_and_opt_out: True
- benefit_or_compensation: True
- alternatives_2plus: True
- structured_constraints: True
- personalized_to_role: True

Structured constraints:

```json
{
  "event_id": "review_vpp3",
  "vpp_window": {
    "start_h": 66.0,
    "end_h": 67.0,
    "text": "18:00-19:00"
  },
  "hvac": {
    "setpoint_c": 26.0,
    "preferred_min_c": 24.0,
    "preferred_max_c": 26.0,
    "restore_after_h": 67.0,
    "auto_restore": true
  },
  "appliances": {
    "washer": {
      "start_h": 20.0,
      "skip": false,
      "dr_adjustable": true
    },
    "water_heater": {
      "preheat_start_h": 15.0,
      "preheat_end_h": 17.0,
      "preheat_temp_c": 55.0,
      "preheat": true
    },
    "ev": {
      "charge_start_h": 19.0,
      "charge_end_h": 23.9,
      "target_soc": 0.8,
      "departure_h": 7.5
    }
  },
  "hard_constraints": [
    "no_present_controllable_non_ac_load_inside_vpp_window",
    "comfort_and_safety_override_grid_request",
    "user_can_opt_out_or_restore"
  ]
}
```
