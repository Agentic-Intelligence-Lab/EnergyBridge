# 家庭模型围护结构材料整改报告

## 1. 报告目的

本报告用于说明 EnergyPlus 原始家庭建筑模型的围护结构材料现状，并提出一套面向中国住宅建筑节能标准的围护结构材料整改方案。

本轮工作只形成说明与方案，暂不直接修改 IDF。后续确认城市、气候区和改造路线后，再把本报告中的构造写入 `Material`、`Construction` 和表面构造引用。

目标模型：

- 模型文件：`SingleFamilyHouse_HP_Slab.idf`
- 本地路径：`/jupyterfile/Building_Model/Family_Model/SingleFamilyHouse_HP_Slab.idf`
- 来源：EnergyPlus 24.1 ExampleFiles
- 当前热区：`living_unit1`、`attic_unit1`
- 当前空调：一套风管式空气源热泵系统，服务 `living_unit1`

## 2. 标准依据

已下载或归档的标准资料位于：

`/jupyterfile/Building_Model/Standard`

主要依据如下：

1. **GB 55015-2021《建筑节能与可再生能源利用通用规范》**
   - 已下载住建部公开 PDF。
   - 当前建筑节能与可再生能源利用的强制性工程建设通用规范。
   - 住建部公告明确：本规范为强制性工程建设规范，全部条文必须严格执行；现行标准中有关规定与本规范不一致时，以本规范为准。

2. **GB 50176-2016《民用建筑热工设计规范》**
   - 已归档公开目录、室内计算参数、材料热物理参数页面。
   - 用于确定热工设计分区、室内热工计算参数、材料导热系数、表面换热阻、围护结构传热计算方法等。

3. **JGJ 26-2018《严寒和寒冷地区居住建筑节能设计标准》**
   - 已归档围护结构热工设计与气候区属公开页面。
   - 用作严寒、寒冷地区住宅节能设计的细化参考。

4. **JGJ 134-2010《夏热冬冷地区居住建筑节能设计标准》**
   - 已归档公开资料页。
   - 如果后续城市改为上海、武汉、重庆等夏热冬冷地区，可切换到该标准逻辑。

5. **JGJ 75-2012《夏热冬暖地区居住建筑节能设计标准》**
   - 已下载住建部公开 PDF，并保存公告页面。
   - 如果后续城市改为深圳、广州等夏热冬暖地区，可切换到该标准逻辑。

## 3. 气候区假设与控制目标

本轮先按“寒冷地区低层住宅”提出整改方案，原因是：

- 当前模型是两层居住面积加阁楼，属于低层住宅体量。
- 当前工作区已有天津气象文件，天津类城市属于供暖需求明显的寒冷地区工作流。
- 采用寒冷地区方案时，外墙、屋面、外窗传热系数要求较严格，作为第一版底模更稳妥。

本报告暂按 `<=3 层住宅` 处理。采用的主要目标值如下：

| 构件 | 建议控制目标 |
|---|---:|
| 屋面或顶层热边界 | `K <= 0.25 W/(m2*K)` |
| 外墙 | `K <= 0.35 W/(m2*K)` |
| 架空或外挑楼板 | `K <= 0.35 W/(m2*K)` |
| 周边地面保温层热阻 | `R >= 1.60 m2*K/W` |
| 外窗，一般窗墙比 | `K <= 1.80 W/(m2*K)` |
| 外窗，较高窗墙比时的保守目标 | `K <= 1.50 W/(m2*K)` |
| 寒冷 B 区东西向夏季太阳得热 | 建议 `SHGC <= 0.55` |

如果未来目标城市改为深圳或广州，围护结构重点应从“降低传热系数”转为“屋面隔热、外窗遮阳、东西向太阳得热控制、自然通风开口面积控制”。

## 4. 原始建筑围护结构

### 4.1 外墙

原始构造名：`Exterior Wall`

原始层次，由外到内：

1. `syn_stucco`
2. `sheathing_consol_layer`
3. `OSB_7/16in`
4. `wall_consol_layer`
5. `Drywall_1/2in`

原始热工性能估算：

- 材料层热阻约 `R = 3.53 m2*K/W`
- 不含表面换热阻时，约 `K = 0.28 W/(m2*K)`

判断：

- 从数值上看，原始外墙已经能满足寒冷地区低层住宅 `K <= 0.35 W/(m2*K)` 的目标。
- 但它是美国轻型木结构住宅构造，包含 OSB、综合墙体保温层、石膏板等，不符合中国住宅模型的材料表达习惯。
- 因此建议不是单纯“加强保温”，而是把材料体系改为中国常见住宅墙体体系。

### 4.2 屋面与阁楼热边界

原始阁楼外屋面构造名：`Exterior Roof`

原始层次：

1. `Asphalt_shingle`
2. `OSB_1/2in`

原始热工性能估算：

- 材料层热阻约 `R = 0.19 m2*K/W`
- 不含表面换热阻时，约 `K = 5.36 W/(m2*K)`

原始居住区到阁楼的顶棚构造名：`Interior Ceiling`

