# EnergyBridge Baseline Matrix Report

- Source: `benchmark_results/reports/3day_tianjin/3day_tianjin_baseline_matrix_summary.json`
- Jobs: `30`

## Method Averages

| method      | user_pref_score | energy_kwh_total | vpp_window_energy_kwh | appliance_shift_success_rate |
| ----------- | --------------- | ---------------- | --------------------- | ---------------------------- |
| Agent       | 4.1667          | 88.9095          | 4.665                 | 0.75                         |
| MPC Dynamic | 2.2333          | 77.0387          | 4.0935                | 0.6834                       |
| MPC EP      | 2.0333          | 82.9988          | 4.6413                | 0.5167                       |

## Persona by Method Score Matrix

| persona_id                              | Agent | MPC Dynamic | MPC EP |
| --------------------------------------- | ----- | ----------- | ------ |
| atom_comfort_sensitive                  | 5     | 1           | 1      |
| atom_control_auto                       | 5     | 4           | 3.667  |
| atom_price_indifferent                  | 4     | 2           | 2      |
| atom_task_rigid                         | 3.667 | 3.333       | 2.667  |
| basic_role_a_commuter_price_cooperative | 3.667 | 3.333       | 2      |
| basic_role_b_home_comfort_gated         | 4     | 1           | 1      |
| basic_role_c_irregular_cautious         | 3.667 | 2           | 2      |
| basic_role_d_commuter_ideal_dr          | 4.333 | 1           | 1      |
| basic_role_e_caregiver_low_dr           | 4.333 | 1.667       | 2      |
| basic_role_f_commuter_ev_optimizer      | 4     | 3           | 3      |

## Top 10 Runs by User Score

| persona_id                              | method_label | user_pref_score | energy_kwh_total | vpp_window_energy_kwh | appliance_shift_success_rate | appliance_vpp_avoidance_rate | appliance_task_completion_rate | elapsed_s |
| --------------------------------------- | ------------ | --------------- | ---------------- | --------------------- | ---------------------------- | ---------------------------- | ------------------------------ | --------- |
| atom_control_auto                       | Agent        | 5               | 73.5411          | 3.8152                | 1                            | 1                            | 1                              | 0         |
| atom_comfort_sensitive                  | Agent        | 5               | 81.9683          | 4.1805                | 1                            | 1                            | 1                              | 0         |
| basic_role_d_commuter_ideal_dr          | Agent        | 4.3333          | 76.9016          | 3.2065                | 1                            | 1                            | 1                              | 0         |
| basic_role_e_caregiver_low_dr           | Agent        | 4.3333          | 102.9173         | 6.7779                | 0.5                          | 0.5                          | 1                              | 0         |
| atom_control_auto                       | MPC Dynamic  | 4               | 66.377           | 2.996                 | 1                            | 1                            | 1                              | 0         |
| atom_price_indifferent                  | Agent        | 4               | 81.4515          | 5.3208                | 0.5                          | 0.5                          | 1                              | 0         |
| basic_role_b_home_comfort_gated         | Agent        | 4               | 84.5706          | 5.9558                | 0.5                          | 0.5                          | 1                              | 0         |
| basic_role_f_commuter_ev_optimizer      | Agent        | 4               | 135.7527         | 3.8152                | 1                            | 1                            | 1                              | 0         |
| atom_control_auto                       | MPC EP       | 3.6667          | 71.9228          | 4.8898                | 0.5                          | 0.5                          | 1                              | 0         |
| basic_role_a_commuter_price_cooperative | Agent        | 3.6667          | 78.909           | 3.6213                | 1                            | 1                            | 1                              | 0         |

## Quick Read

- Best average user score: **Agent**
- Lowest average total energy: **MPC Dynamic**
- Best average appliance shift success rate: **Agent**

The matrix is calendar-aware and uses the 3-day benchmark window (Day 1 to Day 3).