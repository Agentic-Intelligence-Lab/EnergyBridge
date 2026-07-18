# RAG 相似度检索记忆库 v2（加天气特征）

跟 `dr_capacity_memory_toolkit/june_2025_daily_eb_rule_milp/` 是同一类"可复用历史
记忆数据"工具包，结构对齐（`config/` + `data/`），不是一次性 benchmark 输出，因此
单独归档在这里。区别只在于学长那份是 `method=eb_rule_milp`，这份是
`method=EnergyBridge`，且这份多带了 `weather_features` 字段。

## 数据来源与处理流程

1. 基准数据来自 5 households × 2 cities (Germany/Tianjin) × 30 days × method=EnergyBridge
   的历史DR事件仿真结果（`benchmark_results/vpp_patch_2026-07-15/_batch_logs/` 批次），共300条事件。
2. **质量清洗**：15条记录因数据质量问题被剔除（不是本文件独有的新发现，剔除依据见下），
   **本目录下所有文件（核心记忆库 + config + 全部 data/ 派生文件）都已同步物理剔除这15条对应
   的记录/行/baseline，不是留标记跳过**：
   - 5条：天津 2025-06-01（day1），全部5户的EV调度器/打分异常
     （HEMA agent未显式给出`ev_charge_start_h/end_h`，虽然EV实际达到目标SOC，但被
     打分规则误判为策略失败）
   - 10条：德国/天津 day4，实时容量估计器（`capacity_estimator.py`,
     `method=state_physical_with_optional_baseline`）在VPP窗口快照返回近零或退化值
     （只污染`model_bid_kw`/`capacity_recommended_bid_kw`等衍生字段，`realized_delivery`/
     `no_dr_baseline_kwh`本身不受影响）
3. **另有10条曾被标记"疑似质量差"（`event_score<3.0`或电器规避失败）拿去重跑验证**：
   4条重跑后达标并替换为更优结果（仅体现在核心记忆库文件，见下方"已知的一处不一致"）；
   6条重跑2次仍不达标，但排查确认根因是`user_pref_scorer.py`对EV未充满电这一项打分硬顶
   到≤2分，跟实际容量交付无关，且`_similarity()`/`estimate_event_capacity_from_memory()`
   本身不读`event_score`字段——判定不是真问题，**保留在本文件里，未被剔除**。
4. **天气特征增强**：对核心记忆库剩余285条逐条附加`weather_features`字段（5维：
   `t_mean_day, t_max_day, rh_mean_day, ghi_sum_day, cloud_cover_mean_day`），
   由 `energybridge/quantification/weather_shift/features.py::attach_weather_to_memory()`
   计算，跟天气分布修正模块用的同一套特征定义/同一份气象源数据，不是另起一套。
5. **配套 config/data 文件的对齐**：`_batch_logs/` 批次目录本身已包含跟学长
   toolkit 结构对应的原始产出（30天事件排布 config、raw/with-counterfactual 600行
   审计表、300条 no-DR 基线库），直接复用、按同样的15条key过滤剔除，未重新跑仿真。

## 最终产出

**核心记忆库 285条事件**（300条原始 - 15条质量剔除），**每条都带`weather_features`
字段**，`method`字段统一为`"EnergyBridge"`。本文件内不含任何`data_quality_excluded`
标记的记录——需要排除的都已经在生成时物理剔除，不是留标记让检索代码运行时跳过。

## Contents

```
rag_similarity_v2_weather/
├── README.md
├── dr_event_memory.py                         # 修改后完整模块源码（隔离测试版，未合并进生产）
├── config/
│   └── vpp_events_june_memory_merged30.json   # 30天事件排布（原始source，未过滤——是schedule不是逐条数据）
└── data/
    ├── energybridge_daily_dr_memory_rag_v2_weather.json        # 核心RAG记忆库，285条+weather_features
    ├── daily_dr_memory_no_dr_counterfactual_library.json       # no-DR基线库，300→285（按15条key过滤）
    ├── daily_dr_memory_summary_raw.json / .csv                 # 原始运行审计表，600→570行（15条key的no_dr+EnergyBridge两侧都剔除）
    └── daily_dr_memory_summary_with_counterfactual.json / .csv # 同上，比对no-DR基线后的版本，600→570行
```

## 已知的一处不一致（如实说明，不是遗漏）

核心记忆库（`energybridge_daily_dr_memory_rag_v2_weather.json`）里有4条记录是"重跑
后用更优结果替换"的（见上方第3点），**这4条的改进值只体现在核心记忆库文件里，
`daily_dr_memory_summary_raw/with_counterfactual` 这两份审计表仍保留第一次原始跑的
数值**——因为这两份是"实际发生过的运行记录"审计留痕，不是"应该拿去用的最终结果"，
重跑修正属于事后的质量决策，不应该回填改写已发生的审计历史。如果下游需要严格对齐
这4条的口径，请以核心记忆库文件为准。

## RAG 权重（跟天气修正线的IS/KNN权重不是同一类东西）

`_similarity()` 权重与结构调整（新增天气相似度项，z-score+高斯核，按(household,city)
分组标准化）：

```
entity=4.0  city=2.0  method=1.0  hour=2.0  duration=0.5
baseline_kwh=3.5  day_proximity=0.5  weather=0.5
```

LOO回归验证（本库上留一法评估，新旧对比）：

| | err_p90 | 覆盖率(P10-P90) |
|---|---|---|
| 德国 旧→新 | 12.1%→6.9% | 51.7%→69.0% |
| 天津 旧→新 | 22.1%→18.4% | 65.0%→62.9% |
| 整体 旧→新 | 17.3%→14.7% | 58.2%→66.0% |

**状态**：代码改动尚未合并进生产 `dr_event_memory.py`，本目录数据可独立使用于验证/
天气修正等下游工作，不依赖代码改动是否已部署。
