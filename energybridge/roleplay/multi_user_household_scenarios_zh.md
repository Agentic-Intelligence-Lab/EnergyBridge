# 多用户家庭 Benchmark Scenario 设计草案

日期：2026-06-19

本文档用于评审 5 个多用户家庭 benchmark scenario。每个 scenario 表示一种家庭结构；同一个家庭结构可以分别放到 `Tianjin` 和 `Germany` 两个地点中运行，从而形成地点 variation。天津没有 day-ahead price，图中建议使用每日电力消耗 `energy_kwh_per_day`；德国有电价数据，图中建议使用总电费 `electricity_cost_eur`。

## 设计原则

1. 先复用当前已有的单用户 role JSON，不急着新增成员级 persona JSON。
2. 多用户的新增部分应该是“家庭级 JSON + prompt”，用于固定成员组成、成员关系、共享物理电器和日程合并；成员级 role JSON 仍然只负责各自的 role-play 偏好、评论、选择和打分。
3. 每次 VPP 事件前，每个成员独立评论并选择策略；事件后，每个成员独立评分并给出评论。矩阵表中的用户分数取成员分数均值，同时保留成员级反馈用于审查。
4. 家庭中任一已配置共享电器的关键任务没有被输出策略时，应在对应成员评分中被惩罚；如果涉及老人、幼儿或高舒适敏感成员，应优先惩罚舒适违规。
5. 同一家庭在天津和德国只改变天气/电价/地点语境，不改变家庭成员构成。这样地点差异主要测试方法对物理环境和价格信号的适应性。
6. 每个家庭保留 3-5 名成员，避免过度复杂，但要覆盖通勤、居家、照护、EV、低激励、低信任、日程不稳定等核心行为轴。
7. 电器配置尽量满配。第一版多用户 benchmark 默认把 `ac`、`washer`、`dryer`、`dishwasher`、`water_heater`、`ev` 都加入家庭共享设备集合；只有在实现或物理模型明确不支持时才减少。这样可以测试方法是否真正覆盖所有可控设备，而不是只在少数电器上表现好。

## 已有 role JSON 速查

| role JSON | 中文含义 | 适合映射的家庭成员 |
| --- | --- | --- |
| `basic_role_a_commuter_price_cooperative.json` | 价格敏感、愿意配合的普通通勤者 | 普通上班族、愿意看节省金额的家庭主力 |
| `basic_role_b_home_comfort_gated.json` | 全天在家、舒适优先、需要确认 | 居家办公者、对空调舒适要求高的人 |
| `basic_role_c_irregular_cautious.json` | 日程不稳定、谨慎、需要确认 | 轮班工作者、经常外出或计划变化的人 |
| `basic_role_d_commuter_ideal_dr.json` | 稳定通勤、高自动化信任、理想 DR 用户 | 高信任通勤者、愿意自动调度的家庭成员 |
| `basic_role_e_caregiver_low_dr.json` | 照护/弱势成员场景、低 DR 价值 | 照护者、老人/幼儿舒适安全代理 |
| `basic_role_f_commuter_ev_optimizer.json` | EV 通勤者、自动优化充电 | EV 车主、德国价格响应重点角色 |
| `atom_comfort_sensitive.json` | 单轴舒适敏感 | 老人、怕热/怕冷成员、睡眠敏感成员 |
| `atom_control_auto.json` | 单轴高信任自动控制 | 愿意让系统自动执行的成员 |
| `atom_price_indifferent.json` | 单轴价格不敏感 | 不在乎电费、只关心便利的人 |
| `atom_task_rigid.json` | 单轴任务时间刚性 | 学生、固定作息者、固定家务窗口 |

## Scenario 1：标准双职工通勤家庭

**家庭 ID**：`household_s1_dual_commuter_standard`

**成员组成**

| 成员 | 家庭角色 | 使用的 role JSON | 映射理由 |
| --- | --- | --- | --- |
| 父亲 | EV 通勤者，关心次日出行和充电 | `basic_role_f_commuter_ev_optimizer.json` | 让普通家庭也覆盖 EV 充电窗口和 SOC 约束 |
| 母亲 | 稳定通勤者，高信任自动化 | `basic_role_d_commuter_ideal_dr.json` | 代表家庭中愿意让系统自动调度的一方，并提供可平移家务负荷 |
| 孩子 | 学生，晚间学习，任务时间较固定 | `atom_task_rigid.json` | 现有库没有学生 persona，用任务刚性代理固定作息和晚间约束 |
| 老人 | 傍晚和夜间舒适敏感 | `atom_comfort_sensitive.json` | 代表多成员家庭中的舒适上限约束 |

