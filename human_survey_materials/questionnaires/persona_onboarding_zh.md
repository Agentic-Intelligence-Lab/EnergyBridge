# 用户偏好问卷

用途：用 4 道选择题记录参与者本人的家庭能源偏好。请按自己的真实想法回答，不需要扮演角色，预计用时 2 分钟。该结果不会用来给你分配角色，只用于分析个人偏好是否影响后续角色扮演。

这份问卷与 EnergyBridge 给 role-play LLM 的 onboarding 问卷使用同一组 4 个 `question_id` 和同一套选项编码。研究者只把它作为参与者自身偏好的记录和角色渗入分析变量。

## 背景说明

虚拟电厂/VPP 事件指电网在某个高峰时段希望家庭短时间降低用电。例如 18:00-19:00，系统可能建议稍微调整空调温度、提前或推迟洗衣机/洗碗机/热水器/电动车充电等操作。你可以接受，也可以拒绝。

## 核心问题

### Q1. VPP 事件中的优先级

`question_id`: `vpp_priority`

当系统建议你在 1 小时 VPP 高峰事件中减少用电时，家庭应该优先考虑什么？

请选择最接近的一项。

- `comfort_routine_first`: 舒适和日常 routine 优先，空调温度、热水、家务节奏不能明显受影响。
- `bill_savings_first`: 电费节省优先，只要能明确省钱，我可以接受一些不便。
- `grid_support_first`: 支持电网/环保优先，只要影响可控，我愿意配合削峰。
- `balanced_tradeoff`: 平衡处理，在低干扰的前提下节省电费并支持电网。
- `confirm_before_changes`: 解释和确认优先，系统必须讲清楚，并由我确认后再执行。

### Q2. 空调温度弹性

`question_id`: `thermostat_flexibility`

如果安全没有问题，在 VPP 高峰事件中，你通常能接受多大的临时空调设定温度变化？

请选择最接近的一项。

- `almost_none_0_5c`: 几乎不能接受，约 0.5°C 或更小。
- `small_1c_short`: 可以接受约 1°C，但必须时间较短。
- `moderate_1_2c_with_benefit`: 如果省钱或支持电网的收益清楚，可以接受约 1-2°C。
- `larger_when_unoccupied`: 如果家里没人，或不影响睡眠/工作/照护，可以接受更大变化。

### Q3. 家电调度授权

`question_id`: `appliance_shift_consent`

对于洗衣机、洗碗机、热水器、电动车充电这类设备，如果完成期限能保证，系统可以自动调整时间吗，还是必须先问你？

请选择最接近的一项。

- `do_not_move_without_approval`: 未经明确同意不要调整，必须按我的原计划执行。
- `shift_1_2h_deadline_protected`: 可以提前或推迟 1-2 小时，但必须保证按时完成。
- `shift_to_cheaper_periods`: 可以移到更便宜的时段，只要不影响热水、洗衣、出行等可用性。
- `automatic_optimization_ok`: 可以自动优化，我不需要每次确认。

### Q4. 日程和 routine 约束

`question_id`: `calendar_routine_constraints`

哪些日程或家庭 routine 不应该被打扰？尤其是傍晚回家、吃饭、洗澡、照护、睡眠或工作安排。

可选择 1-2 项。

- `arrival_comfort`: 回家时的舒适必须保证，例如到家前后空调不能太差。
- `meals_chores`: 吃饭和固定家务安排不能被打乱。
- `shower_hot_water`: 洗澡和热水时间必须保证。
- `caregiving_sleep_work`: 照护、睡眠或工作安排必须保证。
- `irregular_confirm_same_day`: 我的日程经常变，系统当天行动前最好再次确认。

## 可选补充（1 题）

5. 对你来说，家庭能源系统最不能影响的一件事是什么？（一句话即可）
