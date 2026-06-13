# Persona Library — Schema v2.0

用于 EnergyBridge 角色扮演评估（role-play evaluation）的家庭用户画像库。  
建模依据：《家庭用户行为角色建模》，六维标签体系。

---

## 1. 设计理念

### 1.1 为什么需要 persona？

不同的家庭用户对价格、舒适和控制权的态度差别很大——有人全权授权给智能体，有人每次都要确认；
有人温度稍微偏一度就不舒服，有人完全无所谓。这些差异决定了智能体应该用什么策略对话、解释多少、
要不要主动行动。

Persona 库的目标是提供一组**行为稳定、可复现的虚拟用户**，让评估结果可以跨版本对比。

库按三层组织：
- **Atoms（原子）**：每个只有一个维度是极端值，其余全部中性。用来单独验证智能体对某一类用户特征的处理。
- **Basic（文档原型）**：直接来自角色建模文档第6节的复合示例，覆盖典型用户场景，供人工筛选。
- **Archetype/Derived（历史复合）**：原有 8 个 archetype + 5 个 derived，保持不变。

### 1.2 六维标签

每个 persona 在 6 个维度上各取一个值，来自《家庭用户行为角色建模》§4：

```
persona = schedule × comfort × task × price × control × grid_value
```

| 维度 | 含义 | 驱动系统中的什么行为 |
|------|------|---------------------|
| `schedule` | 用户何时在家、离家、睡觉 | 可调负荷时间窗口 / 预冷预热触发 |
| `comfort` | 温度敏感程度 | 温控 DR 的可接受幅度和持续时长 |
| `task` | 家务/EV 任务是否可重排 | 洗衣/洗碗/热水/EV 的调度策略 |
| `price` | 经济激励响应强度 | DR 事件触发阈值、解释详细程度 |
| `control` | 对智能体自动控制的信任 | 是否需要确认、是否允许自动执行 |
| `grid_value` | 对电网的柔性贡献价值 | 聚合商视角的调度优先级 |

**有效枚举值（来自 schema.py VALID_TAGS）：**

| 维度 | 枚举值 |
|------|--------|
| `schedule` | `regular_commuter` \| `stay_at_home` \| `night_owl` \| `irregular` \| `caregiver` |
| `comfort` | `temp_tolerant` \| `normal_comfort` \| `temp_sensitive` \| `low_control_tolerance` |
| `task` | `flexible` \| `semi_rigid` \| `rigid` \| `ev_constrained` |
| `price` | `price_sensitive` \| `needs_explanation` \| `low_incentive` \| `event_fatigue` |
| `control` | `high_trust_auto` \| `suggestion_first` \| `confirm_required` \| `privacy_sensitive` \| `low_auto_accept` |
| `grid_value` | `evening_peak` \| `stable_flex` \| `uncertain_flex` \| `short_peak_cut` \| `low_value` |

---

## 2. Atoms —— 20 个原子用户

每个 atom 只有一个维度是极端值，其余设为中性默认。用来做**单因素回归测试**。

**中性基线**：`schedule=regular_commuter` / `comfort=normal_comfort` / `task=semi_rigid` / `price=needs_explanation` / `control=suggestion_first` / `grid_value=uncertain_flex`

### 2.1 Schedule 维度（5个）

| 文件名 | 极端值 | 用户特征 | 测什么 |
|--------|--------|---------|--------|
| `atom_schedule_commuter` | `regular_commuter` | 工作日规律离家，晚高峰集中用能 | 智能体能否利用高可预测性提前规划 |
| `atom_schedule_home` | `stay_at_home` | 白天持续在家，全天舒适约束存在 | 智能体会不会把白天当空闲窗口 |
| `atom_schedule_night` | `night_owl` | 凌晨才睡，夜间用电活跃 | 智能体会不会把深夜当安全调度时段 |
| `atom_schedule_irregular` | `irregular` | 作息波动大，计划频繁变更 | 智能体会不会过度依赖历史规律 |
| `atom_schedule_caregiver` | `caregiver` | 家有老人/幼儿，高保护需求 | 智能体能否识别脆弱成员并提升舒适阈值 |

### 2.2 Comfort 维度（4个）

