# Pantheon Research 看板借鉴备忘录

记录日期：2026-06-25

参考页面：https://pantheon-research.com/equity

本备忘录用于后续逐步建设美股、黄金、美债等大类资产跟踪框架。结论基于对 Pantheon Research 页面、前端模块和公开后端接口样例的观察；这些外部接口和页面后续可能变化，执行前需要重新核对。

## 一、值得借鉴的优点

### 1. 模块化研究台，而不是单页图表

Pantheon 的组织方式不是只做一个股票页，而是把 macro、equity、stock radar、commodity、fixed income、forex、crypto 等模块放在同一研究台里。

可借鉴点：

- 用统一入口承载多个资产类别。
- 每个资产类别保留自己的分析框架，不强行用同一套指标解释所有资产。
- 顶层页面只做总览和导航，细节放到资产页。

### 2. 数据质量显性化

页面和接口中大量使用 `MISSING`、`PROXY`、`stale`、`source-limited`、`usable`、`complete` 等状态。这比直接给一个漂亮分数更重要。

可借鉴点：

- 每个指标都应标注来源、更新时间、是否代理变量、是否缺失。
- 结论页要同时显示信号和数据可信度。
- 缺数据时明确显示 `MISSING`，不要用估计值掩盖。

### 3. 框架解释和结论同屏

美股模块有 `valuation / fundamentals / technical / narrative / analyst / events` 信号对齐；商品模块有 L1-L4 分层、宏观 regime、事件 watchlist、商品比价；固收模块有 action gate、portfolio stance、what changed、core data table、methodology。

可借鉴点：

- 不只展示结果，还展示框架结构。
- 让每个结论能追溯到具体层级和输入指标。
- 保留 methodology / caveat 区域，防止使用者误读。

### 4. 决策语言更适合跟踪

它反复使用 `what changed`、`why now`、`wrong if`、`next trigger`、`watch inputs` 这类语言。这比普通市场评论更适合形成跟踪闭环。

可借鉴点：

- 每次更新不只写“当前怎么看”，还要写“哪里变了”。
- 为每个判断设置失效条件。
- 把下一步需要观察的数据或事件列出来。

### 5. LLM 只作为解释层

Pantheon 的 equity 模块里，LLM overlay 会标注 source-backed 强弱；证据不足时宁可空白，且不影响确定性结果。

可借鉴点：

- 确定性指标负责打分和状态。
- AI 负责解释、摘要、对齐证据和指出缺口。
- 没有来源支撑时 fail-closed，不让 AI 猜测。

## 二、缺点与存疑

### 1. 具体结论不能直接信

观察到 commodity 模块存在 `db_snapshot_stale`、`ttl_soft_exceeded`，且商品观点混合了季度人工 editorial base 和日频自动数据。页面看起来实时，但部分观点并非实时更新。

风险：

- 使用者容易把季度观点误认为日频结论。
- 价格更新和观点更新的频率不同，可能产生错配。

### 2. 打分权重不完全透明

固收模块展示了一部分公式，但美股、商品、priority queue 的具体排序、权重和阈值不完全透明。

风险：

- 只能借鉴结构，不能照搬分数。
- 如果没有透明规则，长期复盘会困难。

### 3. 部分数据依赖付费或机构源

前端和接口里能看到 analyst feed required、provider blocked、requires institutional source、unavailable without paid source 等提示。

风险：

- 框架设计得过满，但个人可获得数据不足。
- 为了完整性补假数据，会降低整个系统可信度。

### 4. UI 容易制造过度精确感

`score`、`rating`、`priority`、`BUY/HOLD/AVOID` 等显示很强，但背后可能有 proxy、missing、static seed、source-limited。

风险：

- 看板越漂亮，越容易让弱信号显得像强结论。
- 必须把数据质量和 caveat 放在视觉上足够显眼的位置。

### 5. 单股框架复杂度很高

美股单股部分涉及 forward FCF、ROIC 5Y、macro beta、事件、财务、分析师数据、行业 regime、风险 cluster 等。部分字段还显示未接入或代理。

风险：

- 如果一开始就做单股框架，数据和维护成本会很高。
- 更适合先做指数和大类资产，再扩展到单股。

## 三、最小可借鉴框架

先不复制完整 Pantheon，而是抽出一个适合个人维护的最小框架。

### 统一资产页结构

