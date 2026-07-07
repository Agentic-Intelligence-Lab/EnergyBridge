# VPP Strategy Explanation Review Data

Generated: 2026-07-07 14:48:37
Records: 18

## basic_role_a_commuter_price_cooperative / review_vpp1

- City: Germany
- Method: EnergyBridge
- Score: N/A
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 0.50 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 0.50 kW of adjustable load during this peak period. Recommended strategy: Balanced option. Temporarily adjust only within 24.0-26.0°C, then restore comfort after the VPP window; Start the washer at 19:00 for about 2 h, avoiding 18:00-19:00; Start the dishwasher at 21:00 for about 1.5 h, avoiding 18:00-19:00; Preheat the water heater from 15:00 to 17:00 at 55°C; store heat before bath time and avoid 18:00-19:00. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 24.0-26.0°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: The reference target is about 0.50 kW, so low-disruption actions are enough; focus on moving controllable non-AC load out of the window. Options include: Conservative option / Balanced option / Enhanced response option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.00 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.00 kW of adjustable load during this peak period. Recommended strategy: Balanced option. Temporarily adjust only within 24.0-26.0°C, then restore comfort after the VPP window; Start the washer at 19:00 for about 2 h, avoiding 18:00-19:00; Start the dishwasher at 21:00 for about 1.5 h, avoiding 18:00-19:00; Preheat the water heater from 15:00 to 17:00 at 55°C; store heat before bath time and avoid 18:00-19:00. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 24.0-26.0°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: The reference shedding target is about 1.00 kW; the plan prioritizes shifting roughly 4.7 kW of controllable device load. Options include: Conservative option / Balanced option / Enhanced response option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.50 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.50 kW of adjustable load during this peak period. Recommended strategy: Balanced option. Temporarily adjust only within 24.0-26.0°C, then restore comfort after the VPP window; Start the washer at 19:00 for about 2 h, avoiding 18:00-19:00; Start the dishwasher at 21:00 for about 1.5 h, avoiding 18:00-19:00; Preheat the water heater from 15:00 to 17:00 at 55°C; store heat before bath time and avoid 18:00-19:00. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 24.0-26.0°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: The reference shedding target is about 1.50 kW; the plan prioritizes shifting roughly 4.7 kW of controllable device load. Options include: Conservative option / Balanced option / Enhanced response option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 0.50 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 0.50 kW of adjustable load during this peak period. Recommended strategy: Balanced option. Temporarily adjust only within 24.5-25.5°C, then restore comfort after the VPP window; Start the washer at 10:00 for about 2 h, avoiding 18:00-19:00; Keep the fixed water-heater preheat window 18:00-20:00 at 50°C; protect bath-time hot water and do not use this routine as a shedding resource. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 24.5-25.5°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: No controllable device load can be safely shifted now; the benefit is mainly avoiding new noncritical load during the window. Options include: Conservative option / Balanced option / Enhanced response option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.00 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.00 kW of adjustable load during this peak period. Recommended strategy: Balanced option. Temporarily adjust only within 24.5-25.5°C, then restore comfort after the VPP window; Start the washer at 10:00 for about 2 h, avoiding 18:00-19:00; Keep the fixed water-heater preheat window 18:00-20:00 at 50°C; protect bath-time hot water and do not use this routine as a shedding resource. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 24.5-25.5°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: No controllable device load can be safely shifted now; the benefit is mainly avoiding new noncritical load during the window. Options include: Conservative option / Balanced option / Enhanced response option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.50 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.50 kW of adjustable load during this peak period. Recommended strategy: Balanced option. Temporarily adjust only within 24.5-25.5°C, then restore comfort after the VPP window; Start the washer at 10:00 for about 2 h, avoiding 18:00-19:00; Keep the fixed water-heater preheat window 18:00-20:00 at 50°C; protect bath-time hot water and do not use this routine as a shedding resource. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 24.5-25.5°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: No controllable device load can be safely shifted now; the benefit is mainly avoiding new noncritical load during the window. Options include: Conservative option / Balanced option / Enhanced response option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 0.50 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 0.50 kW of adjustable load during this peak period. Recommended strategy: Balanced option. Temporarily adjust only within 24.0-26.0°C, then restore comfort after the VPP window; Start the washer at 19:00 for about 2 h, avoiding 18:00-19:00; Keep the fixed water-heater preheat window 18:00-20:00 at 50°C; protect bath-time hot water and do not use this routine as a shedding resource. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 24.0-26.0°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: No controllable device load can be safely shifted now; the benefit is mainly avoiding new noncritical load during the window. Options include: Conservative option / Balanced option / Enhanced response option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.00 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.00 kW of adjustable load during this peak period. Recommended strategy: Balanced option. Temporarily adjust only within 24.0-26.0°C, then restore comfort after the VPP window; Start the washer at 19:00 for about 2 h, avoiding 18:00-19:00; Keep the fixed water-heater preheat window 18:00-20:00 at 50°C; protect bath-time hot water and do not use this routine as a shedding resource. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 24.0-26.0°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: No controllable device load can be safely shifted now; the benefit is mainly avoiding new noncritical load during the window. Options include: Conservative option / Balanced option / Enhanced response option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.50 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.50 kW of adjustable load during this peak period. Recommended strategy: Balanced option. Temporarily adjust only within 24.0-26.0°C, then restore comfort after the VPP window; Start the washer at 19:00 for about 2 h, avoiding 18:00-19:00; Keep the fixed water-heater preheat window 18:00-20:00 at 50°C; protect bath-time hot water and do not use this routine as a shedding resource. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 24.0-26.0°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: No controllable device load can be safely shifted now; the benefit is mainly avoiding new noncritical load during the window. Options include: Conservative option / Balanced option / Enhanced response option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 0.50 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 0.50 kW of adjustable load during this peak period. Recommended strategy: Balanced option. Temporarily adjust only within 23.0-27.0°C, then restore comfort after the VPP window; Start the washer at 13:00 for about 2 h, avoiding 18:00-19:00; Start the dishwasher at 16:00 for about 1.5 h, avoiding 18:00-19:00; Preheat the water heater from 15:00 to 17:00 at 55°C; store heat before bath time and avoid 18:00-19:00. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 23.0-27.0°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: The reference target is about 0.50 kW, so low-disruption actions are enough; focus on moving controllable non-AC load out of the window. Options include: Conservative option / Balanced option / Enhanced response option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.00 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.00 kW of adjustable load during this peak period. Recommended strategy: Balanced option. Temporarily adjust only within 23.0-27.0°C, then restore comfort after the VPP window; Start the washer at 13:00 for about 2 h, avoiding 18:00-19:00; Start the dishwasher at 16:00 for about 1.5 h, avoiding 18:00-19:00; Preheat the water heater from 15:00 to 17:00 at 55°C; store heat before bath time and avoid 18:00-19:00. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 23.0-27.0°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: The reference shedding target is about 1.00 kW; the plan prioritizes shifting roughly 4.7 kW of controllable device load. Options include: Conservative option / Balanced option / Enhanced response option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.50 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.50 kW of adjustable load during this peak period. Recommended strategy: Balanced option. Temporarily adjust only within 23.0-27.0°C, then restore comfort after the VPP window; Start the washer at 13:00 for about 2 h, avoiding 18:00-19:00; Start the dishwasher at 16:00 for about 1.5 h, avoiding 18:00-19:00; Preheat the water heater from 15:00 to 17:00 at 55°C; store heat before bath time and avoid 18:00-19:00. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 23.0-27.0°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: The reference shedding target is about 1.50 kW; the plan prioritizes shifting roughly 4.7 kW of controllable device load. Options include: Conservative option / Balanced option / Enhanced response option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 0.50 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 0.50 kW of adjustable load during this peak period. Recommended strategy: Advisory-only option. Keep the AC setpoint at 25.0°C within 22.0-25.0°C; do not cross caregiving or comfort boundaries for the VPP event; Start the washer at 10:00 for about 2 h, avoiding 18:00-19:00; Keep the fixed water-heater preheat window 17:00-19:00 at 50°C; protect bath-time hot water and do not use this routine as a shedding resource. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 22.0-25.0°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: No controllable device load can be safely shifted now; the benefit is mainly avoiding new noncritical load during the window. Options include: Conservative option / Balanced option / Advisory-only option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.00 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.00 kW of adjustable load during this peak period. Recommended strategy: Advisory-only option. Keep the AC setpoint at 25.0°C within 22.0-25.0°C; do not cross caregiving or comfort boundaries for the VPP event; Start the washer at 10:00 for about 2 h, avoiding 18:00-19:00; Keep the fixed water-heater preheat window 17:00-19:00 at 50°C; protect bath-time hot water and do not use this routine as a shedding resource. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 22.0-25.0°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: No controllable device load can be safely shifted now; the benefit is mainly avoiding new noncritical load during the window. Options include: Conservative option / Balanced option / Advisory-only option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.50 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.50 kW of adjustable load during this peak period. Recommended strategy: Advisory-only option. Keep the AC setpoint at 25.0°C within 22.0-25.0°C; do not cross caregiving or comfort boundaries for the VPP event; Start the washer at 10:00 for about 2 h, avoiding 18:00-19:00; Keep the fixed water-heater preheat window 17:00-19:00 at 50°C; protect bath-time hot water and do not use this routine as a shedding resource. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 22.0-25.0°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: No controllable device load can be safely shifted now; the benefit is mainly avoiding new noncritical load during the window. Options include: Conservative option / Balanced option / Advisory-only option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 0.50 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 0.50 kW of adjustable load during this peak period. Recommended strategy: Balanced option. Temporarily adjust only within 24.0-26.0°C, then restore comfort after the VPP window; Start the washer at 20:00 for about 2 h, avoiding 18:00-19:00; Preheat the water heater from 15:00 to 17:00 at 55°C; store heat before bath time and avoid 18:00-19:00; Set the EV charging window to 19:00-23:54, avoid 18:00-19:00, and reach 80% SOC before 07:30. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 24.0-26.0°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: The reference target is about 0.50 kW, so low-disruption actions are enough; focus on moving controllable non-AC load out of the window. Options include: Conservative option / Balanced option / Enhanced response option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.00 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.00 kW of adjustable load during this peak period. Recommended strategy: Balanced option. Temporarily adjust only within 24.0-26.0°C, then restore comfort after the VPP window; Start the washer at 20:00 for about 2 h, avoiding 18:00-19:00; Preheat the water heater from 15:00 to 17:00 at 55°C; store heat before bath time and avoid 18:00-19:00; Set the EV charging window to 19:00-23:54, avoid 18:00-19:00, and reach 80% SOC before 07:30. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 24.0-26.0°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: The reference shedding target is about 1.00 kW; the plan prioritizes shifting roughly 10.9 kW of controllable device load. Options include: Conservative option / Balanced option / Enhanced response option.

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
- Why: 18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.50 kW of adjustable load during this peak period.

18:00-19:00 is a VPP demand-response window; the grid is asking the household to reduce about 1.50 kW of adjustable load during this peak period. Recommended strategy: Balanced option. Temporarily adjust only within 24.0-26.0°C, then restore comfort after the VPP window; Start the washer at 20:00 for about 2 h, avoiding 18:00-19:00; Preheat the water heater from 15:00 to 17:00 at 55°C; store heat before bath time and avoid 18:00-19:00; Set the EV charging window to 19:00-23:54, avoid 18:00-19:00, and reach 80% SOC before 07:30. Protected boundary: Indoor-temperature control must stay within the user's preferred comfort range 24.0-26.0°C and auto-restore after 18:00-19:00. The user can cancel, pause, or switch to the conservative option before or during the event. Expected benefit: The reference shedding target is about 1.50 kW; the plan prioritizes shifting roughly 10.9 kW of controllable device load. Options include: Conservative option / Balanced option / Enhanced response option.

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