**推荐设备集合**

- `ac`
- `washer`
- `dryer`
- `dishwasher`
- `water_heater`
- `ev`

**合理性分析**

这是最接近日常城市家庭的基准：白天大多数人外出，傍晚 18:00 后集中回家，VPP 事件、晚饭后洗碗、洗衣、烘干、热水、EV 充电和空调舒适同时出现。它能测试方法是否能在普通家庭中完成满设备调度，同时不牺牲老人和学生的晚间舒适。

**是否需要新增 role-play prompt**

- 成员级 prompt：暂时不需要新增，先复用现有 JSON。
- 家庭级 prompt：需要新增。

**建议新增家庭级 prompt**

```text
你在模拟一个四口之家：两个成年人通勤上班，一个学生晚间在家学习，一位老人对温度变化敏感。请把家庭满意度看作共同决策结果，而不是单个用户的偏好。

评分原则：
1. 空调舒适必须优先满足老人和学生的晚间需求；如果室温超出舒适范围，即使节能效果好也要扣分。
2. washer、dryer、dishwasher、water_heater 和 EV 都属于家庭刚需或强约束任务。任何已配置且当天需要完成的电器，如果控制策略没有明确输出或导致任务未完成，应给出明显惩罚。
3. 父母可以接受合理的自动调度，尤其是把 washer/dryer/dishwasher/water_heater/EV 移出 VPP 时段，但不能牺牲孩子学习、老人休息或 EV 次日出行。
4. 如果策略在 18:00-19:00 VPP 窗口减少用电，同时所有任务按时完成，家庭应给较高分。
```

## Scenario 2：多代同住照护家庭

**家庭 ID**：`household_s2_multigeneration_caregiver`

**成员组成**

| 成员 | 家庭角色 | 使用的 role JSON | 映射理由 |
| --- | --- | --- | --- |
| 主要照护者 | 白天在家，照顾老人/幼儿 | `basic_role_e_caregiver_low_dr.json` | 直接对应照护场景，强调安全和稳定 |
| 老人 | 全天在家，舒适敏感 | `atom_comfort_sensitive.json` | 强化弱势成员舒适约束 |
| 通勤家庭成员 | 晚间回家，负责 EV 出行 | `basic_role_f_commuter_ev_optimizer.json` | 在照护场景中也覆盖 EV 充电，但 EV 不能压过照护舒适 |
| 居家办公成员 | 白天在家，需要稳定空调 | `basic_role_b_home_comfort_gated.json` | 让白天 occupancy 不为空，测试不能简单关空调 |
| 学生/幼儿代理 | 固定睡眠和热水需求 | `atom_task_rigid.json` | 用任务刚性代理固定作息和不可随意移动的需求 |

**推荐设备集合**

- `ac`
- `washer`
- `dryer`
- `dishwasher`
- `water_heater`
- `ev`

**合理性分析**

这是低 DR 弹性家庭，但仍然采用满设备配置。它能测试算法是否会为了电价或 VPP 过度调高空调设定点、推迟热水、延误 EV 或跳过家务。好的方法不一定能提供最大削峰，但应明确保护照护场景中的舒适、EV 次日出行和所有任务完成。

**是否需要新增 role-play prompt**

- 成员级 prompt：暂时不需要新增。
- 家庭级 prompt：强烈需要新增，因为此类家庭不能用单个 persona 的满意度近似。

**建议新增家庭级 prompt**

```text
你在模拟一个多代同住家庭：家中有照护者、老人、固定作息的孩子/学生，以及至少一名通勤成员。家庭的首要目标是安全、舒适和日常照护稳定，其次才是节能或参与 VPP。

评分原则：
1. 老人和孩子的舒适、安全、热水需求具有最高优先级。任何导致室温明显不适、热水未准备好、关键家务未完成、EV 未达到次日出行需求的策略，都应低分。
2. 家庭可以接受非常温和的节能行为，例如无人区域空调关闭、家务提前完成、热水和 EV 避开 VPP，但不接受需要成员主动忍耐的方案。
3. 如果系统要求确认却直接执行了影响舒适的动作，应扣分。
4. 如果策略没有覆盖所有已配置电器，或依赖默认/兜底而非明确策略，应按任务未完成处理。
```