原始层次：

1. `ceil_consol_layer`
2. `Drywall_1/2in`

原始热工性能估算：

- 材料层热阻约 `R = 7.28 m2*K/W`
- 不含表面换热阻时，约 `K = 0.14 W/(m2*K)`

判断：

- 如果阁楼保持非空调热区，那么真正保护居住空间的是 `living_unit1` 与 `attic_unit1` 之间的顶棚，原始顶棚保温很强。
- 如果未来把阁楼改成可控温热区，那么外屋面和山墙就会变成主要外围护结构，必须重做保温。

### 4.3 地面与地基换热

原始表面构造名：`Interior Floor`

原始层次：

1. `Plywood_3/4in`
2. `Carpet_n_pad`

模型中地面与土壤换热由 `GroundHeatTransfer:Slab:*` 对象处理。当前模型已有垂直边缘保温：

- `RVINS ~= 1.761 m2*K/W`

判断：

- 该值略高于寒冷地区周边地面保温层 `R >= 1.60 m2*K/W` 的目标。
- 后续主要需要把木质地面表达改为中国常见混凝土楼板和 XPS 周边保温表达。

### 4.4 外窗

原始构造名：`Exterior Window`

原始材料：`Glass`，SimpleGlazingSystem

参数：

- `U = 1.70358 W/(m2*K)`
- `SHGC = 0.3344`
- `VT = 0.88`

判断：

- 当前外窗传热系数已经满足 `K <= 1.80 W/(m2*K)`。
- 当前 SHGC 也偏低，有利于夏季太阳得热控制。
- 后续建议把它改名和重新参数化为国产住宅 Low-E 中空玻璃，而不是保留泛化的 `Glass`。

### 4.5 外门

原始构造名：`Exterior Door`

原始材料：`door_const`

估算：

- 材料层热阻约 `R = 0.587 m2*K/W`
- 不含表面换热阻时，约 `K = 1.70 W/(m2*K)`

判断：

- 作为保温入户门，该数值可以接受。

## 5. 预计修改的围护结构材料方案

### 5.1 外墙整改方案

建议新构造名：`CN_ExteriorWall_AAC_RockWool_Cold`

建议层次，由外到内：

1. 20 mm 外侧水泥砂浆或保护层
2. 70 mm 岩棉板或石墨 EPS 外保温层
3. 200 mm 蒸压加气混凝土砌块或等效轻质砌体填充墙
4. 20 mm 内侧抹灰或石膏找平层

建议参数：

| 材料 | 厚度 | 导热系数建议值 |
|---|---:|---:|
| 外侧水泥砂浆 | 0.020 m | `0.93 W/(m*K)` |
| 岩棉或石墨 EPS | 0.070 m | `0.040-0.045 W/(m*K)` |
| 加气混凝土砌块 | 0.200 m | `0.16 W/(m*K)` |
| 内侧抹灰 | 0.020 m | `0.70-0.93 W/(m*K)` |

估算结果：

- 含常规内外表面热阻后，预计 `K ~= 0.32-0.35 W/(m2*K)`。

选择理由：

- 满足寒冷地区低层住宅外墙 `K <= 0.35 W/(m2*K)` 的目标。
- 采用中国住宅更常见的砌体填充墙加外保温体系。
- 外保温有利于降低热桥影响，比单纯内保温更适合作为标准住宅底模。

### 5.2 顶层热边界整改方案

第一阶段建议保持阁楼非空调，把保温重点放在居住区与阁楼之间的顶棚。

建议新构造名：`CN_AtticFloor_RockWool_Cold`

建议层次，由居住侧到阁楼侧：

1. 12 mm 石膏板或抹灰层
2. 180 mm 岩棉或玻璃棉保温层
3. 100-120 mm 钢筋混凝土楼板或等效结构层

建议参数：

| 材料 | 厚度 | 导热系数建议值 |
|---|---:|---:|
| 石膏板或抹灰层 | 0.012-0.020 m | `0.16-0.93 W/(m*K)` |
| 岩棉或玻璃棉 | 0.180 m | `0.040-0.045 W/(m*K)` |
| 钢筋混凝土楼板 | 0.100-0.120 m | `1.74 W/(m*K)` |

估算结果：

- 含常规表面热阻后，预计 `K ~= 0.22-0.25 W/(m2*K)`。

选择理由：

- 满足寒冷地区低层住宅屋面或顶层热边界 `K <= 0.25 W/(m2*K)` 的目标。
- 保留阁楼作为非空调缓冲层，和当前模型拓扑最一致。
- 避免同时改动围护结构和 HVAC 热区控制，便于先做清晰的 before/after 仿真。

如果后续决定把阁楼改为空调热区，则应额外新增 `CN_Roof_XPS_Cold`：

1. 屋面瓦或保护层
2. 防水层
3. 120 mm XPS 保温层
4. 100-120 mm 钢筋混凝土屋面板
5. 内侧抹灰层

预计 `K ~= 0.23-0.25 W/(m2*K)`。

### 5.3 地面与周边地面整改方案

