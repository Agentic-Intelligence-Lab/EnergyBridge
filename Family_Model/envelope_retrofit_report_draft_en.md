# Family Model Envelope Retrofit Report Draft

## 1. Purpose

This report documents the proposed retrofit path for replacing the original EnergyPlus example-house envelope constructions with China-oriented residential envelope assemblies. It does not modify the IDF yet. It records the original constructions, proposed replacement constructions, key thermal parameters, reasons, and standards basis.

Target model:

- IDF: `SingleFamilyHouse_HP_Slab.idf`
- Local path: `/jupyterfile/Building_Model/Family_Model/SingleFamilyHouse_HP_Slab.idf`
- Source: EnergyPlus 24.1 ExampleFiles
- Current thermal zones: `living_unit1` and `attic_unit1`
- Current HVAC: one ducted air-to-air unitary heat pump serving `living_unit1`

## 2. Standards Basis

Primary standards and references are archived in `/jupyterfile/Building_Model/Standard`.

- GB 55015-2021 建筑节能与可再生能源利用通用规范: current mandatory national general code. The MOHURD announcement states that this is a mandatory engineering construction code, all provisions are mandatory, and where existing standards conflict with it, GB 55015-2021 governs.
- GB 50176-2016 民用建筑热工设计规范: thermal design method, climate zoning, indoor thermal calculation parameters, surface heat transfer resistances, and material thermal-physical parameters.
- JGJ 26-2018 严寒和寒冷地区居住建筑节能设计标准: used as cold/severe-cold residential design reference. GB 55015 remains the top-level mandatory basis.
- JGJ 134-2010 and JGJ 75-2012: kept as backup references for hot-summer/cold-winter and hot-summer/warm-winter cities.

Working assumption for this first retrofit proposal:

- Climate workflow: cold-region low-rise residence, compatible with a Tianjin-style future simulation.
- Building height class: `<=3 floors`, because the model represents a two-storey living zone plus attic.
- Main GB 55015 envelope targets used here:
  - Cold A low-rise roof: `K <= 0.25 W/(m2*K)`.
  - Cold A/B low-rise exterior wall: `K <= 0.35 W/(m2*K)`.
  - Cold A/B low-rise exterior floor / exposed floor: `K <= 0.35 W/(m2*K)`.
  - Cold A/B low-rise perimeter ground insulation: insulation layer `R >= 1.60 m2*K/W` for Cold A; Cold B is not stricter for this model.
  - Cold A/B low-rise exterior window: `K <= 1.80 W/(m2*K)` when window-wall ratio is not high; use `K <= 1.50 W/(m2*K)` as the conservative target if any facade segment falls in the higher window-wall-ratio bin.
  - Cold B summer east/west SHGC should be controlled when relevant; the proposed glazing keeps SHGC below `0.55`.

If the target city is later changed to Shenzhen or another hot-summer/warm-winter city, the retrofit priority should shift from heating U-value control to solar heat-gain control, exterior shading, roof/wall heat inertia, and natural ventilation.

## 3. Existing Envelope Summary

The original model is a US-style lightweight detached-house envelope. It is already thermally strong in some places, but its material system is not China-oriented.

### 3.1 Exterior Wall

Current EnergyPlus construction: `Exterior Wall`

Layers:

1. `syn_stucco`
2. `sheathing_consol_layer`
3. `OSB_7/16in`
4. `wall_consol_layer`
5. `Drywall_1/2in`

Approximate current layer-only thermal resistance:

- `R ~= 3.53 m2*K/W`
- `K ~= 0.28 W/(m2*K)` without surface films

Assessment:

- Thermally, the wall is already better than the cold low-rise target of `K <= 0.35 W/(m2*K)`.
- Materially, it is a US lightweight wall with OSB, stucco, consolidated insulation/stud layers, and drywall. It should be replaced with a Chinese residential wall assembly for local interpretability.

### 3.2 Roof and Attic Boundary

Current external attic roof construction: `Exterior Roof`

Layers:

1. `Asphalt_shingle`
2. `OSB_1/2in`

Approximate current layer-only thermal resistance:

- `R ~= 0.19 m2*K/W`
- `K ~= 5.36 W/(m2*K)` without surface films

Current living-to-attic ceiling construction: `Interior Ceiling`

Layers:

1. `ceil_consol_layer`
2. `Drywall_1/2in`

Approximate current layer-only thermal resistance:

- `R ~= 7.28 m2*K/W`
- `K ~= 0.14 W/(m2*K)` without surface films

Assessment:

- If the attic remains unconditioned, the true thermal boundary for the living space is the insulated ceiling between `living_unit1` and `attic_unit1`, which is already strong.
- If the attic becomes conditioned later, the roof and gable walls must be upgraded, because the current roof itself is almost uninsulated.

### 3.3 Ground Slab / Floor

Current surface construction: `Interior Floor`

Layers:

1. `Plywood_3/4in`
2. `Carpet_n_pad`

Ground coupling is handled by EnergyPlus `GroundHeatTransfer:Slab:*` objects rather than by a normal exterior floor construction. The existing slab settings include vertical insulation with `RVINS ~= 1.761 m2*K/W`, which is already slightly above the cold-region perimeter insulation target of `R >= 1.60 m2*K/W`.

Assessment:

- The existing numerical slab edge insulation is acceptable for the cold-region target.
- The material naming and assembly should still be localized to a reinforced concrete slab plus perimeter XPS insulation description.

### 3.4 Exterior Windows

Current EnergyPlus construction: `Exterior Window`

Material:

- `Glass`: SimpleGlazingSystem
- `U = 1.70358 W/(m2*K)`
- `SHGC = 0.3344`
- `VT = 0.88`

Assessment:

- The current window U-value meets `K <= 1.80 W/(m2*K)`.
- The SHGC is low enough for summer solar control.
- For Chinese residential interpretation, it should be renamed and parameterized as Low-E insulated glazing rather than generic `Glass`.

### 3.5 Exterior Door

Current construction: `Exterior Door`

Layer:

- `door_const`

Approximate current layer-only thermal resistance:

- `R ~= 0.587 m2*K/W`
- `K ~= 1.70 W/(m2*K)`

Assessment:

- This is acceptable for a thermally insulated entrance door in the current model.

## 4. Proposed China-Oriented Envelope Assemblies

These proposed assemblies are intended to preserve stable simulation behavior while making the model understandable as a Chinese residential building.

### 4.1 Exterior Wall Replacement

Proposed construction name:

- `CN_ExteriorWall_AAC_RockWool_Cold`

Proposed layers, outside to inside:

1. 20 mm exterior cement mortar / protective render
2. 70 mm rock wool board or graphite EPS external insulation
3. 200 mm autoclaved aerated concrete block, or equivalent light masonry infill wall
4. 20 mm interior plaster / gypsum finish

Nominal parameters:

- Insulation conductivity: `0.040-0.045 W/(m*K)`.
- AAC block conductivity: about `0.16 W/(m*K)`.
- Estimated total U-value including conventional surface films: about `0.32-0.35 W/(m2*K)`.

Reason:

- Meets the cold low-rise target `K <= 0.35 W/(m2*K)`.
- Converts the US lightweight OSB/drywall wall to a Chinese masonry plus external-insulation wall system.
- External insulation reduces thermal bridge risk compared with internal-only insulation.

### 4.2 Living-to-Attic Ceiling / Roof Thermal Boundary

If attic remains unconditioned, modify the ceiling/attic-floor boundary:

Proposed construction name:

- `CN_AtticFloor_RockWool_Cold`

Proposed layers, living side to attic side:

1. 12 mm gypsum board or plaster layer
2. 180 mm mineral wool / glass wool insulation
3. 100-120 mm reinforced concrete or lightweight structural floor layer, if using a China-style floor/ceiling interpretation

Nominal parameters:

- Mineral wool conductivity: about `0.040-0.045 W/(m*K)`.
- Estimated U-value including conventional surface films: about `0.22-0.25 W/(m2*K)`.

Reason:

- Meets the cold low-rise roof/upper-envelope target `K <= 0.25 W/(m2*K)`.
- Keeps attic as an unconditioned buffer, which is consistent with the current model topology.

