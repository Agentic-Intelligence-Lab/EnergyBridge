# 中型办公建筑模型使用说明

## 文件夹内容

`Model_Use` 是当前中型办公建筑模型的可使用版本，尽量只保留后续仿真、控制和复现实验需要的文件。

- `MediumOffice_Tianjin_IndependentControl.idf`：最终整理好的天津中型办公建筑 EnergyPlus 模型。
- `control_15zone_ems_actuator.py`：15 个热区独立温度设定点控制示例代码。
- `run_15zone_control_example/`：控制示例运行后的轻量结果摘要。

## 模型来源与用途

该模型基于 EnergyPlus 示例/原型建筑 `ASHRAE901_OfficeMedium_STD2019` 修改得到。当前版本已经完成以下整理：

- 替换为天津天气和天津地区地温参数。
- 对围护结构材料进行了国产化参数改造。
- 去除了原模型中会干扰外部接管的旧 EMS/PythonPlugin/ExternalInterface 控制逻辑。
- 为 15 个可控热区分别建立了独立的冷热设定点日程。
- 通过 pyenergyplus actuator 可以在仿真过程中逐 timestep 改写每个热区的设定点。

这个模型的定位不是单纯的固定日程办公楼，而是一个控制研究用底模。后续可以用于多热区独立温控、强化学习控制、类似 sinergym 的逐步交互仿真等任务。

## 天气、运行期与地温

- 天气文件：`Building_Model/Weather/Tianjin/CHN_Tianjin.Tianjin.545270_CSWD.epw`
- 设计日文件：父级流程中使用天津 CSWD 对应 DDY。
- 当前 RunPeriod：6 月 1 日至 9 月 1 日。
- RunPeriod 名称：`summer_hvac_test`
- 时间步：每小时 4 个 timestep。
- 地温对象：`Site:GroundTemperature:FCfactorMethod`
- 月地温参数，单位为 degC：

```text
17.04, 16.84, 16.98, 20.32, 20.99, 24.44,
25.06, 25.26, 25.26, 21.98, 18.15, 17.38
```

这组地温用于缓解原始模型在天津夏季工况下出现的不合理地面换热影响。

## 建筑几何与热区

根据 EnergyPlus HTML 报告：

- 总建筑面积：`4982.19 m2`
- 净空调面积：`4982.19 m2`
- 楼层：3 层办公楼，另有每层吊顶/回风夹层 plenum。
- 可控空调热区：15 个。
- 不作为温控对象的 plenum 热区：`FirstFloor_Plenum`、`MidFloor_Plenum`、`TopFloor_Plenum`。

15 个可控热区如下：

| 分组 | 热区 | 单区面积 |
| --- | --- | ---: |
| 核心区 | `Core_bottom`, `Core_mid`, `Core_top` | `983.54 m2` |
| 底层外区 | `Perimeter_bot_ZN_1`, `Perimeter_bot_ZN_3` | `207.34 m2` |
| 底层外区 | `Perimeter_bot_ZN_2` | `131.26 m2` |
| 底层外区 | `Perimeter_bot_ZN_4` | `131.25 m2` |
| 中层外区 | `Perimeter_mid_ZN_1`, `Perimeter_mid_ZN_3` | `207.34 m2` |
| 中层外区 | `Perimeter_mid_ZN_2` | `131.26 m2` |
| 中层外区 | `Perimeter_mid_ZN_4` | `131.25 m2` |
| 顶层外区 | `Perimeter_top_ZN_1`, `Perimeter_top_ZN_3` | `207.34 m2` |
| 顶层外区 | `Perimeter_top_ZN_2` | `131.26 m2` |
| 顶层外区 | `Perimeter_top_ZN_4` | `131.25 m2` |

典型办公热区高度为 `2.7432 m`，plenum 高度为 `1.2192 m`。

## 围护结构设置

该模型从原 ASHRAE 原型建筑的围护结构出发，参考国内标准和天津寒冷地区气候条件进行了材料参数改造。标准资料存放在：

