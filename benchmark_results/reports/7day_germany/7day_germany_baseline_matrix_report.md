# EnergyBridge Baseline Matrix Report

- Source: `benchmark_results/reports/7day_germany/7day_germany_baseline_matrix_summary.json`
- Jobs: `30`

## Method Averages

| method       | user_pref_score | energy_kwh_total | vpp_window_energy_kwh | appliance_shift_success_rate |
| ------------ | --------------- | ---------------- | --------------------- | ---------------------------- |
| EnergyBridge | 4.1625          | 114.7686         | 7.9815                | 0.7071                       |
| MPC Dynamic  | 2.7             | 113.0691         | 8.462                 | 0.5928                       |
| MPC EP       | 2.5625          | 113.1646         | 8.5446                | 0.5976                       |

## Persona by Method Score Matrix

| persona_id                              | EnergyBridge | MPC Dynamic | MPC EP |
| --------------------------------------- | ------------ | ----------- | ------ |
| atom_comfort_sensitive                  | 4.875        | 2           | 1.5    |
| atom_control_auto                       | 4.625        | 4.375       | 4.125  |
| atom_price_indifferent                  | 3.875        | 2.375       | 2.125  |
| atom_task_rigid                         | 4            | 3.125       | 3.25   |
| basic_role_a_commuter_price_cooperative | 3.875        | 3.25        | 3.125  |
| basic_role_b_home_comfort_gated         | 4.375        | 2.5         | 2.25   |
| basic_role_c_irregular_cautious         | 3.875        | 2.75        | 3.125  |
| basic_role_d_commuter_ideal_dr          | 4.25         | 1.375       | 1.375  |
| basic_role_e_caregiver_low_dr           | 3.875        | 2.375       | 2.25   |
| basic_role_f_commuter_ev_optimizer      | 4            | 2.875       | 2.5    |

## Top 10 Runs by User Score

| persona_id                         | method_label | user_pref_score | energy_kwh_total | vpp_window_energy_kwh | appliance_shift_success_rate | appliance_vpp_avoidance_rate | appliance_task_completion_rate | elapsed_s |
| ---------------------------------- | ------------ | --------------- | ---------------- | --------------------- | ---------------------------- | ---------------------------- | ------------------------------ | --------- |
| atom_comfort_sensitive             | EnergyBridge | 4.875           | 100.0617         | 3.3765                | 1                            | 1                            | 1                              | 120.5     |
| atom_control_auto                  | EnergyBridge | 4.625           | 97.8538          | 3.0602                | 1                            | 1                            | 1                              | 152       |
| atom_control_auto                  | MPC Dynamic  | 4.375           | 97.1336          | 4.3056                | 0.857                        | 0.857                        | 1                              | 26.3      |
| basic_role_b_home_comfort_gated    | EnergyBridge | 4.375           | 99.4929          | 12.8564               | 0.5                          | 0.5                          | 1                              | 371.7     |
| basic_role_d_commuter_ideal_dr     | EnergyBridge | 4.25            | 101.5701         | 2.8134                | 1                            | 1                            | 1                              | 139.4     |
| atom_control_auto                  | MPC EP       | 4.125           | 95.8869          | 3.5946                | 0.929                        | 0.929                        | 1                              | 188       |
| atom_task_rigid                    | EnergyBridge | 4               | 100.072          | 12.9971               | 0.357                        | 0.357                        | 1                              | 152.6     |
| basic_role_f_commuter_ev_optimizer | EnergyBridge | 4               | 242.0318         | 3.0604                | 1                            | 1                            | 1                              | 136.8     |
| basic_role_c_irregular_cautious    | EnergyBridge | 3.875           | 99.482           | 12.8429               | 0.357                        | 0.357                        | 1                              | 91.5      |
| atom_price_indifferent             | EnergyBridge | 3.875           | 100.1306         | 12.8441               | 0.357                        | 0.357                        | 1                              | 93.8      |

## Quick Read

- Best average user score: **EnergyBridge**
- Lowest average total energy: **MPC Dynamic**
- Best average appliance shift success rate: **EnergyBridge**

The matrix is calendar-aware and uses the 3-day benchmark window (Day 1 to Day 3).