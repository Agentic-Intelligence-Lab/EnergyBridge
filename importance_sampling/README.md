## File Descriptions

### `data/clean_records_285_with_preload.json`

- **Purpose**: Source dataset — cleaned historical demand response (DR) events (June), including pre-event load.
- **Key fields**: `memory_event_id`, `household_id`, `realized_delivery_kwh`, `pre_event_load_kw`, `no_dr_baseline_kwh`, `temp_pre_1h`, `temp_vpp`, etc.

### `data/july_events_31days.json`

- **Purpose**: Target dataset — DR events from July 1–31.
- **Structure**: Matches source format.

### `IS_result/weights_package_germany_6to7.json`

- **Purpose**: Output of importance sampling (Jun → Jul, Germany).
- **Contents**:
  - Estimator: `LogisticDensityRatio`
  - Feature set: `[pre_event_load_kw, temp_pre_1h, temp_vpp]`
  - ESS = 0.9635, weight range [0.641, 1.3502]

### `IS_result/all_schemes_comparison.json`

- **Purpose**: Benchmark of all tested feature schemes.
- **Best scheme**: Scheme 5 — *Pre-event load + pre-1h temp + VPP-window temp* (3D, hourly)
- **Metrics per scheme**: `ess`, `mae_is`, `mae_baseline`, `mae_improvement_pct`, `weight_stats`.

### `IS_weather_and_preload.py`

- **Purpose**: Main implementation script.
- **Functionality**:
  - Loads source/target JSON data.
  - Computes importance weights.
  - Evaluates multiple schemes and selects optimal one.
  - Outputs weighted predictions and validation metrics.
