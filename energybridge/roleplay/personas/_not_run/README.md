# 备选 Persona — 暂不参与评估

此目录下的文件 **不会被 `load_personas()` 加载**（loader 只扫根目录 `personas/`）。

## 为什么放在这里？

每次完整评估运行耗时较长，当前根目录保留了最精简的 10 个候选：
- 6 个 `basic_role_*`：来自角色建模文档 §6 的复合原型，覆盖典型用户区间
- 4 个 `atom_*`：最关键的单因素回归测试点

这里的 29 个文件已通过 `validate_persona()` 校验，可随时移回根目录启用。

## 何时使用这里的文件？

- **做维度覆盖测试**：移入对应的 `atom_schedule_*` / `atom_comfort_*` 等
- **做历史对比**：移入 `archetype_*` / `derived_*`
- **扩大评估规模**：全部移回根目录即可（共 39 个）

## 文件清单（29个）

| 类型 | 数量 | 说明 |
|------|------|------|
| `archetype_*` | 8 | 原有历史复合用户原型 |
| `derived_*` | 5 | 原有历史衍生用户 |
| `atom_comfort_*` | 3 | comfort 维度其余原子（normal / tolerant / low_tolerance） |
| `atom_control_*` | 3 | control 维度其余原子（suggest / privacy / low_accept） |
| `atom_price_*` | 3 | price 维度其余原子（driven / explain / fatigued） |
| `atom_schedule_*` | 5 | schedule 全部原子 |
| `atom_task_*` | 2 | task 维度其余原子（flexible / semi_rigid） |