建议新构造或设置名：`CN_GroundSlab_PerimeterXPS_Cold`

建议表达：

1. 100 mm 混凝土板
2. 室内地面面层
3. 周边竖向 XPS 保温，50 mm
4. 在 EnergyPlus `GroundHeatTransfer:Slab:Insulation` 中保持或设置 `RVINS >= 1.60 m2*K/W`

建议参数：

| 材料 | 厚度 | 导热系数建议值 | 热阻 |
|---|---:|---:|---:|
| XPS 周边保温 | 0.050 m | `0.030 W/(m*K)` | `R ~= 1.67 m2*K/W` |

选择理由：

- 满足寒冷地区周边地面保温层热阻 `R >= 1.60 m2*K/W`。
- 当前模型的 `RVINS ~= 1.761` 已经接近该方案，后续主要是材料命名和本土化说明。

### 5.4 外窗整改方案

建议新构造名：`CN_LowE_Insulated_Window_Cold`

建议 EnergyPlus SimpleGlazingSystem 参数：

| 参数 | 建议值 |
|---|---:|
| U-Factor | `1.60 W/(m2*K)` |
| SHGC | `0.40-0.45` |
| Visible Transmittance | `0.60` |

选择理由：

- 满足寒冷地区低层住宅外窗 `K <= 1.80 W/(m2*K)`。
- 如果未来因窗墙比提高而采用更严格目标，`U = 1.60` 也比原模型更接近 `K <= 1.50 W/(m2*K)` 的保守要求。
- `SHGC <= 0.55` 可覆盖寒冷 B 区东西向夏季太阳得热控制的保守需求。
- 比原始 `Glass` 更像国产住宅 Low-E 中空玻璃。

### 5.5 外门整改方案

建议新构造名：`CN_Insulated_Exterior_Door`

建议参数：

- 等效 `K ~= 1.70 W/(m2*K)`，或保留原始等效热工性能。

选择理由：

- 作为住宅保温入户门，该参数不会成为主要能耗瓶颈。
- 不建议第一轮大幅改动外门，以免引入不必要变量。

### 5.6 阁楼山墙

如果阁楼保持非空调：

- 山墙可仅做材料名称本土化，不作为第一轮主要改造对象。

如果阁楼改为空调热区：

- 山墙应改为与外墙相同或相近的 `CN_ExteriorWall_AAC_RockWool_Cold`。

选择理由：

- 当前 `attic_unit1` 是缓冲热区时，山墙主要影响阁楼温度，间接影响居住区。
- 一旦阁楼成为空调热区，山墙和屋面都变成主要外围护结构，必须满足外墙/屋面传热系数目标。

## 6. 后续 IDF 修改建议

建议后续分阶段修改：

1. 第一阶段：只改围护结构，不改热区和 HVAC。
   - 新增中国材料 `Material`。
   - 新增中国构造 `Construction`。
   - 替换外墙、顶棚、外窗、外门、地面相关构造引用。
   - 保持阁楼非空调。

2. 第二阶段：做 EnergyPlus 仿真验证。
   - 检查 `eplusout.err`。
   - 对比改造前后居住区温度、加热量、冷却量、电耗。
   - 输出表面传热、外窗得热、`Zone Air Temperature` 等变量。

3. 第三阶段：再考虑 5 热区和独立温控。
   - 将 `living_unit1` 拆分为客厅、卧室、厨房、卫生间、其他房间。
   - 新增每个热区的温控器。
   - 决定采用独立分体机、风管机、多联机，还是先用 IdealLoads 做控制实验。

## 7. 总体建议

当前模型的部分原始热工性能并不差，尤其是外墙和生活区到阁楼的顶棚。但是它的材料体系明显是美国住宅样式，不适合作为“中国住宅模型”的正式说明。

因此建议第一版整改目标是：

- 保持原模型拓扑稳定；
- 不急于把阁楼做成空调热区；
- 先把围护结构材料体系改成中国住宅常见构造；
- 让改造后的传热系数满足或略优于寒冷地区低层住宅目标；
- 稳定后再做 5 热区、EMS、actuator 和 Python API 控制。

## 8. Family_Simple.idf 实际修改记录

本节记录 2026-05-08 已经落地到 IDF 的修改。新模型文件为：

`/jupyterfile/Building_Model/Family_Model/Family_Simple.idf`

原始模型 `SingleFamilyHouse_HP_Slab.idf` 未覆盖，作为对照基线保留。

### 8.1 新增国产材料对象

新增位置：`Family_Simple.idf` 的 `Material` 和 `WindowMaterial:SimpleGlazingSystem` 段。