| 文件名 | 极端值 | 用户特征 | 测什么 |
|--------|--------|---------|--------|
| `atom_comfort_tolerant` | `temp_tolerant` | 可接受±2°C，对预冷预热接受度高 | 智能体是否充分利用温控自由度 |
| `atom_comfort_normal` | `normal_comfort` | 可接受±1°C，长时间偏离会不满 | 中性对照组 |
| `atom_comfort_sensitive` | `temp_sensitive` | 仅±0.5°C，容易手动恢复原设定 | 智能体有没有在敏感用户前过度调温 |
| `atom_comfort_low_tolerance` | `low_control_tolerance` | 温度敏感+拒绝自动干预 | 双重约束下的协商降级策略 |

### 2.3 Task 维度（3个）

| 文件名 | 极端值 | 用户特征 | 测什么 |
|--------|--------|---------|--------|
| `atom_task_flexible` | `flexible` | 洗衣/洗碗/热水均可延迟1-4小时 | 智能体能否积极利用可平移负荷 |
| `atom_task_semi_rigid` | `semi_rigid` | 部分可延迟但有硬截止时间 | 截止约束识别（中性基线） |
| `atom_task_rigid` | `rigid` | 家务时间固定，不为电价改变节奏 | 任务拒绝后智能体能否降级为纯温控建议 |

### 2.4 Price 维度（4个）

| 文件名 | 极端值 | 用户特征 | 测什么 |
|--------|--------|---------|--------|
| `atom_price_driven` | `price_sensitive` | 愿为省钱改变行为，关注节省金额 | 智能体是否用省钱框架而非技术术语 |
| `atom_price_explain` | `needs_explanation` | 需要收益/影响/退出说明才配合 | 智能体解释的完整性与透明度 |
| `atom_price_indifferent` | `low_incentive` | 省钱不是目标，舒适便利优先 | 智能体能否切换到非价格沟通策略 |
| `atom_price_fatigued` | `event_fatigue` | 频繁请求后响应意愿下降 | 智能体的打扰频率管理和疲劳识别 |

### 2.5 Control 维度（4个）

| 文件名 | 极端值 | 用户特征 | 测什么 |
|--------|--------|---------|--------|
| `atom_control_auto` | `high_trust_auto` | 全权委托，只要事后报告 | 智能体会不会对已授权用户多余确认 |
| `atom_control_suggest` | `suggestion_first` | 需要建议+简单确认，通常批准 | 建议-确认交互流程（中性基线） |
| `atom_control_privacy` | `privacy_sensitive` | 质疑数据采集，敏感作息推断 | 智能体的数据最小化说明策略 |
| `atom_control_low_accept` | `low_auto_accept` | 完全拒绝自动控制，只接受提醒 | 智能体能否正确退回纯通知模式 |

> Atom 用于**回归测试**：改了智能体代码后快速验证某个边界有没有退步。不适合完整对话评估。

---

## 3. Basic —— 6 个文档原型（供人工筛选）

直接来自《家庭用户行为角色建模》§6 的复合示例角色，**尽可能丰富**，供研究者人工筛选后用于评估。

| 文件名 | 维度组合 | 一句话描述 |
|--------|---------|-----------|
| `basic_role_a_commuter_price_cooperative` | 规律通勤 + 普通舒适 + 任务可延迟 + 价格敏感 + 建议优先 + 晚高峰 | 上班族，给省钱理由就配合，是最常见合作型 |
| `basic_role_b_home_comfort_gated` | 白天居家 + 温度敏感 + 任务半刚性 + 低激励 + 确认授权 + 短时削峰 | 居家办公，不为小钱改舒适，每次必须先问 |
| `basic_role_c_irregular_cautious` | 不规律 + 普通舒适 + 任务刚性 + 解释需求 + 确认授权 + 高不确定柔性 | 作息乱，今天的计划跟昨天可能完全不同 |
| `basic_role_d_commuter_ideal_dr` | 规律通勤 + 温度宽容 + 任务可延迟 + 价格敏感 + 全自动 + 稳定柔性 | 理想DR用户，作息规律+温宽容+全权委托 |
| `basic_role_e_caregiver_low_dr` | 家庭照护 + 温度敏感 + 任务刚性 + 低激励 + 低自动接受 + 低价值 | 家有老人，舒适安全第一，不适合DR |
| `basic_role_f_commuter_ev_optimizer` | 规律通勤 + 普通舒适 + EV约束 + 价格敏感 + 全自动 + 稳定柔性 | 有EV，低谷充电+SOC保障，典型V2G场景 |