## Scenario 3：居家办公混合家庭

**家庭 ID**：`household_s3_hybrid_work_from_home`

**成员组成**

| 成员 | 家庭角色 | 使用的 role JSON | 映射理由 |
| --- | --- | --- | --- |
| 居家办公者 | 工作日白天在家，舒适优先 | `basic_role_b_home_comfort_gated.json` | 直接对应 stay-at-home / WFH comfort-gated |
| 通勤伴侣 | 价格敏感，晚间回家且需要 EV 充电 | `basic_role_f_commuter_ev_optimizer.json` | 代表晚高峰回家的可协商用户，同时覆盖 EV 充电约束 |
| 自由职业/学生 | 作息不稳定，谨慎确认 | `basic_role_c_irregular_cautious.json` | 引入不稳定日程，测试实时 re-planning |

**推荐设备集合**

- `ac`
- `washer`
- `dryer`
- `dishwasher`
- `water_heater`
- `ev`

**合理性分析**

这个家庭的关键是白天有人在家，同时设备尽量满配。它可以验证 occupancy-aware AC 策略是否真的基于日程，而不是简单假设白天无人。它也能测试方法是否能处理“一个人希望省钱，另一个人工作时不想被打扰”，以及 EV/烘干/洗碗/热水等多设备同时存在时的冲突。

**是否需要新增 role-play prompt**

- 成员级 prompt：暂时不需要新增。
- 家庭级 prompt：需要新增。

**建议新增家庭级 prompt**

```text
你在模拟一个三人混合办公家庭：一名成员白天在家办公，需要稳定舒适；一名成员通勤，晚间回家并关心电费；另一名成员作息不稳定，通常需要解释和确认。

评分原则：
1. 白天只要有人在家办公，就不能把空调当作无人状态处理；工作时段的温度波动应被严格扣分。
2. washer、dryer、dishwasher、water_heater 和 EV 可以被移动到低影响时段，但必须明确输出策略并完成任务。
3. 对日程不稳定成员，策略需要留有余量；如果依赖过强的固定历史日程假设，应降低满意度。
4. 如果策略能在不影响白天办公舒适的情况下避开 VPP 和高能耗时段，应给高分。
```

## Scenario 4：EV 通勤优化家庭

**家庭 ID**：`household_s4_ev_commuter_flexible`

**成员组成**

| 成员 | 家庭角色 | 使用的 role JSON | 映射理由 |
| --- | --- | --- | --- |
| EV 车主 | 每天通勤，晚上回家充电 | `basic_role_f_commuter_ev_optimizer.json` | 直接对应 EV 充电约束和 SOC 保证 |
| 伴侣 | 高信任自动调度，任务灵活 | `basic_role_d_commuter_ideal_dr.json` | 增强可调度洗衣/洗碗/热水灵活性 |
| 家庭成员 | 不太在意电价，更重视便利 | `atom_price_indifferent.json` | 防止整个家庭过度 price-driven |
| 可选成员 | 自动化接受度高 | `atom_control_auto.json` | 如果需要 4 人版本，可加入高信任成员 |

**推荐设备集合**

- `ac`
- `ev`
- `washer`
- `dryer`
- `dishwasher`
- `water_heater`

**合理性分析**

这是高 DR 潜力家庭。EV 充电是主要可调负荷，洗衣、烘干、洗碗和热水提供额外灵活性。它特别适合 Germany 场景，因为 day-ahead price 会明显影响 EV 充电窗口；天津场景则可以测试没有价格时是否仍能避开 VPP。

**是否需要新增 role-play prompt**

- 成员级 prompt：暂时不需要新增。
- 家庭级 prompt：需要新增，尤其要明确 EV SOC 约束是硬约束。

**建议新增家庭级 prompt**

```text
你在模拟一个有 EV 的通勤家庭。家庭愿意参与自动优化，但 EV 次日出行需求必须被保证。节省电费和 VPP 支持很重要，但不能导致 EV 未达到目标 SOC，也不能跳过必要家务。

评分原则：
1. EV 充电必须给出明确的 start/end 充电窗口；如果 EV 未达到目标 SOC 或策略没有覆盖 EV，应给出惩罚性低分。
2. washer、dryer、dishwasher、water_heater 也需要明确策略并完成任务；不能只优化 EV 而忽略其他电器。
3. 德国地点下，低价时段充电和避开 VPP 应被奖励；天津地点下，没有电价时也应优先避开 VPP 和保证任务完成。
4. 如果策略兼顾 EV、家务、热水和空调舒适，并减少 VPP 窗口用电，应给高分。
```