| 新对象 | 位置 | 主要参数 | 依据 |
|---|---:|---|---|
| `CN_CementMortar_20mm` | `Family_Simple.idf:1905` | 厚度 `0.020 m`，导热系数 `0.93 W/(m*K)` | GB 50176-2016 常用材料热物理参数 |
| `CN_RockWool_70mm` | `Family_Simple.idf:1916` | 厚度 `0.070 m`，导热系数 `0.041 W/(m*K)` | GB 50176-2016；GB 55015-2021 外墙限值目标 |
| `CN_AAC_Block_200mm` | `Family_Simple.idf:1927` | 厚度 `0.200 m`，导热系数 `0.16 W/(m*K)` | GB 50176-2016 加气混凝土类材料取值 |
| `CN_InteriorPlaster_20mm` | `Family_Simple.idf:1938` | 厚度 `0.020 m`，导热系数 `0.70 W/(m*K)` | GB 50176-2016 |
| `CN_GypsumBoard_12mm` | `Family_Simple.idf:1949` | 厚度 `0.012 m`，导热系数 `0.16 W/(m*K)` | GB 50176-2016 |
| `CN_MineralWool_180mm` | `Family_Simple.idf:1960` | 厚度 `0.180 m`，导热系数 `0.041 W/(m*K)` | GB 50176-2016；GB 55015-2021 屋面/顶层热边界限值 |
| `CN_ReinforcedConcrete_120mm` | `Family_Simple.idf:1971` | 厚度 `0.120 m`，导热系数 `1.74 W/(m*K)` | GB 50176-2016 钢筋混凝土类材料取值 |
| `CN_ReinforcedConcrete_100mm` | `Family_Simple.idf:1982` | 厚度 `0.100 m`，导热系数 `1.74 W/(m*K)` | GB 50176-2016 |
| `CN_CeramicTile_10mm` | `Family_Simple.idf:1993` | 厚度 `0.010 m`，导热系数 `1.05 W/(m*K)` | GB 50176-2016 陶瓷/地砖类材料取值 |
| `CN_InsulatedDoor_45mm` | `Family_Simple.idf:2004` | 厚度 `0.045 m`，导热系数 `0.0765 W/(m*K)` | 保持原门等效传热水平 |
| `CN_LowE_Insulated_Glass_Cold` | `Family_Simple.idf:2051` | `U=1.60 W/(m2*K)`，`SHGC=0.42`，`VT=0.60` | GB 55015-2021 外窗传热系数与太阳得热控制目标 |

### 8.2 新增国产构造对象

新增位置：`Family_Simple.idf:2241` 起。

| 新构造 | 位置 | 层次 | 预计热工结果 |
|---|---:|---|---|
| `CN_ExteriorWall_AAC_RockWool_Cold` | `Family_Simple.idf:2241` | 水泥砂浆 20 mm + 岩棉 70 mm + 加气混凝土 200 mm + 内抹灰 20 mm | 材料层 `R=3.0074 m2*K/W`，含常规表面热阻后约 `K=0.3167 W/(m2*K)` |
| `CN_AtticFloor_RockWool_Cold` | `Family_Simple.idf:2248` | 钢筋混凝土 120 mm + 矿棉 180 mm + 石膏板 12 mm | 材料层 `R=4.5342 m2*K/W`，含常规表面热阻后约 `K=0.2108 W/(m2*K)` |
| `CN_GroundSlab_Concrete_Finish` | `Family_Simple.idf:2254` | 钢筋混凝土 100 mm + 地砖 10 mm | 地面热边界仍由 `GroundHeatTransfer:Slab:*` 处理 |
| `CN_LowE_Insulated_Window_Cold` | `Family_Simple.idf:2259` | Low-E 中空玻璃简化系统 | `U=1.60 W/(m2*K)`，`SHGC=0.42` |
| `CN_LowE_Insulated_Window_Blinds_Cold` | `Family_Simple.idf:2263` | Low-E 中空玻璃 + 原内置百叶 `int_blind` | 用于原有遮阳控制 |
| `CN_Insulated_Exterior_Door` | `Family_Simple.idf:2268` | 45 mm 等效保温门 | 材料层 `R=0.5882 m2*K/W`，约 `K=1.70 W/(m2*K)` |

### 8.3 表面构造引用替换

本轮只替换主要居住区外围护结构和顶层热边界，不改热区数量、不改 HVAC、不加 EMS。

| 构件 | 修改位置 | 修改前 | 修改后 | 标准/理由 |
|---|---:|---|---|---|
| 居住区到阁楼的顶棚 `ceiling_unit1` | `Family_Simple.idf:2326` | `Interior Ceiling` | `CN_AtticFloor_RockWool_Cold` | 满足寒冷地区低层住宅顶层热边界 `K <= 0.25 W/(m2*K)` |
| 居住区外墙，8 个外墙面 | `Family_Simple.idf:2428`、`2448`、`2469`、`2488`、`2509`、`2530`、`2552`、`2572` | `Exterior Wall` | `CN_ExteriorWall_AAC_RockWool_Cold` | 满足寒冷地区低层住宅外墙 `K <= 0.35 W/(m2*K)`；材料体系国产化 |
| 首层地面 `Floor_unit1` | `Family_Simple.idf:2592` | `Interior Floor` | `CN_GroundSlab_Concrete_Finish` | 将原木地板/地毯表达改为混凝土楼板 + 面层；地基换热仍由 Slab 对象处理 |
| 8 个外窗 | `Family_Simple.idf:2610`、`2621`、`2632`、`2643`、`2654`、`2665`、`2676`、`2687` | `Exterior Window` | `CN_LowE_Insulated_Window_Cold` | 外窗 `U=1.60 W/(m2*K)`，满足寒冷地区住宅外窗控制目标 |
| 外门 `Door_ldb_unit1` | `Family_Simple.idf:2700` | `Exterior Door` | `CN_Insulated_Exterior_Door` | 保留约 `K=1.70 W/(m2*K)` 的保温门水平 |
| 原窗遮阳控制构造 | `Family_Simple.idf:2715` | `window_w_blinds` | `CN_LowE_Insulated_Window_Blinds_Cold` | 避免遮阳启用时回退到原 `Glass` 构造 |

