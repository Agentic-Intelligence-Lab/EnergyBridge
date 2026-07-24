## File Descriptions

### `data/june_2025_{germany,tianjin}_with_preload.json`

- **Purpose**: Source dataset (June) — cleaned historical demand response (DR)
  events, including pre-event load, per city.
- **Key fields**: `memory_event_id`, `household_id`, `realized_delivery_kwh`,
  `pre_event_load_kw`, `no_dr_baseline_kwh`, `temp_pre_1h`, `temp_vpp`, etc.

### `data/july_2025_{germany,tianjin}_with_preload.json`

- **Purpose**: Target dataset (July 1-31) — same format as the June source,
  per city.

### `IS_result/weights_package_{germany,tianjin}_6to7.json`

- **Purpose**: Output of importance sampling (June -> July), one package per
  city.
- **Contents**: estimator name, feature set, per-event `raw_weight` keyed by
  `memory_event_id`, and validation metrics (ESS, weight range). Regenerate
  via `IS_weather_and_preload.py` (below) rather than hand-editing; current
  numbers are whatever the last run produced.

### `IS_result/all_schemes_comparison.json`

- **Purpose**: Benchmark of all tested feature schemes for the currently
  configured city.
- **Metrics per scheme**: `ess`, `mae_is`, `mae_baseline`,
  `mae_improvement_pct`, `weight_stats`.

### `IS_weather_and_preload.py`

- **Purpose**: Main implementation script -- loads the source/target JSON
  data for one city, computes importance weights across feature schemes,
  selects the best one, and writes `IS_result/weights_package_<city>_6to7.json`
  plus `IS_result/all_schemes_comparison.json`.
- **Usage**: set `USE_CITY = "Germany"` or `"Tianjin"` near the top of the
  file, then run:
  ```bash
  python importance_sampling/IS_weather_and_preload.py
  ```
  Run it once per city; each run overwrites that city's `weights_package_*`
  and the comparison file.

## Consuming the weights elsewhere

`experiments/benchmark/run_capacity_shed_evaluation.py --apply-is` reads
`IS_result/weights_package_{city}_6to7.json` and reweights the capacity
report's top-k quantile by each neighbor's `raw_weight` -- see the top-level
`dr_capacity_memory_toolkit/README.md` for that script's usage. It currently
only has June->July packages for Germany and Tianjin, so `--apply-is` is a
no-op for any other city/month pair.
