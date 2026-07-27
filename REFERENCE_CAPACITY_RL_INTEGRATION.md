# Reference Capacity and RL Integration

## Merged functionality

- The reference state-constrained DR capacity estimator is available under
  `energybridge.quantification`.
- `ApplianceSuite` current state is adapted into the estimator schema before
  each family VPP event. The adapter does not modify the reference estimator or
  add empirical capacity.
- The household sends committable capacity, a conservative recommended bid,
  success probability, rebound estimate, and constraints to the VPP demand
  agent.
- The VPP target is bounded so it cannot request more reduction than the
  household's recommended bid.
- The reference Typical Human Gymnasium baseline, random policy, and PPO smoke
  experiment live independently under `baselines/rl_typical_human`.
- The RL baseline receives the reference estimator's committable capacity,
  recommended bid, and success probability as observation features. Per-event
  capacity assessments and named RL actions are written automatically to its
  metrics summary.

## Known capacity integration issues

- `ApplianceSuite` stores the previous timestep's device power. At the 18:00
  event boundary, a water-heater preheat schedule ending at 18:00 may therefore
  still appear to be running and can be counted as shed capacity.
- The family appliance simulator does not expose a physical water-tank
  temperature. The adapter must currently use the configured setpoint as the
  observation, which limits the reference estimator's thermal-slack accuracy.
- HVAC is not represented as a device supported by the reference capacity
  estimator. It is intentionally excluded rather than estimated with a new
  empirical formula.
- A zero capacity can be correct when all tasks are already finished and no
  controllable device is currently drawing power. It can also expose the
  observation limitations above; these cases should be distinguished before
  interpreting zero as no household flexibility.

## Simulator versions

The reference bundle targets Sinergym 3.12 and EnergyPlus 25.1 with an epJSON
model. Configure the standard EnergyPlus 25.1 installation through
`EPLUS_ROOT`. A separately installed special build may require a newer glibc.

EnergyBridge's family and office benchmarks continue to use EnergyPlus 24.1
through the native Python API. The three-day RL baseline uses the same 24.1
family model for direct comparison, while the Sinergym reference path remains
independent on 25.1. The Typical Human environment is explicitly lightweight
and does not launch EnergyPlus.

## Validation

Capacity smoke:

```bash
PYTHONPATH=. python -c "from energybridge.quantification import assess_suite_vpp_request"
```

RL smoke:

```bash
conda activate energybridge
python -m baselines.rl_typical_human.run_experiment \
  --ppo-timesteps 64 --persona atom_comfort_sensitive
```

Formal PPO training:

```bash
python -m baselines.rl_energyplus.train \
  --hours 4 --device cuda \
  --output benchmark_results/rl_energyplus_formal
```

See `baselines/rl_energyplus/README.md` for the environment contract,
resume workflow, generated metrics, known issues, and tuning priorities.