### 8.4 地面 Slab 参数修改

修改位置：`GroundHeatTransfer:Slab:MatlProps` 与 `GroundHeatTransfer:Slab:Insulation`。

| 参数 | 位置 | 修改前 | 修改后 | 说明 |
|---|---:|---:|---:|---|
| Slab 材料密度 | `Family_Simple.idf:2788` | `2300 kg/m3` | `2500 kg/m3` | 改为钢筋混凝土类取值 |
| Slab 比热 | `Family_Simple.idf:2790` | `650 J/(kg*K)` | `920 J/(kg*K)` | 改为钢筋混凝土类取值 |
| Slab 导热系数 | `Family_Simple.idf:2792` | `0.9 W/(m*K)` | `1.74 W/(m*K)` | 改为钢筋混凝土类取值 |
| 周边竖向保温热阻 `RVINS` | `Family_Simple.idf:2829` | `1.76099742894375 m2*K/W` | `1.6667 m2*K/W` | 对应 50 mm XPS、导热系数约 `0.030 W/(m*K)`，满足 `R >= 1.60 m2*K/W` |

### 8.5 本轮未修改项

以下对象本轮保留，原因是它们不属于第一阶段的主要居住区热边界，或修改会引入 HVAC/热区拓扑变化：

- `Roof_front_unit1`、`Roof_back_unit1` 仍使用原 `Exterior Roof`。
- `Roof_right_unit1`、`Roof_left_unit1` 仍使用原 `Gable_end`。
- `Inter zone floor 1` 仍使用原 `Interior Floor`，因为它是 `living_unit1` 内部的绝热分隔面，不是对室外或土壤的主要热边界。
- `attic_unit1` 仍为非空调缓冲热区，没有新增温控器或空调设备。

如果下一步决定把阁楼改为空调热区，则必须继续把 `Exterior Roof` 和 `Gable_end` 改为满足屋面/外墙限值的国产构造。

### 8.6 校验结果

已执行解析级校验：

```bash
/usr/local/bin/energyplus -x -c --convert-only -d /tmp/family_simple_convert_check_x /jupyterfile/Building_Model/Family_Model/Family_Simple.idf
```

结果：

- EnergyPlus Completed Successfully
- `0 Warning`
- `0 Severe Errors`

完整仿真校验说明：

- 直接运行完整仿真时，`Family_Simple.idf` 与原始 `SingleFamilyHouse_HP_Slab.idf` 都会在 `Floor_unit1` 处报同一个错误：缺少 `surfPropOthSdCoefSlabAverage` 对应的 `SurfaceProperty:OtherSideCoefficients`。
- 该问题来自原始 ExampleFile 的 `GroundHeatTransfer:Slab:*` / Slab 预处理工作流，不是本次新增国产材料和构造造成的错误。
- 后续若要用 `pyenergyplus` 直接跑该模型，需要单独处理 Slab 预处理输出，或把地面边界改为可直接运行的 `OtherSideCoefficients` / 地温 schedule 方案。

## 9. 天津天气文件导入与地面边界修复记录

本节是第 8.4 与第 8.6 节之后的后续修复记录。以本节为准：`Family_Simple.idf` 已不再依赖 `GroundHeatTransfer:Slab:*` 预处理器，而是改为可由 EnergyPlus 主程序直接运行的天津天气 + 月地温方案。

### 9.1 天气文件来源

使用的天气文件已整理在 `Building_Model/Weather/Tianjin/`：

- EPW：`CHN_Tianjin.Tianjin.545270_CSWD.epw`
- DDY：`CHN_Tianjin.Tianjin.545270_CSWD.ddy`

天津 EPW 头部信息为：

- 位置：天津，中国，WMO `545270`
- 纬度：`39.08 deg`
- 经度：`117.07 deg`
- 时区：`UTC+8`
- 海拔：`2.5 m`

### 9.2 IDF 中导入的天津地点与设计日

