# EnergyBridge Baseline Matrix Report

- Source: `benchmark_results/reports/7day_tianjin/7day_tianjin_baseline_matrix_summary.json`
- Jobs: `30`

## Method Averages

| method       | user_pref_score | energy_kwh_total | vpp_window_energy_kwh | appliance_shift_success_rate |
| ------------ | --------------- | ---------------- | --------------------- | ---------------------------- |
| EnergyBridge | 3.9375          | 213.4519         | 13.9191               | 0.7023                       |
| MPC Dynamic  | 2.15            | 183.4383         | 12.5627               | 0.5928                       |
| MPC EP       | 2.2625          | 196.225          | 12.9927               | 0.5499                       |

## Persona by Method Score Matrix

| persona_id                              | EnergyBridge | MPC Dynamic | MPC EP |
| --------------------------------------- | ------------ | ----------- | ------ |
| atom_comfort_sensitive                  | 4.75         | 1           | 1.125  |
| atom_control_auto                       | 4.375        | 3.375       | 3.125  |
| atom_price_indifferent                  | 2.875        | 2           | 2      |
| atom_task_rigid                         | 3.75         | 2.75        | 3      |
| basic_role_a_commuter_price_cooperative | 3.75         | 2.875       | 3      |
| basic_role_b_home_comfort_gated         | 4.5          | 1.25        | 2      |
| basic_role_c_irregular_cautious         | 3.5          | 2.875       | 3      |
| basic_role_d_commuter_ideal_dr          | 4.125        | 1.375       | 1.125  |
| basic_role_e_caregiver_low_dr           | 3.75         | 1.5         | 1.875  |
| basic_role_f_commuter_ev_optimizer      | 4            | 2.5         | 2.375  |

## Top 10 Runs by User Score

| persona_id                              | method_label | user_pref_score | energy_kwh_total | vpp_window_energy_kwh | appliance_shift_success_rate | appliance_vpp_avoidance_rate | appliance_task_completion_rate | elapsed_s |
| --------------------------------------- | ------------ | --------------- | ---------------- | --------------------- | ---------------------------- | ---------------------------- | ------------------------------ | --------- |
| atom_comfort_sensitive                  | EnergyBridge | 4.75            | 210.8134         | 10.8541               | 1                            | 1                            | 1                              | 257.8     |
| basic_role_b_home_comfort_gated         | EnergyBridge | 4.5             | 208.7862         | 19.3216               | 0.5                          | 0.5                          | 1                              | 96        |
| atom_control_auto                       | EnergyBridge | 4.375           | 191.4516         | 9.7367                | 1                            | 1                            | 1                              | 427.5     |
| basic_role_d_commuter_ideal_dr          | EnergyBridge | 4.125           | 175.3576         | 6.685                 | 1                            | 1                            | 1                              | 155.6     |
| basic_role_f_commuter_ev_optimizer      | EnergyBridge | 4               | 334.1833         | 10.3551               | 1                            | 1                            | 1                              | 141.1     |
| basic_role_a_commuter_price_cooperative | EnergyBridge | 3.75            | 193.7378         | 8.5053                | 0.952                        | 0.952                        | 1                              | 0         |
| atom_task_rigid                         | EnergyBridge | 3.75            | 193.9632         | 18.7791               | 0.357                        | 0.357                        | 1                              | 87.5      |
| basic_role_e_caregiver_low_dr           | EnergyBridge | 3.75            | 218.4147         | 19.4011               | 0.5                          | 0.5                          | 1                              | 91.8      |
| basic_role_c_irregular_cautious         | EnergyBridge | 3.5             | 200.6372         | 17.5509               | 0.357                        | 0.357                        | 1                              | 85.7      |
| atom_control_auto                       | MPC Dynamic  | 3.375           | 161.4999         | 9.0539                | 0.857                        | 0.857                        | 1                              | 111.6     |

## Quick Read

- Best average user score: **EnergyBridge**
- Lowest average total energy: **MPC Dynamic**
- Best average appliance shift success rate: **EnergyBridge**

The matrix is calendar-aware and uses the 3-day benchmark window (Day 1 to Day 3).