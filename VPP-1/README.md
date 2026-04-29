# VPP-1

VPP-1 是最小化 VPP 上游任务到建筑侧查询命令的原型。

它只做两件事：

1. 生成或接收上游电网/市场任务 `MarketDispatchTask`。
2. 将任务翻译为标准 JSON 风格的建筑侧能力查询命令 `FlexibilityQuery`。

VPP-1 不实现建筑侧 Agent、不接入真实 EnergyPlus、不执行控制、不通知用户、不做收益结算。

## 核心链路

```text
MarketDispatchTask
→ TaskInterpreter
→ FlexibilityQuery
→ 建筑侧 Agent / EnergyPlus
```

## 运行 Demo

```bash
python run_demo.py
```

Demo 会连续运行 10 轮，交替生成 `invitation` 和 `emergency` 两类任务，打印任务摘要与完整查询 JSON，并保存到：

```text
outputs/vpp_1_demo_results.json
```

## 运行测试

```bash
python -m pytest
```

## 当前边界

- 不做建筑侧 Agent。
- 不做 EnergyPlus 物理仿真。
- 不做本地优化。
- 不做用户邀请。
- 不做控制执行。
- 不做收益结算。