| 对象 | 修改位置 | 修改前 | 修改后 | 数据来源 |
|---|---:|---|---|---|
| `Site:Location` | `Family_Simple.idf:42` | `Fairbanks Intl Arpt_AK_USA Design_Conditions` | `Tianjin CHN CSWD 545270` | 天津 EPW/DDY |
| 供暖设计日 | `Family_Simple.idf:51` | Fairbanks `-41.9 C` 供暖设计日 | 天津 `99.6%` 供暖设计日，干球 `-10.2 C` | 天津 DDY |
| 制冷设计日 | `Family_Simple.idf:79` | Fairbanks `27.4 C` 制冷设计日 | 天津 `0.4%` 制冷设计日，干球 `34.2 C`，湿球 `23.5 C` | 天津 DDY |
| `SimulationControl` | `Family_Simple.idf:15` | `Run Simulation for Sizing Periods = Yes` | `No` | 后续 EMS/RL 只需要天气文件逐时运行；设计日保留用于 sizing，但不作为正式输出环境运行 |

### 9.3 地面边界修复

原模型地面问题来自 `Floor_unit1` 使用：

- `Outside Boundary Condition = GroundSlabPreprocessorAverage`
- `Outside Boundary Condition Object = surfPropOthSdCoefSlabAverage`

直接用 EnergyPlus 主程序运行时，该对象依赖 Slab 预处理器生成，当前文件中不存在对应的 `SurfaceProperty:OtherSideCoefficients`，因此会产生 severe/fatal。

本次修复如下：

| 对象 | 修改位置 | 修改前 | 修改后 | 说明 |
|---|---:|---|---|---|
| `Site:GroundTemperature:BuildingSurface` | `Family_Simple.idf:109` | 无 | 天津 EPW 中 `2 m` 月地温：`9.47, 5.34, 3.37, 3.30, 6.44, 11.00, 15.90, 20.10, 22.27, 21.87, 18.97, 14.52 C` | 用作首层地面热边界的稳定月地温 |
| `Floor_unit1` 外边界 | `Family_Simple.idf:2611` | `GroundSlabPreprocessorAverage` | `Ground` | 取消 Slab 预处理器依赖 |
| `Floor_unit1` 外边界对象 | `Family_Simple.idf:2612` | `surfPropOthSdCoefSlabAverage` | 空 | `Ground` 边界不需要外边界对象 |
| `GroundHeatTransfer:*` 对象 | 原 `Family_Simple.idf:2767` 起 | 保留 Slab/Basement 预处理对象 | 已删除 | 避免 EnergyPlus 主程序要求额外运行 `ExpandObjects`/Slab 预处理流程 |

### 9.4 排风节点修复

地面问题解决后，天津年运行暴露出原模型中的一个节点连接 severe：`ZONE EXHAUST NODE_UNIT1` 同时被 `Fan:ZoneExhaust` 与 `HeatExchanger:AirToAir:SensibleAndLatent` 作为非父级入口节点使用。

修复方式：

| 对象 | 修改位置 | 修改前 | 修改后 | 说明 |
|---|---:|---|---|---|
| `Fan:ZoneExhaust` | `Family_Simple.idf:3759` | `Zone Exhaust Node_unit1` | `Zone Exhaust Fan Node_unit1` | 给排风机单独分配 zone exhaust node |
| `NodeList, Zone Exhaust Node_list_unit1` | `Family_Simple.idf:4105` | `Zone Exhaust Node_unit1, Zone Exhaust Node1_unit1` | `Zone Exhaust Fan Node_unit1, Zone Exhaust Node_unit1, Zone Exhaust Node1_unit1` | 将排风机节点放在第一位，以满足 AirflowNetwork 对 `ZoneExhaustFan` 的校验 |

### 9.5 最终仿真验证

执行命令：

```bash
/usr/local/bin/energyplus -w /jupyterfile/Building_Model/Weather/Tianjin/CHN_Tianjin.Tianjin.545270_CSWD.epw -d /jupyterfile/Building_Model/Family_Model/run_tianjin_ground_check /jupyterfile/Building_Model/Family_Model/Family_Simple.idf
```

最终结果：

- EnergyPlus Completed Successfully
- `0 Severe Errors`
- `0 Fatal Errors`
- 输出目录：`Building_Model/Family_Model/run_tianjin_ground_check/`

仍存在的主要 warning：

- `Site:GroundTemperature:BuildingSurface` 部分月地温低于 `15 C`。这是天津 2 m 月地温数据导致的提示，属于寒冷地区天气数据合理结果。
- `AirflowNetwork` 提示 `ZoneVentilation:*` 不参与模拟，因为当前 AFN 控制模式为 `MultizoneWithDistribution`。
- 若干 HVAC 标准额定工况、热泵热水器、泵功率为 0、夏季少量 HVAC 最大迭代次数 warning。它们不是本次地面边界导致的问题，可在后续 HVAC/AFN 整理阶段单独处理。

## 10. EV 与储水式电热水器外部控制模型接入记录

本节记录在围护结构和天津天气修复之后，为支持 `pyenergyplus` 外部联合仿真而加入的两个可控设备模型：电动汽车充电负荷（EV）和储水式电热水器（EWH）。本次改造目标不是把复杂控制逻辑写死在 IDF 中，而是在 IDF 中保留物理接口和 actuator，然后由外部 Python 程序在每个 timestep 计算控制量并覆盖对应 schedule。