## Scenario 5：年轻合租/室友家庭

**家庭 ID**：`household_s5_shared_roommates_irregular`

**成员组成**

| 成员 | 家庭角色 | 使用的 role JSON | 映射理由 |
| --- | --- | --- | --- |
| 室友 A | 规律上班，拥有 EV，愿意节省 | `basic_role_f_commuter_ev_optimizer.json` | 让合租场景也覆盖 EV 充电和共享车位约束 |
| 室友 B | 轮班/不稳定日程 | `basic_role_c_irregular_cautious.json` | 代表合租中的不确定 occupancy |
| 室友 C | 只关心便利，不在意电价 | `atom_price_indifferent.json` | 引入低激励冲突 |
| 室友 D | 任务时间固定，低容忍改动 | `atom_task_rigid.json` | 代表固定洗衣/热水窗口冲突 |

**推荐设备集合**

- `ac`
- `washer`
- `dryer`
- `dishwasher`
- `water_heater`
- `ev`

**合理性分析**

合租家庭可以测试多用户偏好冲突：有人愿意节省，有人不在乎电价，有人作息不稳定，有人固定使用洗衣机、烘干机、热水器和 EV 车位。它比普通家庭更容易出现“系统认为合理，但某个成员强烈不满意”的情况。

**是否需要新增 role-play prompt**

- 成员级 prompt：暂时不需要新增。
- 家庭级 prompt：需要新增，而且需要比其他 scenario 更强调冲突调解。

**建议新增家庭级 prompt**

```text
你在模拟一个四人合租家庭。室友之间没有完全统一的偏好：有人愿意节省电费，有人只关心便利，有人作息不稳定，有人有固定任务时间。家庭满意度应反映“最不满意成员”的影响，而不是简单平均。

评分原则：
1. 如果策略只服务价格敏感成员、却明显影响价格不敏感或任务刚性成员，应降低分数。
2. washer、dryer、dishwasher、water_heater 和 EV 车位/充电器是共享资源，必须明确安排并避免与成员固定需求冲突。
3. 对不稳定日程成员，不要假设其一定不在家；如果 occupancy 不明确，应保守保护舒适。
4. 如果策略能清楚解释安排、避免 VPP、完成任务，并且没有让任一室友承担明显不便，应给高分。
```

## 推荐优先级

| 优先级 | Scenario | 原因 |
| --- | --- | --- |
| P0 | S1 标准双职工通勤家庭 | 最接近普通家庭基准，适合作为主 benchmark |
| P0 | S2 多代同住照护家庭 | 用于测试安全、舒适和低 DR 弹性边界 |
| P0 | S4 EV 通勤优化家庭 | 用于测试高灵活性和 EV 充电约束 |
| P1 | S3 居家办公混合家庭 | 用于验证 occupancy-aware AC 和白天有人场景 |
| P1 | S5 年轻合租/室友家庭 | 用于压力测试多用户冲突和不确定日程 |

## 后续实现建议

1. 新增 household-level schema，例如：

```json
{
  "household_id": "household_s1_dual_commuter_standard",
  "members": [
    {"member_id": "father", "persona_id": "basic_role_f_commuter_ev_optimizer"},
    {"member_id": "mother", "persona_id": "basic_role_d_commuter_ideal_dr"}
  ],
  "household_prompt": "...",
  "appliance_config_policy": "maximal_shared_device_set",
  "scoring_policy": "comfort_safety_min_then_task_completion_then_energy"
}
```

2. 日程合并时，occupancy 应按“任一成员在家则家中有人”处理；舒适约束应按最严格成员或弱势成员优先处理。
3. 电器配置应取家庭层面的共享电器，而不是简单把每个成员的 appliance config 相加。第一版建议统一使用满配共享设备：`ac`、`washer`、`dryer`、`dishwasher`、`water_heater`、`ev`。
4. 评分时建议拆成三层：
   - 成员个人满意度；
   - 家庭硬约束：舒适安全、热水、EV SOC、任务完成；
   - 家庭总体满意度：可用加权平均，但弱势成员和任务失败要有下限约束。
5. 在天津/德国地点 variation 中，家庭组成不变，只替换天气、电价和报告指标。