每个资产页都使用同一套页面合同：

1. `Posture`：当前姿态，例如 bullish / neutral / defensive / risk-off。
2. `Score Stack`：各层信号，不只给总分。
3. `What Changed`：本期最重要变化。
4. `Wrong If`：当前判断的失效条件。
5. `Next Trigger`：下一步需要观察的数据或事件。
6. `Data Quality`：fresh / stale / proxy / missing。
7. `Methodology`：指标、权重、口径、限制。

### 美股 v1

目标先做指数/大盘框架，不做单股。

核心层级：

- 估值：CAPE、earnings yield spread、forward PE（可得时）。
- 盈利与增长：EPS revision、PMI、盈利预期（可得时）。
- 流动性与利率：10Y UST、real yield、Fed path。
- 风险偏好：VIX、HY OAS、credit spread。
- 市场宽度与动量：SPY/QQQ 均线、advance/decline、sector breadth。

最小输出：

- 美股姿态：risk-on / selective / defensive。
- 本期变化：估值、利率、信用、波动率谁贡献最大。
- 失效条件：利率、信用、波动率或盈利预期反向突破。

### 黄金 v1

当前仓库已有央行黄金储备数据，黄金应优先做。

核心层级：

- L1 宏观：10Y real yield、DXY、10Y breakeven。
- L2 官方部门：已有的中国央行黄金储备、全球央行黄金储备。
- L3 资金与仓位：黄金 ETF flows、COT positioning；拿不到则标 `MISSING`。
- L4 价格与技术：黄金价格、均线、动量、回撤。

最小输出：

- 黄金姿态：bullish / neutral / pressured。
- 本期变化：央行购金、实际利率、美元、价格动量的变化。
- 失效条件：实际利率上行、美元走强、央行购金放缓、价格跌破关键均线。

### 美债 v1

先做美国国债和久期配置框架，不做复杂组合优化。

核心层级：

- 增长：就业、ISM、GDPNow 或替代指标。
- 通胀：CPI、core CPI、breakeven。
- Fed 路径：Fed funds、2Y yield。
- 曲线：3M/10Y、2Y/10Y、10Y/30Y。
- 信用与金融条件：HY OAS、IG OAS、NFCI。
- 波动率：MOVE；拿不到可标 `MISSING` 或用替代代理。

最小输出：

- 美债姿态：add duration / neutral / reduce duration。
- 久期偏好：short / belly / long。
- 本期变化：增长、通胀、实际利率、信用谁在驱动。
- 失效条件：通胀再加速、就业转强、信用风险逆转、曲线重新定价。

## 四、建议执行顺序

### Phase 1：升级当前黄金页

在现有 `gold-reserves-dashboard` 上新增黄金框架页，不急着改成复杂应用。

任务：

- 保留现有央行黄金储备跟踪。
- 新增 `Posture / Score Stack / What Changed / Wrong If / Next Trigger / Data Quality` 区块。
- 对当前已有数据标 `fresh` 或 `source-backed`。
- 对暂未接入的 real yield、DXY、ETF、COT 明确标 `MISSING` 或 `planned`。

### Phase 2：接入黄金宏观指标

任务：

- 接入黄金价格。
- 接入 DXY。
- 接入 10Y real yield。
- 接入 10Y breakeven。
- 为每个指标记录来源、更新时间、状态。

### Phase 3：做美债 v1

任务：

- 新增美债页面。
- 建立增长、通胀、Fed、曲线、信用、波动率六层。
- 先输出姿态和解释，不做自动交易建议。

### Phase 4：做美股指数 v1

任务：

- 新增美股页面。
- 先跟踪 SPY/QQQ、估值、10Y、HY OAS、VIX、市场宽度。
- 不做单股筛选，避免一开始陷入财务数据和 analyst feed。

### Phase 5：复盘与扩展

任务：

- 每次更新记录上一期 posture 是否有效。
- 统计哪些指标最常导致判断修正。
- 只有当指数框架稳定后，再考虑单股框架。

## 五、设计原则

- 先做可验证的小闭环，不做大而全平台。
- 缺数据优先显示缺口，不用假数据补齐。
- 结论必须能追溯到输入指标。
- AI 只做解释和摘要，不直接改确定性信号。
- 每个页面都要显示数据时间戳和质量状态。
- 每个框架都要保留 `Wrong If`，防止观点变成口号。