`Building_Model/Standard/envelope`

主要参考文件包括：

- `GB55015-2021_BuildingEnergyRenewable_MOHURD.pdf`
- `GB55016-2021_BuildingEnvironment_MOHURD.pdf`
- `GB50176-2016_ThermalDesign_GF_CABR_index.html`
- `GB50176-2016_MaterialParams_GF_CABR.html`
- `JGJ26-2018_ColdResidential_Envelope_GF_CABR.html`

需要注意：当前模型是用于仿真研究的国产化围护结构改造版本，不是正式公共建筑节能审查报告。

主要外维护结构如下：

| 部位 | IDF 构造名称 | 材料层次 | 报告中热工结果 |
| --- | --- | --- | --- |
| 外墙 | `nonres_ext_wall` | `CN_Cement_Mortar_20mm` + `CN_XPS_Insulation_60mm` + `CN_AAC_Block_200mm` + `CN_Cement_Mortar_20mm` | 含膜传热系数约 `0.303 W/m2-K` |
| 屋面 | `nonres_roof` | `CN_Waterproof_Membrane_10mm` + `CN_XPS_Insulation_120mm` + `CN_Reinforced_Concrete_150mm` + `CN_Cement_Mortar_20mm` | 含膜传热系数约 `0.232 W/m2-K` |
| 外窗 | `Window_U_0.36_SHGC_0.38` / `Glazing Layer` | 简化玻璃系统 | IDF 设定 U 值 `1.80 W/m2-K`，SHGC `0.40`，可见光透射比 `0.60` |
| 地面 | 各底层区域的 `Construction:FfactorGroundFloor` | F-factor 地面计算方法 | 报告中底层地面含膜 U 值约 `0.217-0.228 W/m2-K` |

窗墙比信息：

- 外墙总面积：`1977.67 m2`
- 外窗洞口面积：`652.62 m2`
- 总窗墙比：`33.00%`

## HVAC 系统设置

模型保留中型办公楼原型的 VAV 系统结构：

- 3 个空气环路：`PACU_VAV_bot`、`PACU_VAV_mid`、`PACU_VAV_top`
- 每个空气环路服务一个楼层。
- 主要设备包括：新风混合箱、双速 DX 制冷盘管、供热盘管、变风量风机、VAV 再热末端。
- VAV 末端采用电再热。

最终选定的制冷侧参数如下：

| 空气环路 | 设计送风量 | 高速额定总制冷量 |
| --- | ---: | ---: |
| `PACU_VAV_bot` | `6.930958 m3/s` | `135845.91243 W` |
| `PACU_VAV_mid` | `5.539595 m3/s` | `110608.05302 W` |
| `PACU_VAV_top` | `5.637534 m3/s` | `111692.74129 W` |

本轮 HVAC 改动的原则是：

- 核心区末端最大送风量保持原值。
- 外区 VAV 末端最大送风量约放大到原来的 `1.5` 倍。
- DX 制冷能力约放大到原来的 `1.10` 倍。
- 尽量保持原始绝对最小送风量，避免因为最小送风量同步变大而导致高设定点热区过冷。
- 不采用过低送风温度硬压温度，因为那会引入不自然的过冷和更多设备警告。

## 独立温度设定点控制对象

每个可控热区都有一个独立的 `ThermostatSetpoint:DualSetpoint`。冷热设定点日程命名规则为：

- 供热设定点：`<ZONE>_HTG_SP_CONTROL`
- 供冷设定点：`<ZONE>_CLG_SP_CONTROL`

例如：

- `CORE_BOTTOM_HTG_SP_CONTROL`
- `CORE_BOTTOM_CLG_SP_CONTROL`
- `PERIMETER_BOT_ZN_1_HTG_SP_CONTROL`
- `PERIMETER_BOT_ZN_1_CLG_SP_CONTROL`

IDF 默认设定值：

- 供热设定点：`16.0 degC`
- 供冷设定点：`26.0 degC`

