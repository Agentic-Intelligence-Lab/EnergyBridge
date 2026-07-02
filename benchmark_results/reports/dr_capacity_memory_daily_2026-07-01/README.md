# DR 容量上报历史库归档

本目录保存 2026-07-01 生成的正式 daily-split DR memory 数据。历史库不是一次性跑 30 天，而是把 30 天中的每一天作为独立样本运行：

```text
30 天 x 5 个大家庭 x 2 个城市 x 2 个方法(no_dr, eb_rule_milp) = 600 个 1-day run
```

原始 run 目录仍保留在：

```text
/home/hku_user/work/EnergyBridge/benchmark_results/2026-07-01_agent_dr_memory_daily/
```

## 文件说明

`daily_memory_data/`

- `daily_dr_memory_summary_raw.json/csv`: 600 个 daily run 的原始矩阵汇总。
- `daily_dr_memory_summary_with_counterfactual.json/csv`: 已匹配 no-DR 反事实 baseline 后的汇总。
- `daily_dr_memory_no_dr_counterfactual_library.json`: no-DR 反事实基线库。
- `eb_rule_milp_daily_dr_memory.json`: EB+rule+MILP 的历史 DR memory，共 300 个事件。

`capacity_report/`

- `household_matrix_summary_eb_rule_milp_agent_capacity_report_daily_top5_dryrun_7days_H6.json/csv`: 用 daily memory 对 7-day 目标结果做 top-5 分布容量上报的结果。
- 该 summary 显式包含容量 band 选择字段，例如 `agent_capacity_report_primary_distribution_position`、`agent_capacity_report_distribution_position_counts`、`agent_capacity_report_primary_choice`。其中 `p25/p50/p75` 分别对应保守/校准/激进的历史交付分布位置。

`source_config/`

- `vpp_events_june_memory.json`: 30 天历史 DR 事件配置。

`logs/`

- `daily_dr_memory_pipeline.log`: 600 个 daily run 的执行日志。

## 当前 top-5 分布容量上报结果

使用 top-5 历史事件分布，按同城市、同家庭、同方法、同 VPP 小时优先检索，并使用 no-DR baseline 做温和修正。dry-run 表示 agent 固定选 calibrated/P50 band，没有额外 LLM 调用。

```text
Germany: reported_avg_kw = 3.2769, actual_avg_kw = 3.9842, delivery_ratio = 1.2158
Tianjin: reported_avg_kw = 3.6076, actual_avg_kw = 3.3280, delivery_ratio = 0.9225
All    : reported_avg_kw = 3.4423, actual_avg_kw = 3.6561, delivery_ratio = 1.0621
```

`delivery_ratio = actual delivered kW / reported capacity kW`。
