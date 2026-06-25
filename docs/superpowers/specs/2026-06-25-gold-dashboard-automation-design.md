# 黄金看板:数据自动化 + 金价层 设计稿

设计日期:2026-06-25
负责人:xuwang
状态:方向已通过(2026-06-25 评审),本修订版作为执行依据

## 1. 背景与目标

当前黄金看板(`黄金数据驱动跟踪`)的核心日频数据来自手工导出的 Wind Excel
(`招商证券:黄金图表整理2606.xlsx`),而 FRED(通胀预期)、CFTC(仓位)已自动化。
这造成两个问题:

1. **数据获取脆弱**:最关键的实际利率/美元/金价/ETF/波动率全靠手工导出,不可复现、易过期。
2. **数据质量虚标**:`build_site.py` 几乎把所有层硬编码为 `data_quality="fresh"`,
   没有任何 staleness 计算。实测隐含波动率(GVZ)停在 `2026-05-29`,比其它序列(`06-24`)
   晚约 4 周,却仍显示 `fresh`——违背了项目自身在 `docs/pantheon_research_dashboard_notes.md`
   里列为"最值得借鉴"的数据质量显性化原则。

本轮目标(遵循奥卡姆剃刀 + 科学性):

- 用本机 `tkf_wind` 把核心序列自动化,先退役核心日频 Excel 依赖(增量推进)。
- 引入真实 staleness 判定。
- 补一个缺失的关键观察维度:**金价自身的趋势/动量**作为独立评分层。
- 把经济政策不确定性(EPU)、地缘政治风险纳入评分。

设计理念为奥卡姆剃刀(只做有增量价值的改动、不重写可用部分)与科学性
(每个数值都可溯源、可对账、可证伪)并重。

## 2. 范围

### 本轮做
- 数据管道:`SPTAUUSDOZ.IDC`(金价)、`USDX.FX`(美元)、`GVZ.GI`(波动率)切到 Wind;
  实际利率切到 FRED `DFII10`;通胀预期 `T10YIE` / 仓位 CFTC 维持自动化。
- 新增「价格与趋势」评分层(金价 vs 200 日均线 + 3 个月动量)。
- 新增「经济政策不确定性」「地缘政治风险」两个评分层(本轮自 Excel 既有 sheet 读取)。
- 真实 staleness 判定,替换硬编码 `fresh`。
- 每个新评分层配关系检验卡,暴露其"有效/失效"。

### 本轮不做(留备注,见 §7)
- ETF 持仓 / 央行储备 / M2 估值 / EPU / 地缘 的 Wind EDB 代码发现与切换
  (增量退役,下一轮);本轮这些继续从 Excel 读取,但改用真实 staleness。
- 评分方法论重构:层级共线性双重计数、重叠窗口显著性、固定相关阈值。

## 3. 决策记录(含验证证据)

| # | 决策 | 依据 |
|---|------|------|
| D1 | 数据管道沿用"本地抓取→提交 CSV→CI 渲染"模型 | `pages.yml` 在 GitHub `ubuntu-latest` 上**只**跑 `build_site.py`;Wind 服务器 `10.92.26.150` 为 LAN,CI 不可达。Wind 抓取必须本地化。 |
| D2 | 金价/美元/波动率走 Wind wsd | 已对账:`SPTAUUSDOZ.IDC`=3991.7、`USDX.FX`=101.5745(2026-06-24)、`GVZ.GI`=24.91(2026-05-29),与 Excel **完全一致**。 |
| D3 | 实际利率走 FRED `DFII10` | 已验证:FRED `DFII10` 06-22=2.28、06-23=2.29,与 Excel 实际利率逐日一致。零口径变化,且与 `T10YIE` 同源、CI 可刷。 |
| D4 | Excel 增量退役 | 用户选择:先切已对账序列,EDB 宏观码下一轮再查。最小可验证闭环。 |
| D5 | EPU/地缘直接进评分层 | 用户选择。本轮自 Excel 既有 sheet(`经济政策不确定性`/`地缘政治风险`)读取。 |
| D6 | 新评分层均配关系检验卡 | 弱信号(EPU/地缘)进了评分,需把其历史有效性摆在明面,防止过度精确感。 |

## 4. 架构与数据流

```
本地(LAN, 有 Wind):
  scripts/refresh_wind_data.py   # 新增, tkf_wind 只读
     → data/market/wind_daily.csv      (date, gold_price, dollar_index, gvz)
  scripts/refresh_market_data.py # 扩展
     → data/market/fred_t10yie.csv     (通胀预期, 已有)
     → data/market/fred_dfii10.csv     (实际利率, 新增)
     → data/market/cftc_gold_cot.csv   (仓位, 已有)
  (上述 CSV 全部 git 提交)

CI(GitHub Actions, 无 Wind):
  build_site.py  →  读 CSV(+ 本轮仍读 Excel 的 ETF/储备/M2/EPU/地缘)  →  site/index.html
```

要点:
- **职责分离**:`refresh_wind_data.py` = LAN-only(依赖 tkf_wind);
  `refresh_market_data.py` = 公网(纯 stdlib,CI 亦可跑)。互不耦合。
- **一次性对账**:`refresh_wind_data.py` 切换前断言 Wind 末值与 Excel 末值在重叠日一致,
  通过后才信任 Wind(把 D2 的人工对账固化为自动检查)。
- `build_site.py` 改为优先读 CSV;GVZ 三年分位由内置 `percentile_rank()` 自算,
  不再依赖 Excel 预算列。