Python 控制脚本通过 EnergyPlus actuator 写入这些日程值。对应 actuator 信息为：

- Component Type：`Schedule:Constant`
- Control Type：`Schedule Value`
- Actuator Key：日程名称，例如 `CORE_BOTTOM_CLG_SP_CONTROL`

这个方式和 EMS/PythonPlugin 使用的是同一类 EnergyPlus actuator 机制。当前 IDF 中没有保留旧 EMS 程序，因此外部 Python 可以完全接管冷热设定点。

## 输出变量

最终 IDF 已加入 timestep 级输出变量，主要包括：

- `Zone Mean Air Temperature`
- `Zone Thermostat Cooling Setpoint Temperature`
- `Zone Thermostat Heating Setpoint Temperature`
- `Zone Air System Sensible Cooling Rate`
- `Zone Air System Sensible Heating Rate`
- `Zone Air Terminal VAV Damper Position`
- `Zone Air Terminal Sensible Cooling Rate`
- `Zone Air Terminal Sensible Heating Rate`
- `Zone Air Terminal Outdoor Air Volume Flow Rate`
- `Facility Total HVAC Electricity Demand Rate`
- `Facility Total Building Electricity Demand Rate`
- `Facility Total Electricity Demand Rate`
- `Fan Electricity Rate`
- `Cooling Coil Electricity Rate`
- `Heating Coil Electricity Rate`
- `Heating Coil NaturalGas Rate`

这些输出可以用于后续控制器观测量、奖励函数、电力需求分析和设备响应分析。

## 控制示例运行方法

在工作区根目录运行：

```bash
python /jupyterfile/Building_Model/Medium_Office/Model_Use/control_15zone_ems_actuator.py --pattern unique
```

可选模式：

```bash
python /jupyterfile/Building_Model/Medium_Office/Model_Use/control_15zone_ems_actuator.py --pattern uniform_24
python /jupyterfile/Building_Model/Medium_Office/Model_Use/control_15zone_ems_actuator.py --pattern uniform_26
python /jupyterfile/Building_Model/Medium_Office/Model_Use/control_15zone_ems_actuator.py --pattern staggered
```

如果需要输出逐 timestep 原始日志，可以加：

```bash
python /jupyterfile/Building_Model/Medium_Office/Model_Use/control_15zone_ems_actuator.py --pattern unique --write-log
```

默认输出文件：

- `run_15zone_control_example/zone_control_zone_summary.csv`
- `run_15zone_control_example/zone_control_group_summary.csv`
- `run_15zone_control_example/zone_control_summary.json`

## 控制示例验证结果

已测试命令：

```bash
python /jupyterfile/Building_Model/Medium_Office/Model_Use/control_15zone_ems_actuator.py --pattern unique
```

测试结果：

- EnergyPlus exit code：`0`
- 控制热区数量：`15`
- 最大设定点跟踪误差：`0.000000 degC`
- 最大单热区平均绝对温度误差：约 `0.237 degC`
- 最大单热区未满足小时数，阈值为设定点上方 `0.556 degC`：`74.50 h`

分组结果：

| 分组 | 命令供冷设定点 | EnergyPlus 报告供冷设定点 | 平均热区温度 |
| --- | ---: | ---: | ---: |
| 核心区 | `25.000 degC` | `25.000 degC` | `25.033 degC` |
| 底层外区 | `23.750 degC` | `23.750 degC` | `23.873 degC` |
| 中层外区 | `25.750 degC` | `25.750 degC` | `25.778 degC` |
| 顶层外区 | `26.625 degC` | `26.625 degC` | `26.595 degC` |

结论：

- 控制信号链路是正确的：Python 写入的 actuator 值等于 EnergyPlus 报告的 thermostat setpoint。
- 15 个热区都可以独立设置供冷/供热设定点。
- 平均温度可以较好跟随设定点。
- 个别外区在太阳辐射或负荷峰值时会短时高于设定点，这属于天气、太阳得热和 HVAC 容量动态导致的实际温度响应问题，不是 actuator 控制失败。