### 10.1 IDF 侧改造

| 类型 | 对象/位置 | 修改前 | 修改后 | 说明 |
|---|---|---|---|---|
| 旧家电负荷 | `ElectricEquipment` | `dishwasher1`、`refrigerator1`、`clotheswasher1`、`electric_dryer1`、`electric_range1`、`television1`、`electric_mels1`、`IECC_Adj1` | 已删除 | 清除原美国住宅模板中的家电内热源，避免与后续 EV/EWH 控制实验混淆 |
| EV 充电器 | `ElectricEquipment, EV_Charger` | 无 | `Design Level = 7000 W`，`Schedule = EV_Charging_Fraction_Control`，`Fraction Lost = 1` | EV 计入建筑电表，但充电热量不进入室内热区 |
| EV 控制日程 | `Schedule:Constant, EV_Charging_Fraction_Control` | 无 | 初值 `0`，范围按 `Fraction` | 外部 Python 写入 `0-1` 充电比例 |
| 电热水器水箱 | `WaterHeater:Stratified, Water Heater_Tank_unit1` | `0.196841372 m3`，`5.5 kW`，`dhw_setpt_hpwh`，皮肤损失系数 `4.536492 W/m2-K` | `0.12 m3`，`3.0 kW`，`EWH_Setpoint_Control`，皮肤损失系数 `1.2 W/m2-K` | 改为更接近家庭储水式电热水器的参数；具体保温损失后续可继续按 GB 21519 校核 |
| 电热水器设定点 | `Schedule:Constant, EWH_Setpoint_Control` | 无 | 初值 `50 C` | 外部 Python 写入电热水器设定温度 |
| 电热水器启停 | `Schedule:Constant, EWH_Availability_Control` | 无 | 初值 `1` | 外部 Python 写入 DHW 设备可用性 |
| DHW 支路 | `WaterUse:Equipment` 与 `WaterUse:Connections` | 洗衣机和洗碗机挂在 DHW 回路上 | 已删除洗衣机/洗碗机热水支路 | 避免将洗衣机/洗碗机用水与本轮 EWH 控制混在一起 |
| EMS/Actuator 接口 | `Output:EnergyManagementSystem` + `pyenergyplus` actuator handle | 无 | 输出 EMS actuator 字典，并由外部 Python 直接获取 actuator | 不显式声明 `EnergyManagementSystem:Actuator` 对象，避免与外部 API 重复声明同一 actuator |

### 10.2 外部控制代码

外部控制程序已放在：

```text
Building_Model/Family_Model/control_model/control_model.py
```

运行方式示例：

```bash
python /jupyterfile/Building_Model/Family_Model/control_model/control_model.py
```

该程序通过 `pyenergyplus.api.EnergyPlusAPI` 启动 EnergyPlus，并在 `callback_begin_system_timestep_before_predictor` 中写入 actuator：

| Python 写入对象 | EnergyPlus actuator | 含义 |
|---|---|---|
| `u_ev,k` | `Schedule:Constant / Schedule Value / EV_Charging_Fraction_Control` | EV 充电比例，`0-1` |
| `P_ev,grid,k` | `ElectricEquipment / Electricity Rate / EV_Charger` | EV 实际充电功率，单位 `W` |
| `T_sp,k` | `Schedule:Constant / Schedule Value / EWH_Setpoint_Control` | 电热水器设定温度，单位 `C` |
| `a_ewh,k` | `Schedule:Constant / Schedule Value / EWH_Availability_Control` | 电热水器可用性，`0/1` |
| `a_ewh,k` | `Plant Component WaterHeater:Stratified / On/Off Supervisory / Water Heater_Tank_unit1` | 电热水器本体启停监督控制，`0/1` |

EnergyPlus 仍连续运行完整 runperiod；Python 不是每步重启 EnergyPlus，而是在 EnergyPlus 的 timestep 回调点同步读状态、算动作、写 actuator。该方式等价于将固定时间表替换为外部数学模型输出。

### 10.3 EV 数学模型

EV 在 EnergyPlus 中不是室内热源，而是家庭总电表上的外部可控用电负荷。IDF 中只用 `EV_Charger` 表示电网侧充电功率入口，电池 SOC、到家/离家、行驶耗电由 Python 外部模型维护。

参数定义：

| 符号 | 当前值 | 含义 |
|---|---:|---|
| `C_ev` | `60 kWh` | 电池容量 |
| `P_ch,max` | `7 kW` | 家庭交流慢充最大功率，对应 IDF `EV_Charger` 的 `7000 W` |
| `eta_ch` | `0.92` | 充电效率 |
| `SOC_0` | `0.50` | 初始 SOC |
| `SOC_tar` | `0.80` | 目标 SOC |
| `SOC_min` | `0.15` | 最低 SOC |
| `t_arr` | `18:00` | 默认到家时间 |
| `t_dep` | `07:30` | 默认离家时间 |
| `E_drive` | `8 kWh/day` | 默认每日行驶耗电 |

EV 可用性：

