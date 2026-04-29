# VPP-1 架构说明

VPP-1 是当前 VPP 原型中的第一个最小可交付版本。它只实现：

```text
MarketDispatchTask
→ TaskInterpreter
→ FlexibilityQuery
→ 建筑侧 Agent / EnergyPlus
```

## 模块边界

- `core/`：枚举、dataclass 数据结构和 JSON 序列化工具。
- `market/`：生成邀约型和紧急型上游市场/调度任务。
- `interpreter/`：把上游任务翻译成建筑侧能力查询命令。
- `simulation/`：默认小型办公建筑场景与 10 轮 demo runner。

## 当前不做的事情

VPP-1 不实现建筑侧 Agent、不接入真实 EnergyPlus、不做设备控制、不通知用户、不做结算。建筑侧 Agent / EnergyPlus 在本版本中只是 JSON 查询命令的未来接收方。