> 这 6 个角色**来自文档建模示例**，覆盖了从"理想合作"到"低DR价值"的典型区间。建议从这里开始选取评估用角色。

---

## 4. Archetype / Derived —— 历史复合用户（保持不变）

原有 8 个 archetype 和 5 个 derived，行为稳定，不做修改。

### 4.1 Archetype（8个）

| 文件名 | 一句话 | 关键特征 |
|--------|--------|---------|
| `archetype_alpha_commuter_rational` | 普通上班族，给省钱理由就配合 | 建议优先，晚高峰集中 |
| `archetype_beta_caregiver_protective` | 家里有老人/小孩，任何调节都要先问 | 温度敏感，每次确认 |
| `archetype_gamma_night_owl_techie` | 夜猫子，装了EV，全权交给智能体 | 全自动，夜间最灵活 |
| `archetype_delta_eco_prosumer` | 装了光伏和储能，想最大化自用 | 环保动机，全自动 |
| `archetype_epsilon_privacy_guardian` | 在家办公，不想被打扰也不想被监控 | 隐私保护，任务固定 |
| `archetype_zeta_gig_worker_uncertain` | 自由职业，作息不定，每次操作都要确认 | 不规律，每次授权 |
| `archetype_eta_shift_worker_ev` | 轮班工人，EV几点必须充满是硬约束 | 作息不规律，EV出发时间固定 |
| `archetype_theta_budget_maximizer` | 就想省电费，全权委托智能体随便调 | 纯经济动机，全自动 |

### 4.2 Derived（5个）

| 文件名 | 混合自 | 是个什么人 |
|--------|--------|-----------|
| `derived_01_commuter_family_with_kids` | α + β | 双职工家庭，孩子放学前不能太热，校服得在截止前洗完 |
| `derived_02_remote_worker_eco_starter` | δ + ε | 居家办公、刚装了3kW太阳能板，想用光伏窗口但还不太懂 |
| `derived_03_energy_anxious_retiree` | β + ε | 退休夫妻，电费焦虑，午睡绝对不能打扰，不信任自动化 |
| `derived_04_young_dr_fatigued` | α + θ | 年轻租客，一开始很配合，现在被问得有点烦了 |
| `derived_05_weekend_homebody_price_learner` | α + ε | 周末宅家，工作日正常上班，刚开始学看分时电价 |

---

## 5. 文件结构

```
personas/
  atom_comfort_*.json        # comfort 维度原子（4个）
  atom_control_*.json        # control 维度原子（4个）
  atom_price_*.json          # price 维度原子（4个）
  atom_schedule_*.json       # schedule 维度原子（5个）
  atom_task_*.json           # task 维度原子（3个）
  basic_role_*.json          # 文档§6 复合原型（6个，供筛选）
  archetype_*.json           # 历史复合用户（8个）
  derived_*.json             # 历史衍生用户（5个）
  calendars/<persona_id>/calendar_7day.json
```

**总计：39 个 persona，全部通过 `validate_persona()` 校验。**

### 5.1 Calendar 配套日程

`calendars/<persona_id>/calendar_7day.json` 是 EnergyBridge 自研的 persona 离线
合成周日程。它不依赖外部日历 API，而是为 benchmark 固定 7 天日程，使 role-play
评估可复现。Day 1 固定为 Sunday，
因此当前 3 天 benchmark 对应 Sunday、Monday、Tuesday；后续扩展到 7 天时可直接
覆盖完整工作日/非工作日。

Calendar 会自动注入到：
- VPP 前的三种候选策略生成；
- role-play LLM 对 A/B/C 策略的选择；
- VPP 结束后的满意度评分。

它提供的信息包括：当天事件、18:00-19:00 VPP 窗口冲突、回家时间、洗澡热水截止、
EV 下一次出发时间、洗衣/洗碗等家务截止约束。这样用户模拟不再只依赖静态 persona，
而会根据当天日程判断某个策略是否真实可接受。

---

## 6. 使用示例

```python
from energybridge.roleplay.loader import load_personas

# 加载全部
all_personas = load_personas()

# 只用 atom 做回归测试
atoms = [p for p in all_personas if p["meta"]["persona_type"] == "atom"]

# 只用 basic 做评估对话
basic = [p for p in all_personas if p["meta"]["persona_type"] == "archetype"
         and p["id"].startswith("basic_")]
```