```text
A_ev,k = 1,  当 t_k >= t_arr 或 t_k < t_dep
A_ev,k = 0,  其他时间
```

充电功率：

```text
u_ev,k in [0, 1]
P_ev,grid,k = A_ev,k * u_ev,k * P_ch,max
P_ev,batt,k = eta_ch * P_ev,grid,k
```

SOC 离散更新：

```text
SOC_{k+1} = clip(
    SOC_k
    + eta_ch * P_ch,max * u_ev,k * Delta_t / C_ev
    - E_drive,k / C_ev,
    SOC_min,
    1
)
```

其中 `Delta_t` 单位为小时，`E_drive,k` 只在每天离家时扣减一次。当前控制策略为：车辆在家且 `SOC < SOC_tar` 时充电，否则不充电。若剩余 timestep 只需部分功率即可达到目标 SOC，则自动将 `u_ev,k` 限制在小于 1 的值。

IDF 与数学模型的映射：

```text
EV_Charging_Fraction_Control = u_ev,k
EV_Charger Electricity Rate actuator = P_ev,grid,k * 1000
EV_Charger Design Level = P_ch,max * 1000 = 7000 W
EV_Charger Fraction Lost = 1
```

`Fraction Lost = 1` 表示 EV 充电热量不进入 `living_unit1`，只计入电耗；这符合车辆在室外/车位充电、热量不参与室内热平衡的建模假设。

### 10.4 储水式电热水器数学模型

电热水器物理对象仍由 EnergyPlus 的 `WaterHeater:Stratified` 计算。外部 Python 模型不重复求解完整水箱传热，而是根据水箱状态、时段和 EV 充电状态给出设定温度和可用性。其目的相当于用外部控制模型替换原 IDF 固定热水器设定点时间表。

当前 IDF 参数：

| 参数 | 当前值 | 说明 |
|---|---:|---|
| 水箱容积 `V` | `0.12 m3` | 120 L |
| 水箱高度 | `1.05 m` | 竖直圆柱 |
| 加热功率 `P_h,max` | `3.0 kW` | 储水式电热水器常见功率等级 |
| 热效率 `eta_h` | `1.0` | 电阻加热近似 |
| 最大温度限制 | `65 C` | 防止控制器设定过高 |
| 死区 `Delta T_db` | `4 C` | 低于设定点 4 C 左右时恢复加热 |
| 皮肤损失系数 | `1.2 W/m2-K` | 初始保温损失假设，后续可按 GB 21519 进一步校准 |

等效单节点水箱能量平衡可写为：

```text
m = rho_w * V
C_tank = m * c_p,w

C_tank * dT_tank/dt =
    eta_h * P_h,k
    - UA * (T_tank,k - T_amb,k)
    - rho_w * c_p,w * V_draw,k * (T_tank,k - T_in,k)
```

离散形式：

```text
T_tank,k+1 = T_tank,k
    + Delta_t / C_tank * [
        eta_h * P_h,k
        - UA * (T_tank,k - T_amb,k)
        - rho_w * c_p,w * V_draw,k * (T_tank,k - T_in,k)
      ]
```

EnergyPlus 内部用分层水箱模型求解上述过程的更细版本；Python 控制器读取 `Water Heater Tank Temperature`，并写入：

```text
EWH_Setpoint_Control = T_sp,k
EWH_Availability_Control = a_ewh,k
```

当前 Python 控制规则：

```text
若 T_tank,k < 43 C:
    a_ewh,k = 1
    T_sp,k = 55 C
若处于早高峰 06:00-08:30 或晚高峰 18:00-23:00:
    a_ewh,k = 1
    T_sp,k = 55 C
若 EV 正在充电:
    a_ewh,k = 1
    T_sp,k = 47 C
其他时段:
    a_ewh,k = 1
    T_sp,k = 50 C
```

也就是说，EWH 当前不做强制断电，只做设定温度回退；这样更平稳，避免热水舒适性突然失效。后续如果要做需求响应，可以把 `a_ewh,k` 作为 action，允许外部策略在水箱温度足够高时短时关闭电热水器。

### 10.5 输出与日志

IDF 中新增了以下 timestep 输出：

- `EV_Charger, Electric Equipment Electricity Rate`
- `EV_Charger, Electric Equipment Electricity Energy`
- `Water Heater_Tank_unit1, Water Heater Electricity Rate`
- `Water Heater_Tank_unit1, Water Heater Electricity Energy`
- `Water Heater_Tank_unit1, Water Heater Tank Temperature`
- `Zone Mean Air Temperature`
- `Electricity:Facility`
- `InteriorEquipment:Electricity`
- `WaterSystems:Electricity`

外部 Python 额外输出：

```text
Building_Model/Family_Model/run_control_model/control_model_log.csv
```

该 CSV 记录每步的 `SOC`、EV 充电比例、电热水器设定点、电热水器可用性、热区温度、水箱温度、EV/EWH 实际功率和总电表功率。后续若做 RL/Gym 风格环境，可以直接将这些列作为 observation、action、reward 的基础。