- **公共金价**:`wind_daily.csv` 的 `gold_price` 是所有因子关系检验(实际利率/美元/
  通胀/央行/仓位 vs 黄金收益)的统一金价源,按日期 join 到各因子序列;
  消除原先金价散落在多个 Excel sheet 第 2 列的隐患。
- **宽表容缺列**:`wind_daily.csv` 各列更新频率/末日可能不同(如 gold/DXY 到 6/24、GVZ 仅到 5/29)。读数按**每列各自取 latest**(沿用 `latest_with_value` 逐列回扫),不得因某列在近端为空而丢弃整行;每列独立计算 staleness。
- Excel 文件保留(留档、可回滚),仅 ETF/储备/M2/EPU/地缘 仍读取。

## 5. 指标层(5 → 8)与姿态

| 层 | 来源(本轮) | 状态逻辑(沿用 change-based) |
|----|------------|------------------------------|
| 实际利率 | FRED DFII10 | 近 1 月上行=压力 |
| 美元 | Wind USDX.FX | 近 1 月上行=压力 |
| 通胀预期 | FRED T10YIE | 近 1 月上行=支持 |
| 央行购金 | Excel(下轮转 Wind) | 中国&全球同向增持=支持 |
| 仓位与技术 | Wind GVZ + Excel ETF + CFTC | ETF/净多/动量综合 |
| **价格与趋势(新)** | Wind 金价 | 金价 > 200 日均线 且 3 月动量 > 0 = 支持;反之压力;混合中性。回撤作 context 展示 |
| **经济政策不确定性(新)** | Excel(下轮转 Wind) | 近 3 月上行=支持(月频,取 3 月变化平滑噪声) |
| **地缘政治风险(新)** | Excel(下轮转 Wind) | 近 3 月上行=支持(同上) |

**层注:**
- **价格与趋势**:信号由金价自身派生,关系检验必须用 **t 时点信号 vs 未来 1/3 个月黄金收益**(前瞻),**不得用同期收益**,否则构成自证循环。
- **EPU/地缘**:定位为**风险偏好/避险代理,非稳定因果驱动**。UI 文案须克制,关系检验卡尤其重要,避免"地缘上行 = 黄金必涨"的误读。

**姿态阈值(已确认)**:层数 5→8 后绝对分会漂,且未来某些层可能 missing/stale,故采用**归一化倾向**而非绝对阈值:

- `tendency = 净分 / 有效层数`
- `tendency ≥ +0.25` → 偏多;`tendency ≤ -0.25` → 承压;中间 → 中性
- 页面同时显示原始分与倾向,格式如 `score +2 / 8 · tendency +0.25`

暂不引入更复杂的权重体系(共线性问题已在 §7 留档)。

## 6. 数据质量 / staleness 模型

废除硬编码 `data_quality`,改为按"构建日 − 最新观测日"的滞后天数,按序列频率分级:

| 频率 | fresh | stale | 否则 |
|------|-------|-------|------|
| 日频(金价/美元/GVZ/利率) | ≤4 天 | ≤14 天 | very-stale |
| 周频(CFTC) | ≤10 天 | ≤21 天 | very-stale |
| 月频(储备/估值/EPU/地缘) | ≤45 天 | ≤75 天 | very-stale |

数据质量面板同时显示**实际滞后天数**与最新观测日。当前停在 5/29 的 GVZ(滞后约 27 天 > 14)将如实显示 **very-stale**。
缺失序列显式标 `missing`,不以旧值掩盖。

## 7. 已知局限(本轮不解决,留待后续)

1. **层级共线性**:实际利率/美元/通胀预期宏观高度共线(名义≈实际+breakeven),
   等权求和会把一次宏观脉冲计多次,系统性高估结论广度。新增的金价动量/EPU/地缘
   与三元组相关性低,本轮加层反而略增广度;但三元组本身的重复计数未解决。
2. **重叠窗口显著性**:`build_change_pairs` 的日度重叠样本高度自相关,
   有效样本数远小于名义 N,`corr_tone` 的"有效/失效"可信度被高估。
3. **固定相关阈值**:`corr_tone`(0.35=有效)不看样本量与显著性,最低 6 对即给结论。
4. **EDB 退役未完成**:ETF/储备/M2/EPU/地缘 仍读 Excel,下一轮转 Wind EDB。

## 8. 测试策略

- 沿用现有 `unittest`(`tests/test_build_site.py`),**测行为不测实现**。
- 更新:`read_dashboard_data()` 相关断言改为从新 CSV 源取数,数值不变(已对账),保持覆盖。
- 新增:
  - staleness 分级函数的单元测试(日/周/月 × fresh/stale/very-stale 边界)。
  - 三个新层(价格与趋势、EPU、地缘)出现在 layers 与 HTML、且各有关系检验卡。
  - `refresh_wind_data.py` 对账断言的测试(可用固定小样本桩数据)。
- 不为通过检查而弱化/删除任何既有断言(尤其"排除观点文字"的守护测试)。

## 9. 执行顺序(高层,详细计划见后续 plan)

1. `refresh_wind_data.py`:拉 gold/DXY/GVZ → CSV + 对账断言。
2. `refresh_market_data.py`:新增 DFII10 → CSV。
3. `build_site.py`:数据读取层改为优先 CSV;金价/利率/美元/GVZ 解耦 Excel。
4. staleness 模型:替换硬编码 `data_quality`。
5. 新增三层 + 关系检验卡;姿态阈值标定。
6. 更新与新增测试;本地构建核对 `site/index.html`。
