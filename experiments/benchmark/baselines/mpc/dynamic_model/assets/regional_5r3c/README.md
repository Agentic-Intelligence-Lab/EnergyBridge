# Regional 5R3C Dynamics Assets

The benchmark keeps two dynamics-model regions:

- `tianjin`: the legacy Tianjin assets under `thermal_improvement_experiments/04_5r3c_hvac_solar`.
- `berlin`: the Germany/Berlin assets imported from `reference/0707/New_Dynamic_Model.zip`.

`DynamicModelScorer` selects `berlin` when the benchmark state city/weather is
Germany or Berlin. Other cities default to the legacy Tianjin model.
