# EnergyBridge Baseline Matrix Report

- Source: `benchmark_results/reports/7day_germany/7day_germany_baseline_matrix_summary.json`
- Jobs: `40`

## Method Averages

| method       | user_pref_score | energy_kwh_total | vpp_window_energy_kwh | appliance_shift_success_rate |
| ------------ | --------------- | ---------------- | --------------------- | ---------------------------- |
| EnergyBridge | 4.1625          | 114.7686         | 7.9815                | 0.7071                       |
| MPC Dynamic  | 2.7             | 113.0691         | 8.462                 | 0.5928                       |
| MPC EP       | 2.5625          | 113.1646         | 8.5446                | 0.5976                       |
| HEMA Agent   | 2.986           | 110.78           | 10.72                 | 0.745                        |

## Persona by Method Score Matrix

| persona_id                              | EnergyBridge | MPC Dynamic | MPC EP | HEMA Agent |
| --------------------------------------- | ------------ | ----------- | ------ | ---------- |
| atom_comfort_sensitive                  | 4.875        | 2           | 1.5    | 1.43       |
| atom_control_auto                       | 4.625        | 4.375       | 4.125  | 4.29       |
| atom_price_indifferent                  | 3.875        | 2.375       | 2.125  | 4          |
| atom_task_rigid                         | 4            | 3.125       | 3.25   | 3.71       |
| basic_role_a_commuter_price_cooperative | 3.875        | 3.25        | 3.125  | 4          |
| basic_role_b_home_comfort_gated         | 4.375        | 2.5         | 2.25   | 1          |
| basic_role_c_irregular_cautious         | 3.875        | 2.75        | 3.125  | 2.29       |
| basic_role_d_commuter_ideal_dr          | 4.25         | 1.375       | 1.375  | 3.71       |
| basic_role_e_caregiver_low_dr           | 3.875        | 2.375       | 2.25   | 1.57       |
| basic_role_f_commuter_ev_optimizer      | 4            | 2.875       | 2.5    | 3.86       |

## Top 10 Runs by User Score

| persona_id                              | method_label | user_pref_score | energy_kwh_total | vpp_window_energy_kwh | appliance_shift_success_rate | appliance_vpp_avoidance_rate | appliance_task_completion_rate | elapsed_s |
| --------------------------------------- | ------------ | --------------- | ---------------- | --------------------- | ---------------------------- | ---------------------------- | ------------------------------ | --------- |
| atom_comfort_sensitive                  | EnergyBridge | 4.875           | 100.0617         | 3.3765                | 1                            | 1                            | 1                              | 120.5     |
| atom_control_auto                       | EnergyBridge | 4.625           | 97.8538          | 3.0602                | 1                            | 1                            | 1                              | 152       |
| atom_control_auto                       | MPC Dynamic  | 4.375           | 97.1336          | 4.3056                | 0.857                        | 0.857                        | 1                              | 26.3      |
| basic_role_b_home_comfort_gated         | EnergyBridge | 4.375           | 99.4929          | 12.8564               | 0.5                          | 0.5                          | 1                              | 371.7     |
| atom_control_auto                       | HEMA Agent   | 4.29            | 99               | 2.78                  | 1                            | 1                            |                                |           |
| basic_role_d_commuter_ideal_dr          | EnergyBridge | 4.25            | 101.5701         | 2.8134                | 1                            | 1                            | 1                              | 139.4     |
| atom_control_auto                       | MPC EP       | 4.125           | 95.8869          | 3.5946                | 0.929                        | 0.929                        | 1                              | 188       |
| atom_price_indifferent                  | HEMA Agent   | 4               | 95.2             | 7.92                  | 0.5                          | 0.5                          |                                |           |
| basic_role_a_commuter_price_cooperative | HEMA Agent   | 4               | 99.2             | 2.78                  | 1                            | 1                            |                                |           |
| atom_task_rigid                         | EnergyBridge | 4               | 100.072          | 12.9971               | 0.357                        | 0.357                        | 1                              | 152.6     |

## Quick Read

- Best average user score: **EnergyBridge**
- Lowest average total energy: **HEMA Agent**
- Best average appliance shift success rate: **HEMA Agent**

The matrix is calendar-aware and uses the 7-day benchmark window (Day 1 to Day 7).