If attic becomes conditioned later, upgrade the external roof instead:

Proposed construction name:

- `CN_Roof_XPS_Cold`

Proposed layers, outside to inside:

1. Roof tile or protective layer
2. Waterproofing layer
3. 120 mm XPS insulation
4. 100-120 mm reinforced concrete roof slab
5. Interior plaster

Nominal U-value:

- About `0.23-0.25 W/(m2*K)`.

### 4.3 Ground Slab and Perimeter Insulation

Proposed construction / setting:

- `CN_GroundSlab_PerimeterXPS_Cold`

Proposed assembly / EnergyPlus mapping:

1. 100 mm concrete slab
2. Interior finish layer
3. Perimeter vertical XPS insulation, 50 mm, conductivity about `0.030 W/(m*K)`
4. Keep or set slab vertical insulation resistance `RVINS >= 1.60 m2*K/W`

Nominal perimeter insulation:

- 50 mm XPS gives `R ~= 1.67 m2*K/W`.

Reason:

- Matches GB 55015 cold-region perimeter ground insulation logic.
- The current model already has `RVINS ~= 1.761`; this proposal mainly localizes the material interpretation.

### 4.4 Exterior Windows

Proposed construction name:

- `CN_LowE_Insulated_Window_Cold`

Recommended EnergyPlus simple glazing parameters:

- `U-Factor = 1.60 W/(m2*K)` for conservative cold-region compliance.
- `SHGC = 0.40-0.45`.
- `Visible Transmittance = 0.60`.

Reason:

- Meets the `K <= 1.80 W/(m2*K)` cold low-rise target and remains close to the stricter `K <= 1.50 W/(m2*K)` bin if later window-wall ratios are increased.
- Controls east/west summer solar gain if the model is treated as Cold B.
- More realistic as Chinese Low-E insulated glazing than the original generic `Glass` material.

### 4.5 Exterior Door

Proposed construction name:

- `CN_Insulated_Exterior_Door`

Recommended parameter:

- Keep or set equivalent U-value near `1.70 W/(m2*K)`.

Reason:

- Consistent with an insulated residential entry door.
- Avoids changing the load balance unnecessarily.

### 4.6 Attic Gable Walls

If attic remains unconditioned:

- Keep gable walls as secondary envelope, or lightly localize material names.

If attic becomes conditioned:

- Replace `Gable_end` with the same or similar construction as `CN_ExteriorWall_AAC_RockWool_Cold`.

Reason:

- Current attic gable construction is weak compared with conditioned-envelope targets.
- Conditioning the attic changes it from buffer zone to occupied/conditioned zone, so roof and gable walls become primary envelope.

## 5. IDF Implementation Notes for Later

The next IDF modification should be controlled and reversible:

1. Add new China-oriented `Material` and `Construction` objects instead of overwriting all original materials at once.
2. Reassign surface constructions:
   - Exterior living walls: `Exterior Wall` -> `CN_ExteriorWall_AAC_RockWool_Cold`
   - Living-to-attic ceiling: `Interior Ceiling` -> `CN_AtticFloor_RockWool_Cold`
   - Windows: `Exterior Window` -> `CN_LowE_Insulated_Window_Cold`
   - Door: `Exterior Door` -> `CN_Insulated_Exterior_Door`
3. Keep attic unconditioned during the first retrofit simulation, because this isolates envelope changes from HVAC topology changes.
4. If later adding attic temperature control, first decide whether the attic is an occupied second-floor zone or only an attic buffer. These are different modeling tasks.
5. After construction edits, run EnergyPlus once and inspect:
   - `eplusout.err`
   - zone temperatures
   - heating/cooling rates
   - annual or seasonal electricity meters
   - surface heat transfer variables if needed

## 6. Recommendation

For the first Chinese-standard retrofit, do not condition the attic and do not split the HVAC yet. Update only envelope constructions and glazing parameters. This gives a clean before/after comparison:

- Original model: US ExampleFiles lightweight house.
- Retrofit model: China-oriented cold-region low-rise residential envelope.

After that baseline is stable, we can create a second branch that splits `living_unit1` into multiple household zones and adds independent zone-level temperature control.
