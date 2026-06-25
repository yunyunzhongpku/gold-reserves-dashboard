# 黄金看板:数据自动化 + 金价层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把黄金看板的核心日频数据从手工 Wind Excel 切到自动化(tkf_wind 直连 + FRED),引入真实 staleness,并新增金价趋势、EPU、地缘风险三个评分层。

**Architecture:** 沿用现有"本地抓取 → 提交 CSV → CI 渲染"模型。新增本地专用 `refresh_wind_data.py`(tkf_wind,LAN-only)拉 gold/DXY/GVZ;`refresh_market_data.py` 增补 FRED DFII10;`build_site.py` 改读 CSV、对每层按频率算 staleness、新增三层、姿态改归一化倾向。ETF/储备/M2/EPU/地缘本轮仍读 Excel(增量退役)。先以纯函数实现各新层与姿态(单测隔离),最后一个集成任务统一切换数据源并接线。

**Tech Stack:** Python 3.12(CI)/3.9(本机均可)、标准库(csv/urllib/datetime)、openpyxl、tkf_wind(`from tkf_wind import w`)、unittest、纯静态 SVG。

## Global Constraints

- **设计依据**:`docs/superpowers/specs/2026-06-25-gold-dashboard-automation-design.md`。每个任务的要求隐含包含本节。
- **数据源口径(已对账,逐字)**:金价 `SPTAUUSDOZ.IDC`、美元 `USDX.FX`、波动率 `GVZ.GI`、实际利率 FRED `DFII10`、通胀预期 FRED `T10YIE`、仓位 CFTC Disaggregated COT。
- **CI 约束**:`pages.yml` 在 GitHub runner 只跑 `build_site.py`,**不可访问 Wind LAN `10.92.26.150`**。所有 Wind 数据必须本地抓取并提交 CSV;`build_site.py` 与测试只读本地文件,不发起 Wind 请求。
- **测试策略(用户约定,优先于技能默认)**:`build_site.py` 业务逻辑(staleness、层状态、评分/姿态、关系检验)**测试先行(TDD)**;网络抓取脚本(`refresh_*`)**先实现再验证**(仅对可纯化的解析/对账逻辑写单测,网络调用本地跑一次核对)。
- **不弱化既有测试**:尤其 `test_html_is_data_driven_and_excludes_opinion_summary_text`(排除观点文字守护)必须继续通过。
- **姿态阈值(已确认)**:`tendency = 净分 / 有效层数`;`≥ +0.25` 偏多、`≤ -0.25` 承压、中间中性;页面同时显示原始分,格式 `score +2 / 8 · tendency +0.25`。
- **关系检验防自证**:金价趋势层的关系检验必须用 **t 时点信号 vs 未来收益**(前瞻),不得用同期收益。
- **UI 文案克制**:EPU/地缘定位为风险偏好/避险代理,非稳定因果驱动;关系检验卡需呈现真实有效性。
- **测试运行器**:项目用 stdlib `unittest`(无 pytest 依赖,CI 仅装 openpyxl;本机虽碰巧装了 pytest 8.4.2,但**不依赖它**)。所有测试命令统一形如 `PYTHONPYCACHEPREFIX=/private/tmp/gold-dashboard-pycache python3 -m unittest <dotted.path> -v`(前缀避免 `__pycache__` 落库;`tests/` 无需 `__init__.py`,dotted 调用已验证可用)。
- **提交规范**:分支 `feat/gold-dashboard-automation`;commit message 用英文 conventional 风格。**下方 commit snippet 为简洁省略 trailer,执行时每条 `git commit` 必须在 message 末尾追加** `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。每任务末尾提交一次。

## File Structure

| 文件 | 职责 | 变更 |
|------|------|------|
| `scripts/refresh_wind_data.py` | LAN-only:tkf_wind 拉 gold/DXY/GVZ → `wind_daily.csv`,含 Excel 对账 | 新建 |
| `scripts/refresh_market_data.py` | 公网:FRED(+DFII10)/CFTC → CSV | 修改 |
| `scripts/build_site.py` | 读 CSV(+Excel 残留)、staleness、8 层、关系检验、姿态、渲染 | 修改 |
| `data/market/wind_daily.csv` | gold/DXY/GVZ 日频宽表 | 新建(本地生成并提交) |
| `data/market/fred_dfii10.csv` | 实际利率日频 | 新建(本地生成并提交) |
| `tests/test_refresh_wind_data.py` | 纯函数单测(合表/对账) | 新建 |
| `tests/test_build_site.py` | 现有 + staleness/新层/姿态/集成 行为测试 | 修改 |

**执行顺序为 Task 1→8 线性,无前向依赖**:T4/T5/T6 以纯函数+隔离单测落地各新层与姿态;T7 才统一切换数据源、接线、应用 staleness、更新既有测试。

---

### Task 1: Wind 抓取脚本 `refresh_wind_data.py`

**Files:**
- Create: `scripts/refresh_wind_data.py`
- Test: `tests/test_refresh_wind_data.py`
- Generate+commit: `data/market/wind_daily.csv`

**Interfaces:**
- Produces:
  - `build_wind_daily_rows(series_by_key: dict[str, dict[str, float]]) -> list[dict]` — 纯函数,合并各 key 的 `{date: value}` 为按日期升序宽表;某日任一 key 有值即保留,缺失 key 写 `""`。
  - `compare_series(wind_series: dict[str,float], excel_series: dict[str,float|str], tol=1e-6) -> list[tuple]` — 纯函数,返回共同日期不一致项 `(date, wind, excel)`。
  - 产物 CSV 列序:`date, gold_price, dollar_index, gvz`。

- [ ] **Step 1: 写失败测试(纯函数)**

Create `tests/test_refresh_wind_data.py`:

```python
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import refresh_wind_data as rwd


class BuildWindDailyRowsTest(unittest.TestCase):
    def test_merges_keys_by_date_and_keeps_partial_rows(self):
        series = {
            "gold_price": {"2026-06-23": 4112.07, "2026-06-24": 3991.7},
            "dollar_index": {"2026-06-24": 101.5745},
            "gvz": {"2026-05-29": 24.91},
        }
        rows = rwd.build_wind_daily_rows(series)
        self.assertEqual([r["date"] for r in rows], ["2026-05-29", "2026-06-23", "2026-06-24"])
        self.assertEqual(rows[0], {"date": "2026-05-29", "gold_price": "", "dollar_index": "", "gvz": 24.91})
        self.assertEqual(rows[-1], {"date": "2026-06-24", "gold_price": 3991.7, "dollar_index": 101.5745, "gvz": ""})


class CompareSeriesTest(unittest.TestCase):
    def test_flags_only_real_mismatches_on_shared_dates(self):
        wind = {"2026-06-24": 3991.7, "2026-06-23": 4112.07}
        excel = {"2026-06-24": 3991.7, "2026-06-23": 4000.0, "2026-06-20": 4180.87}
        self.assertEqual(rwd.compare_series(wind, excel), [("2026-06-23", 4112.07, 4000.0)])

    def test_ignores_blank_excel_values(self):
        self.assertEqual(rwd.compare_series({"2026-06-24": 3991.7}, {"2026-06-24": ""}), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/gold-dashboard-pycache python3 -m unittest tests.test_refresh_wind_data -v`
Expected: FAIL(`ModuleNotFoundError` 或 `AttributeError`)。

- [ ] **Step 3: 实现脚本**

Create `scripts/refresh_wind_data.py`:

```python
from __future__ import annotations

import argparse
import csv
import glob
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET_DIR = ROOT / "data" / "market"
WIND_DAILY_FILE = MARKET_DIR / "wind_daily.csv"

WIND_SERIES = {
    "gold_price": "SPTAUUSDOZ.IDC",
    "dollar_index": "USDX.FX",
    "gvz": "GVZ.GI",
}
START_DATE = "2003-01-01"
FIELDNAMES = ["date", "gold_price", "dollar_index", "gvz"]

# (sheet, 0-based value column) 与源 Excel 对账
EXCEL_ANCHORS = {
    "gold_price": ("实际利率与金价", 2),
    "dollar_index": ("美元指数", 1),
    "gvz": ("隐含波动率", 1),
}


def build_wind_daily_rows(series_by_key):
    all_dates = sorted({d for series in series_by_key.values() for d in series})
    rows = []
    for d in all_dates:
        row = {"date": d}
        for key in FIELDNAMES[1:]:
            row[key] = series_by_key.get(key, {}).get(d, "")
        rows.append(row)
    return rows


def compare_series(wind_series, excel_series, tol=1e-6):
    mismatches = []
    for d, wv in wind_series.items():
        ev = excel_series.get(d)
        if ev in (None, ""):
            continue
        ev = float(ev)
        if abs(wv - ev) > tol * max(1.0, abs(ev)):
            mismatches.append((d, wv, ev))
    return sorted(mismatches)


def fetch_wind_series(codes, begin, end):
    from tkf_wind import w
    w.start()
    out = {}
    for key, code in codes.items():
        data = w.wsd(code, "close", begin, end, "")
        if getattr(data, "ErrorCode", -1) != 0:
            raise RuntimeError(f"Wind wsd failed for {code}: ErrorCode={data.ErrorCode}")
        series = {}
        for t, v in zip(data.Times, data.Data[0]):
            if v is None:
                continue
            day = t.date() if isinstance(t, datetime) else t
            series[day.strftime("%Y-%m-%d")] = float(v)
        out[key] = series
    return out


def load_excel_anchor_series():
    from openpyxl import load_workbook
    path = glob.glob(str(ROOT / "data" / "*.xlsx"))[0]
    workbook = load_workbook(path, read_only=True, data_only=True)
    out = {}
    try:
        for key, (sheet, col) in EXCEL_ANCHORS.items():
            series = {}
            for raw in workbook[sheet].iter_rows(values_only=True):
                head = raw[0] if raw else None
                if isinstance(head, (date, datetime)) and len(raw) > col and isinstance(raw[col], (int, float)):
                    day = head.date() if isinstance(head, datetime) else head
                    series[day.strftime("%Y-%m-%d")] = float(raw[col])
            out[key] = series
    finally:
        workbook.close()
    return out


def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def refresh(begin=START_DATE, end=None, reconcile=True):
    end = end or date.today().strftime("%Y-%m-%d")
    series = fetch_wind_series(WIND_SERIES, begin, end)
    if reconcile:
        excel = load_excel_anchor_series()
        failures = {
            key: compare_series(series[key], excel.get(key, {}))[:5]
            for key in WIND_SERIES
            if compare_series(series[key], excel.get(key, {}))
        }
        if failures:
            raise SystemExit(f"Reconciliation failed vs Excel: {failures}")
    rows = build_wind_daily_rows(series)
    write_csv(WIND_DAILY_FILE, rows, FIELDNAMES)
    return rows


def main():
    parser = argparse.ArgumentParser(description="Refresh Wind daily series (LAN-only).")
    parser.add_argument("--begin", default=START_DATE)
    parser.add_argument("--end", default=None)
    parser.add_argument("--no-reconcile", action="store_true")
    args = parser.parse_args()
    rows = refresh(begin=args.begin, end=args.end, reconcile=not args.no_reconcile)
    print(f"Wrote {WIND_DAILY_FILE} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/gold-dashboard-pycache python3 -m unittest tests.test_refresh_wind_data -v`
Expected: PASS(3 tests)。

- [ ] **Step 5: 本地实跑抓取并核对(implement→verify)**

Run: `python scripts/refresh_wind_data.py`
Expected: 打印 `Wrote .../wind_daily.csv (N rows)`(N 约 6000+);无 `Reconciliation failed`。
核对:`tail -2 data/market/wind_daily.csv` 末行 `gold_price=3991.7, dollar_index=101.5745`;GVZ 末值 `24.91`(2026-05-29)。对账失败先排查口径,**不要** `--no-reconcile` 绕过。

- [ ] **Step 6: 提交**

```bash
git add scripts/refresh_wind_data.py tests/test_refresh_wind_data.py data/market/wind_daily.csv
git commit -m "feat: add tkf_wind daily refresh with Excel reconciliation"
```

---

### Task 2: FRED 实际利率 `DFII10`

**Files:**
- Modify: `scripts/refresh_market_data.py`
- Generate+commit: `data/market/fred_dfii10.csv`

**Interfaces:**
- Produces: `read_fred_dfii10() -> list[dict]`,每项 `{"date": str, "real_rate": float}`;CSV 列 `date, real_rate`。

- [ ] **Step 1: 实现(脚本类,先实现)**

In `scripts/refresh_market_data.py`,URL 常量区加:

```python
FRED_DFII10_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"
```

文件常量区(`COT_FILE` 旁)加:

```python
DFII10_FILE = MARKET_DIR / "fred_dfii10.csv"
```

`read_fred_t10yie` 后新增:

```python
def read_fred_dfii10():
    text = fetch_bytes(FRED_DFII10_URL, user_agent=False).decode("utf-8")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        value = parse_number(row["DFII10"])
        if value is None:
            continue
        rows.append({"date": row["observation_date"], "real_rate": value})
    return rows
```

**防御性硬化(已核实:DFII10/T10YIE 当前用空串而非 `.` 标缺失,0 个 `.` 行——非修当前 bug,纯零风险前瞻)** —— 把 `parse_number` 的空判断扩到 `.`:

```python
def parse_number(value):
    value = value.strip()
    if value in ("", "."):
        return None
    return float(value)
```

`refresh(...)` 内 `write_csv(BREAKEVEN_FILE, ...)` 后加:

```python
    real_rate_rows = read_fred_dfii10()
    write_csv(DFII10_FILE, real_rate_rows, ["date", "real_rate"])
```

`refresh` 的 `return` 改为:

```python
    return breakeven_rows, real_rate_rows, cot_rows
```

`main()` 内解包与打印更新:

```python
    breakeven_rows, real_rate_rows, cot_rows = refresh(start_year=args.start_year, end_year=args.end_year)
    print(f"Wrote {BREAKEVEN_FILE} ({len(breakeven_rows)} rows)")
    print(f"Wrote {DFII10_FILE} ({len(real_rate_rows)} rows)")
    print(f"Wrote {COT_FILE} ({len(cot_rows)} rows)")
```

- [ ] **Step 2: 本地实跑并核对**

Run: `python scripts/refresh_market_data.py`
Expected: 三个 `Wrote ...`;`fred_dfii10.csv` 行数数千。
核对:`tail -3 data/market/fred_dfii10.csv` 与 Excel 实际利率重叠日一致(如 06-23 ≈ 2.29)。

- [ ] **Step 3: 提交**

```bash
git add scripts/refresh_market_data.py data/market/fred_dfii10.csv
git commit -m "feat: refresh FRED DFII10 real yield to CSV"
```

---

### Task 3: staleness 分级纯函数

**Files:**
- Modify: `scripts/build_site.py`(`QUALITY_LABELS` 扩展 + `FREQUENCY_THRESHOLDS` + `classify_staleness`)
- Test: `tests/test_build_site.py`(`StalenessTest`)

**Interfaces:**
- Produces: `classify_staleness(latest_date: str|None, today: date, frequency: str) -> tuple[str, int|None]`,`quality ∈ {fresh, stale, very-stale, missing}`。

- [ ] **Step 1: 写失败测试**

In `tests/test_build_site.py`,`if __name__` 前新增:

```python
from datetime import date as _date


class StalenessTest(unittest.TestCase):
    def test_daily_boundaries(self):
        today = _date(2026, 6, 25)
        self.assertEqual(build_site.classify_staleness("2026-06-23", today, "daily"), ("fresh", 2))
        self.assertEqual(build_site.classify_staleness("2026-06-15", today, "daily"), ("stale", 10))
        self.assertEqual(build_site.classify_staleness("2026-05-29", today, "daily"), ("very-stale", 27))

    def test_monthly_and_missing(self):
        today = _date(2026, 6, 25)
        self.assertEqual(build_site.classify_staleness("2026-05-31", today, "monthly")[0], "fresh")
        self.assertEqual(build_site.classify_staleness(None, today, "monthly"), ("missing", None))

    def test_future_dated_is_clamped_to_zero(self):
        # 月频常盖到月末(未来日期),滞后不应为负
        self.assertEqual(
            build_site.classify_staleness("2026-06-30", _date(2026, 6, 25), "monthly"), ("fresh", 0))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/gold-dashboard-pycache python3 -m unittest tests.test_build_site.StalenessTest -v`
Expected: FAIL(`AttributeError: ... has no attribute 'classify_staleness'`)。

- [ ] **Step 3: 实现**

`QUALITY_LABELS` 增补:

```python
QUALITY_LABELS = {
    "fresh": "fresh",
    "stale": "stale",
    "very-stale": "very-stale",
    "planned": "planned",
    "missing": "missing",
    "partial": "partial",
}
```

其后新增:

```python
FREQUENCY_THRESHOLDS = {
    "daily": (4, 14),
    "weekly": (10, 21),
    "monthly": (45, 75),
}


def classify_staleness(latest_date, today, frequency):
    if not latest_date:
        return "missing", None
    latest = datetime.strptime(latest_date, "%Y-%m-%d").date()
    lag = max(0, (today - latest).days)   # 月末前瞻日期(如储备盖到月末)不显示负滞后
    fresh_max, stale_max = FREQUENCY_THRESHOLDS[frequency]
    if lag <= fresh_max:
        return "fresh", lag
    if lag <= stale_max:
        return "stale", lag
    return "very-stale", lag
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/gold-dashboard-pycache python3 -m unittest tests.test_build_site.StalenessTest -v`
Expected: PASS(2 tests)。

- [ ] **Step 5: 提交**

```bash
git add scripts/build_site.py tests/test_build_site.py
git commit -m "feat: add frequency-aware staleness classifier"
```

---

### Task 4: 金价「价格与趋势」层 + 前瞻关系(纯函数,隔离单测)

**Files:**
- Modify: `scripts/build_site.py`(`trailing_return`、`attach_rolling_ma`、`make_price_trend_layer`、`build_trend_forward_pairs`、`make_trend_relationship`)
- Test: `tests/test_build_site.py`(`PriceTrendTest`)

**Interfaces:**
- Consumes: `gold_rows`(含 `date`/`_date`/`gold_price`)、`rolling_corr`、`corr_for_range`、`pct_return`、`phase_gold_return`、`weak_periods_from_rolling`、`corr_tone`、`fmt_*`。
- Produces:
  - `make_price_trend_layer(gold_rows) -> dict`(`id="price_trend"`,`frequency="daily"`,`chart_key="gold_price"`,state∈supportive/headwind/neutral)。
  - `make_trend_relationship(gold_rows) -> dict`,two-horizon 形状(`short_term`/`medium_term`/`phases`/`trend_rows`/`factor_key="ma200"`/`gold_key="gold_price"`),**前瞻**配对。

- [ ] **Step 1: 写失败测试**

In `tests/test_build_site.py` 新增:

```python
class PriceTrendTest(unittest.TestCase):
    def _rising(self, n=700, start=1000.0, step=5.0):  # 需 > 200(MA)+63(前瞻)+252(滚动) 才有非空相关
        from datetime import date, timedelta
        d0 = date(2025, 1, 1)
        return [{"date": (d0 + timedelta(days=i)).strftime("%Y-%m-%d"),
                 "_date": d0 + timedelta(days=i),
                 "gold_price": start + step * i} for i in range(n)]

    def test_uptrend_is_supportive(self):
        layer = build_site.make_price_trend_layer(self._rising())
        self.assertEqual(layer["id"], "price_trend")
        self.assertEqual(layer["frequency"], "daily")
        self.assertEqual(layer["state"], "supportive")

    def test_trend_relationship_uses_forward_returns(self):
        rel = build_site.make_trend_relationship(self._rising())
        self.assertIn("short_term", rel)
        self.assertIn("medium_term", rel)
        self.assertGreater(rel["short_term"]["latest_corr"], 0)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/gold-dashboard-pycache python3 -m unittest tests.test_build_site.PriceTrendTest -v`
Expected: FAIL(`make_price_trend_layer` 未定义)。

- [ ] **Step 3: 实现 helper 与层**

`pct_return` 之后加:

```python
def trailing_return(rows, key, lookback):
    valid = [row[key] for row in rows if row.get(key) is not None]
    if len(valid) <= lookback:
        return None
    return pct_return(valid[-1], valid[-1 - lookback])


def attach_rolling_ma(rows, key, window, out_key):
    prices = []
    output = []
    for row in rows:
        value = row.get(key)
        if value is None:
            output.append({**row, out_key: None})
            continue
        prices.append(value)
        ma = sum(prices[-window:]) / window if len(prices) >= window else None
        output.append({**row, out_key: ma})
    return output
```

`make_positioning_layer` 之后加:

```python
def make_price_trend_layer(gold_rows):
    latest = latest_with_value(gold_rows, "gold_price")
    prices = [row["gold_price"] for row in gold_rows if row.get("gold_price") is not None]
    ma200 = sum(prices[-200:]) / 200 if len(prices) >= 200 else None
    momentum_3m = trailing_return(gold_rows, "gold_price", 63)
    peak = max(prices[-252:]) if prices else None
    drawdown = pct_return(latest["gold_price"], peak) if peak else None
    above = ma200 is not None and latest["gold_price"] > ma200

    if above and (momentum_3m or 0) > 0:
        state = "supportive"
    elif ma200 is not None and not above and (momentum_3m or 0) < 0:
        state = "headwind"
    else:
        state = "neutral"

    gap = pct_return(latest["gold_price"], ma200) if ma200 else None
    return {
        "id": "price_trend",
        "name": "价格与趋势",
        "source": "Wind: SPTAUUSDOZ.IDC(派生 200日均线/动量)",
        "frequency": "daily",
        "data_quality": "fresh",
        "state": state,
        "latest": {"date": latest["date"], "value": latest["gold_price"], "gold_price": latest["gold_price"]},
        "change": momentum_3m,
        "change_since": None,
        "value_label": f"{fmt_number(latest['gold_price'])}",
        "change_label": fmt_pct(momentum_3m),
        "read": (
            f"金价 {fmt_number(latest['gold_price'])},"
            f"{'高于' if above else '低于'} 200日均线 {fmt_number(ma200)}({fmt_pct(gap)});"
            f"近3个月动量 {fmt_pct(momentum_3m)},距1年高点回撤 {fmt_pct(drawdown)}。"
        ),
        "wrong_if": "金价跌破 200日均线且 3个月动量转负,趋势支撑失效。",
        "next_trigger": "跟踪金价与 200日均线关系、动量与回撤是否同向恶化。",
        "chart_key": "gold_price",
        "chart_rows": gold_rows,
    }
```

- [ ] **Step 4: 实现前瞻关系**

`make_two_horizon_relationship` 之后加:

```python
def build_trend_forward_pairs(gold_rows, ma_window, horizon):
    points = [
        (row["date"], row["_date"], row["gold_price"])
        for row in gold_rows if row.get("gold_price") is not None
    ]
    pairs = []
    for i in range(ma_window - 1, len(points) - horizon):
        ma = sum(p[2] for p in points[i - ma_window + 1:i + 1]) / ma_window
        signal = points[i][2] / ma - 1                       # t 时点趋势缺口
        forward = points[i + horizon][2] / points[i][2] - 1   # 未来 horizon 收益
        pairs.append({"date": points[i][0], "_date": points[i][1],
                      "factor_change": signal, "gold_return": forward})
    return pairs


def make_trend_relationship(gold_rows):
    short_pairs = build_trend_forward_pairs(gold_rows, 200, 21)
    medium_pairs = build_trend_forward_pairs(gold_rows, 200, 63)
    short_rolling = rolling_corr(short_pairs, "factor_change", "gold_return", 126)
    medium_rolling = rolling_corr(medium_pairs, "factor_change", "gold_return", 252)
    short_latest = short_rolling[-1]["corr"] if short_rolling else None
    medium_latest = medium_rolling[-1]["corr"] if medium_rolling else None

    phase_defs = [
        ("2012-2018", date(2012, 1, 1), date(2018, 12, 31)),
        ("2019-2021", date(2019, 1, 1), date(2021, 12, 31)),
        ("2022-2024", date(2022, 1, 1), date(2024, 12, 31)),
        ("2025-至今", date(2025, 1, 1), None),
    ]
    phases = []
    for label, start, end in phase_defs:
        short_corr, short_obs = corr_for_range(short_pairs, "factor_change", "gold_return", start, end)
        medium_corr, medium_obs = corr_for_range(medium_pairs, "factor_change", "gold_return", start, end)
        phases.append({
            "label": label, "short_corr": short_corr, "medium_corr": medium_corr,
            "short_tone": corr_tone(short_corr, expected="positive"),
            "medium_tone": corr_tone(medium_corr, expected="positive"),
            "observations": min(short_obs, medium_obs),
            "gold_return": phase_gold_return(gold_rows, start, end),
        })

    return {
        "id": "price_trend", "name": "价格与趋势",
        "metric": "200日趋势缺口(t) vs 未来黄金收益(前瞻,防自证)", "expected": "positive",
        "short_term": {"label": "趋势→未来1月", "window": "200日缺口 vs 未来21日收益,126日滚动",
                       "latest_corr": short_latest, "tone": corr_tone(short_latest, expected="positive"),
                       "rolling_corr": short_rolling},
        "medium_term": {"label": "趋势→未来3月", "window": "200日缺口 vs 未来63日收益,252日滚动",
                        "latest_corr": medium_latest, "tone": corr_tone(medium_latest, expected="positive"),
                        "rolling_corr": medium_rolling},
        "latest_corr": medium_latest, "latest_tone": corr_tone(medium_latest, expected="positive"),
        "rolling_corr": medium_rolling,
        "trend_rows": attach_rolling_ma(
            [row for row in gold_rows if row.get("gold_price") is not None], "gold_price", 200, "ma200"),
        "factor_key": "ma200", "factor_label": "200日均线", "gold_key": "gold_price",
        "phases": phases, "weak_periods": weak_periods_from_rolling(medium_rolling),
        "read": (
            f"前瞻相关 短 {fmt_corr(short_latest)} / 中 {fmt_corr(medium_latest)};"
            f"正值表示趋势对未来收益有跟随效应。检验用未来收益,避免与同期金价自证。"
        ),
    }
```

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/gold-dashboard-pycache python3 -m unittest tests.test_build_site.PriceTrendTest -v`
Expected: PASS(2 tests)。

- [ ] **Step 6: 提交**

```bash
git add scripts/build_site.py tests/test_build_site.py
git commit -m "feat: add gold price/trend layer and forward-looking relationship"
```

---

### Task 5: EPU 与地缘风险层 + 月频关系(纯函数,隔离单测)

**Files:**
- Modify: `scripts/build_site.py`(`make_monthly_state_layer`、`make_epu_layer`、`make_gpr_layer`、`make_monthly_factor_relationship`)
- Test: `tests/test_build_site.py`(`MacroRiskLayerTest`)

**Interfaces:**
- Consumes: `gold_rows`、`change_over_observations`、`state_from_delta`、`attach_gold_price`、`pct_return`、`rolling_corr`、`corr_for_range`、`corr_tone`、`phase_gold_return`、`weak_periods_from_rolling`。
- Produces:
  - `make_epu_layer(epu_rows, gold_rows) -> dict`(`id="epu"`,`frequency="monthly"`,`chart_key="epu"`)。
  - `make_gpr_layer(gpr_rows, gold_rows) -> dict`(`id="gpr"`,`chart_key="gpr"`)。
  - `make_monthly_factor_relationship(rel_id, name, metric, rows, factor_key, gold_rows, expected="positive") -> dict`(central_bank 同形状,`start_month=None`)。

- [ ] **Step 1: 写失败测试**

In `tests/test_build_site.py` 新增:

```python
class MacroRiskLayerTest(unittest.TestCase):
    def _monthly(self, values):
        from datetime import date
        rows = []
        for i, v in enumerate(values):
            y, m = 2025 + (i // 12), (i % 12) + 1
            rows.append({"date": f"{y}-{m:02d}-28", "_date": date(y, m, 28), "epu": v, "gpr": v})
        return rows

    def test_rising_uncertainty_is_supportive(self):
        rows = self._monthly([100, 110, 130, 170, 240, 360])
        gold = [{"date": r["date"], "_date": r["_date"], "gold_price": 1000 + 10 * i}
                for i, r in enumerate(rows)]
        epu = build_site.make_epu_layer(rows, gold)
        self.assertEqual(epu["id"], "epu")
        self.assertEqual(epu["frequency"], "monthly")
        self.assertEqual(epu["state"], "supportive")

    def test_gpr_layer_shape(self):
        rows = self._monthly([100, 105, 110, 108, 112, 109])
        gold = [{"date": r["date"], "_date": r["_date"], "gold_price": 1000.0} for r in rows]
        layer = build_site.make_gpr_layer(rows, gold)
        self.assertEqual(layer["id"], "gpr")
        self.assertIn(layer["state"], {"supportive", "neutral", "headwind"})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/gold-dashboard-pycache python3 -m unittest tests.test_build_site.MacroRiskLayerTest -v`
Expected: FAIL(`make_epu_layer` 未定义)。

- [ ] **Step 3: 实现月频层工厂**

`make_price_trend_layer` 之后加:

```python
def make_monthly_state_layer(layer_id, name, source, rows, key, gold_rows, unit=""):
    latest = latest_with_value(rows, key)
    delta, since = change_over_observations(rows, key, 3)        # 近3个月变化
    threshold = abs(latest[key]) * 0.05 if latest else 0.0       # 5% 显著阈值,抗噪
    state = state_from_delta(delta, supportive_when="up", threshold=threshold)
    return {
        "id": layer_id, "name": name, "source": source, "frequency": "monthly",
        "data_quality": "fresh", "state": state,
        "latest": {"date": latest["date"], "value": latest[key]},
        "change": delta, "change_since": since,
        "value_label": f"{fmt_number(latest[key])}{unit}",
        "change_label": fmt_signed(delta, suffix=unit),
        "read": (
            f"{name} 最新 {fmt_number(latest[key])}{unit},近3个月 {fmt_signed(delta, suffix=unit)}。"
            f"作为风险偏好/避险代理观察,非稳定因果驱动,详见关系检验。"
        ),
        "wrong_if": f"{name} 回落且金价同向走弱,避险溢价消退。",
        "next_trigger": f"更新 {name},观察其与金价的滚动相关是否有效。",
        "chart_key": key, "chart_rows": rows,
    }


def make_epu_layer(epu_rows, gold_rows):
    return make_monthly_state_layer(
        "epu", "经济政策不确定性", "Excel: 经济政策不确定性(下轮转 Wind)", epu_rows, "epu", gold_rows)


def make_gpr_layer(gpr_rows, gold_rows):
    return make_monthly_state_layer(
        "gpr", "地缘政治风险", "Excel: 地缘政治风险(下轮转 Wind)", gpr_rows, "gpr", gold_rows)
```

- [ ] **Step 4: 实现通用月频关系**

`make_central_bank_relationship` 之前加:

```python
def make_monthly_factor_relationship(rel_id, name, metric, rows, factor_key, gold_rows, expected="positive"):
    paired = attach_gold_price([r for r in rows if r.get(factor_key) is not None], gold_rows)
    enriched = []
    for i in range(3, len(paired)):
        factor_3m = paired[i][factor_key] - paired[i - 3][factor_key]
        gold_3m = pct_return(paired[i]["gold_price"], paired[i - 3]["gold_price"])
        if gold_3m is None:
            continue
        enriched.append({
            "date": paired[i]["date"], "_date": paired[i]["_date"],
            "factor_3m_change": factor_3m, "gold_return_3m": gold_3m,
            factor_key: paired[i][factor_key], "gold_price": paired[i]["gold_price"],
        })
    rolling = rolling_corr(enriched, "factor_3m_change", "gold_return_3m", 24)
    latest_corr = rolling[-1]["corr"] if rolling else None

    phase_defs = [
        ("2018-2020", date(2018, 1, 1), date(2020, 12, 31)),
        ("2021-2022", date(2021, 1, 1), date(2022, 12, 31)),
        ("2023-2024H1", date(2023, 1, 1), date(2024, 6, 30)),
        ("2024H2-至今", date(2024, 7, 1), None),
    ]
    phases = []
    for label, start, end in phase_defs:
        corr, obs = corr_for_range(enriched, "factor_3m_change", "gold_return_3m", start, end)
        phases.append({"label": label, "corr": corr, "tone": corr_tone(corr, expected=expected),
                       "observations": obs, "gold_return": phase_gold_return(gold_rows, start, end)})

    latest_row = enriched[-1] if enriched else {}
    return {
        "id": rel_id, "name": name, "metric": metric, "expected": expected,
        "latest_corr": latest_corr, "latest_tone": corr_tone(latest_corr, expected=expected),
        "start_month": None, "weak_periods": weak_periods_from_rolling(rolling),
        "trend_rows": enriched, "factor_key": factor_key, "factor_label": name,
        "gold_key": "gold_price", "rolling_corr": rolling, "phases": phases,
        "latest": {"date": latest_row.get("date"),
                   "factor_change": latest_row.get("factor_3m_change"),
                   "gold_return": latest_row.get("gold_return_3m")},
        "read": (
            f"{name} 与金价的 3个月滚动相关 {fmt_corr(latest_corr)};"
            f"作为避险/风险偏好代理,关系常间歇有效,勿当稳定因果。"
        ),
    }
```

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/gold-dashboard-pycache python3 -m unittest tests.test_build_site.MacroRiskLayerTest -v`
Expected: PASS(2 tests)。

- [ ] **Step 6: 提交**

```bash
git add scripts/build_site.py tests/test_build_site.py
git commit -m "feat: add EPU and geopolitical-risk monthly layers and relationships"
```

---

### Task 6: 姿态改归一化倾向(纯函数)

**Files:**
- Modify: `scripts/build_site.py`(`score_layers` 返回 5 元组)
- Test: `tests/test_build_site.py`(`PostureTest`)

**Interfaces:**
- Produces: `score_layers(layers) -> (score:int, posture:str, posture_state:str, tendency:float, active:int)`。

- [ ] **Step 1: 写失败测试**

In `tests/test_build_site.py` 新增:

```python
class PostureTest(unittest.TestCase):
    def _layers(self, states):
        return [{"state": s} for s in states]

    def test_tendency_normalizes_by_active_layers(self):
        score, posture, state, tendency, active = build_site.score_layers(
            self._layers(["supportive"] * 3 + ["neutral"] * 5))
        self.assertEqual(active, 8)
        self.assertAlmostEqual(tendency, 3 / 8)
        self.assertEqual(posture, "偏多")

    def test_neutral_band(self):
        score, posture, state, tendency, active = build_site.score_layers(
            self._layers(["supportive", "headwind", "neutral"]))
        self.assertEqual(posture, "中性")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/gold-dashboard-pycache python3 -m unittest tests.test_build_site.PostureTest -v`
Expected: FAIL(`score_layers` 返回 3 元组,解包失败)。

- [ ] **Step 3: 实现**

把 `score_layers` 替换为:

```python
def score_layers(layers):
    weights = {"supportive": 1, "headwind": -1, "neutral": 0}
    scored = [weights[layer["state"]] for layer in layers if layer["state"] in weights]
    active = len(scored)
    score = sum(scored)
    tendency = score / active if active else 0.0
    if tendency >= 0.25:
        posture, posture_state = "偏多", "supportive"
    elif tendency <= -0.25:
        posture, posture_state = "承压", "headwind"
    else:
        posture, posture_state = "中性", "neutral"
    return score, posture, posture_state, tendency, active
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/gold-dashboard-pycache python3 -m unittest tests.test_build_site.PostureTest -v`
Expected: PASS(2 tests)。

- [ ] **Step 5: 提交**

```bash
git add scripts/build_site.py tests/test_build_site.py
git commit -m "feat: normalize posture to tendency over active layers"
```

---

### Task 7: 集成——切换数据源、接线 8 层、应用 staleness、渲染

**Files:**
- Modify: `scripts/build_site.py`(常量、`make_relationships` 接线、`make_positioning_layer` GVZ 分位、既有 5 层加 `frequency` 与新源、`make_relationship_section` 路由、`make_central_bank_relationship_card` 容错、`read_dashboard_data`、`build_html` hero/质量表)
- Modify: `tests/test_build_site.py`(更新既有断言到新数据源)

**Interfaces:**
- Consumes: Task 1/2 的 CSV、Task 3 `classify_staleness`、Task 4/5 的层与关系、Task 6 的 5 元组 `score_layers`。
- Produces: `read_dashboard_data()` 返回含 8 层(每层运行期 `data_quality`/`lag_days`)、`tendency`、`active_layers`;`make_relationships` 8 关系。

- [ ] **Step 1: 写失败测试(既有断言迁到新源)**

In `tests/test_build_site.py`,`test_reads_refreshed_workbook_into_driver_layers` 做如下改动(实际利率改自 FRED、美元改自 Wind,刷新时间会变 —— 改测行为,不钉易变的精确日期/值):

(a) **实际利率**三行(原 `date=="2026-06-24"`、`value≈2.23`、`data_quality=="fresh"`)替换为:

```python
        self.assertIn("DFII10", layers["real_rate"]["source"])
        self.assertIsNotNone(layers["real_rate"]["latest"]["value"])
        self.assertIn(layers["real_rate"]["data_quality"], {"fresh", "stale", "very-stale"})
```

(b) **美元**两行(原 `assertEqual(layers["dollar"]["latest"]["date"], "2026-06-24")` 与 `assertAlmostEqual(...value, 101.5745)`)替换为(Wind 最新日期随刷新变动,已观测到 06-25):

```python
        self.assertIn("USDX.FX", layers["dollar"]["source"])
        self.assertIsNotNone(layers["dollar"]["latest"]["value"])
        self.assertIn("lag_days", layers["dollar"])
        self.assertIn(layers["dollar"]["data_quality"], {"fresh", "stale", "very-stale"})
```

(c) 该测试末尾追加(新层存在):

```python
        self.assertTrue({"price_trend", "epu", "gpr"} <= set(layers))
```

(reserves 的 `date=="2026-06-30"`、`china≈2321.5452` 来自已提交 Excel,稳定,保留不动。)

In `test_relationships_explain_factor_usefulness_by_phase`,把 `set(relationships)` 期望集合加入 `"price_trend"`、`"epu"`、`"gpr"`。

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/gold-dashboard-pycache python3 -m unittest tests.test_build_site.GoldDashboardDataTest -v`
Expected: FAIL(旧 source 文案 / 缺 price_trend 等)。

- [ ] **Step 3: 加常量**

`COT_FILE` 之后加:

```python
WIND_DAILY_FILE = MARKET_DIR / "wind_daily.csv"
DFII10_FILE = MARKET_DIR / "fred_dfii10.csv"
SHEET_EPU = "经济政策不确定性"
SHEET_GPR = "地缘政治风险"
```

- [ ] **Step 4: 既有层加 `frequency` 与新源**

- `make_real_rate_layer`:`"source"` → `"FRED: DFII10 (10Y TIPS real yield)"`,返回 dict 加 `"frequency": "daily"`。
- `make_dollar_layer`:`"source"` → `"Wind: USDX.FX"`,加 `"frequency": "daily"`。
- `make_inflation_layer` 与 `make_missing_inflation_layer`:各加 `"frequency": "daily"`。
- `make_reserve_layer`:加 `"frequency": "monthly"`。
- `make_positioning_layer`:签名末尾加 `today=None`,函数首行 `today = today or date.today()`;加 `"frequency": "daily"`;把
  `gvz_percentile = latest_vol.get("gvz_3y_percentile") if latest_vol else None`
  替换为
  `gvz_percentile = percentile_rank(vol_rows, "gvz", 756) if latest_vol else None`;
  **新增 GVZ 组件 staleness**(防止过期 GVZ 被 ETF 的 fresh 掩盖——本层 `latest.date` 取自 ETF)。在算出 `latest_vol` 后加:

  ```python
      gvz_date = latest_vol["date"] if latest_vol else None
      gvz_quality, gvz_lag = classify_staleness(gvz_date, today, "daily")
  ```

  在该层返回 dict 的 `latest` 子典加 `"gvz_date": gvz_date, "gvz_quality": gvz_quality, "gvz_lag": gvz_lag`;并在该层 `read` 字符串末尾追加(放在 `cot_sentence` 之后):

  ```python
      f"GVZ 数据 {gvz_date or '—'}({QUALITY_LABELS.get(gvz_quality, gvz_quality)},滞后 {gvz_lag if gvz_lag is not None else '—'} 天)。"
  ```

- [ ] **Step 5: `make_relationships` 接线(统一 gold + 8 关系)**

把 `make_relationships` 替换为:

```python
def make_relationships(
    reserve_rows, real_rate_rows, dollar_rows, breakeven_rows,
    etf_rows, cot_rows, vol_rows, gold_rows, epu_rows, gpr_rows,
):
    return [
        make_central_bank_relationship(reserve_rows, gold_rows),
        make_real_rate_relationship(real_rate_rows),
        make_dollar_relationship(dollar_rows),
        make_inflation_relationship(breakeven_rows, gold_rows),
        make_positioning_relationship(etf_rows, cot_rows, vol_rows, gold_rows),
        make_trend_relationship(gold_rows),
        make_monthly_factor_relationship("epu", "经济政策不确定性",
            "EPU 3个月变化 vs 黄金3个月收益", epu_rows, "epu", gold_rows, expected="positive"),
        make_monthly_factor_relationship("gpr", "地缘政治风险",
            "GPR 3个月变化 vs 黄金3个月收益", gpr_rows, "gpr", gold_rows, expected="positive"),
    ]
```

> `make_inflation_relationship` 第二参数由 `real_rate_rows` 改为 `gold_rows`(其内部 `attach_gold_price` 不变)。

- [ ] **Step 6: 关系卡路由 + 月频卡容错**

In `make_relationship_section`,路由改为:

```python
        if relationship["id"] in {"central_bank_purchases", "epu", "gpr"}:
            cards.append(make_central_bank_relationship_card(relationship))
        elif relationship["id"] in {"real_rate", "dollar", "inflation_expectation", "price_trend"}:
            cards.append(make_horizon_relationship_card(relationship))
        elif relationship["id"] == "positioning_technical":
            cards.append(make_positioning_relationship_card(relationship))
```

In `make_central_bank_relationship_card`,metric-strip 改为容错 `start_month`、去掉"吨"后缀:

```python
      <div class="metric-strip">
        <div><span>起始显著推动</span><strong>{escape(relationship.get('start_month') or '—')}</strong></div>
        <div><span>当前滚动相关</span><strong>{fmt_corr(relationship['latest_corr'])}</strong></div>
        <div><span>最近 3 个月</span><strong>{fmt_signed(relationship['latest']['factor_change'])}</strong></div>
      </div>
```

- [ ] **Step 7: 重写 `read_dashboard_data`**

替换为:

```python
def read_dashboard_data():
    workbook = load_workbook()
    try:
        reserve_rows = read_sheet_rows(workbook, SHEET_RESERVES, {"china_reserves": 1, "global_reserves": 2})
        etf_rows = read_sheet_rows(workbook, SHEET_ETF, {"spdr_holdings": 1, "ishares_holdings": 2})
        valuation_rows = read_sheet_rows(workbook, SHEET_VALUATION, {"gold_price": 7, "gold_to_m2": 9, "valuation_percentile": 10})
        epu_rows = read_sheet_rows(workbook, SHEET_EPU, {"epu": 1})
        gpr_rows = read_sheet_rows(workbook, SHEET_GPR, {"gpr": 1})
    finally:
        workbook.close()

    wind_rows = read_csv_rows(WIND_DAILY_FILE, ["gold_price", "dollar_index", "gvz"])
    # 稀疏宽表 → 派生每指标的稠密子集,使行号 lookback == 有效观测 lookback
    gold_rows = [row for row in wind_rows if row.get("gold_price") is not None]
    dollar_rows = [row for row in wind_rows if row.get("dollar_index") is not None]
    vol_rows = [row for row in wind_rows if row.get("gvz") is not None]
    real_rate_rows = attach_gold_price(read_csv_rows(DFII10_FILE, ["real_rate"]), gold_rows)
    breakeven_rows = read_csv_rows(BREAKEVEN_FILE, ["breakeven_10y"])
    cot_rows = read_csv_rows(COT_FILE, [
        "open_interest", "managed_money_long", "managed_money_short",
        "managed_money_spread", "managed_money_net", "managed_money_net_to_oi",
    ])

    today = date.today()
    layers = [
        make_real_rate_layer(real_rate_rows),
        make_dollar_layer(dollar_rows),
        make_inflation_layer(breakeven_rows),
        make_reserve_layer(reserve_rows),
        make_positioning_layer(etf_rows, vol_rows, gold_rows, cot_rows, today),
        make_price_trend_layer(gold_rows),
        make_epu_layer(epu_rows, gold_rows),
        make_gpr_layer(gpr_rows, gold_rows),
    ]

    for layer in layers:
        quality, lag = classify_staleness(layer["latest"].get("date"), today, layer["frequency"])
        layer["data_quality"] = quality
        layer["lag_days"] = lag

    score, posture, posture_state, tendency, active_layers = score_layers(layers)

    return {
        "title": "黄金数据驱动跟踪",
        "source_file": str(DATA_FILE.relative_to(ROOT)),
        "score": score, "tendency": tendency, "active_layers": active_layers,
        "posture": posture, "posture_state": posture_state,
        "layers": layers,
        "valuation": make_valuation_snapshot(valuation_rows),
        "reserve_rows": layers[3]["chart_rows"],
        "relationships": make_relationships(
            reserve_rows, real_rate_rows, dollar_rows, breakeven_rows,
            etf_rows, cot_rows, vol_rows, gold_rows, epu_rows, gpr_rows),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
```

- [ ] **Step 8: hero 显示 tendency + 质量表加滞后列**

In `build_html`,hero `.score` 行改为:

```python
      <div class="score">score {dashboard['score']:+d} / {dashboard['active_layers']} · tendency {dashboard['tendency']:+.2f}</div>
```

`quality_rows` 改为含滞后,并在 `<thead>` 的 `状态` 与 `数据日期` 之间加 `<th>滞后</th>`:

```python
    quality_rows = "".join(
        f"""
        <tr>
          <td>{escape(layer['name'])}</td>
          <td>{escape(quality_badge(layer['data_quality']))}</td>
          <td>{'—' if layer.get('lag_days') is None else str(layer['lag_days']) + ' 天'}</td>
          <td>{escape(layer['latest']['date'] or '—')}</td>
          <td>{escape(layer['source'])}</td>
        </tr>
        """
        for layer in layers
    )
```

- [ ] **Step 9: 跑测试确认通过**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/gold-dashboard-pycache python3 -m unittest tests.test_build_site.GoldDashboardDataTest -v`
Expected: PASS。

- [ ] **Step 10: 提交**

```bash
git add scripts/build_site.py tests/test_build_site.py
git commit -m "feat: integrate CSV sources, 8 layers, staleness, normalized posture"
```

---

### Task 8: 全量校验与收尾

**Files:**
- Modify: `tests/test_build_site.py`(HTML 含新层/新卡/滞后断言)
- Verify: 本地构建 `site/index.html`

- [ ] **Step 1: 补 HTML 断言**

In `test_html_is_data_driven_and_excludes_opinion_summary_text` 末尾加:

```python
        self.assertIn("价格与趋势", html)
        self.assertIn("经济政策不确定性", html)
        self.assertIn("地缘政治风险", html)
        self.assertIn("滞后", html)
        self.assertIn("tendency", html)
        self.assertIn("GVZ 数据", html)      # 仓位层 GVZ 组件 staleness 被显式呈现(不依赖具体新鲜度;自动化后 GVZ 已刷新到最新)
```

In `test_relationships_explain_factor_usefulness_by_phase` 末尾(HTML 段)加:

```python
        self.assertIn("趋势→未来1月", html)
        self.assertIn("EPU 3个月变化 vs 黄金3个月收益", html)
```

- [ ] **Step 2: 跑全量测试**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/gold-dashboard-pycache python3 -m unittest discover -s tests -v`
Expected: 全部 PASS(含既有"排除观点文字"守护测试)。

- [ ] **Step 3: 本地构建并人工核对**

Run: `python scripts/build_site.py`
Expected: 打印 `Wrote .../site/index.html`。
人工核对浏览器打开 `site/index.html`:8 张层卡;波动率显示 `very-stale` 与滞后天数;hero 显示 `score .. / 8 · tendency ..`;EPU/地缘卡片文案克制、含关系检验。

- [ ] **Step 4: 提交**

```bash
git add tests/test_build_site.py site/index.html
git commit -m "test: assert new layers and staleness surface in HTML"
```

---

## Self-Review

**1. Spec coverage:**
- §2 数据管道(gold/DXY/GVZ→Wind、实际利率→FRED):Task 1、2、7 ✓
- §2 价格与趋势层:Task 4 ✓ | §2 EPU/地缘层:Task 5 ✓
- §2 真实 staleness:Task 3、7 ✓ | §2 每新层配关系检验卡:Task 4、5(+Task 7 路由)✓
- §4 公共金价(wind 统一源、按日期 join):Task 7 `attach_gold_price` + `gold_rows` 贯穿 ✓
- §4 宽表容缺列(每列各自 latest):既有 `read_csv_rows`+`latest_with_value` 逐列回扫;Task 1 测试覆盖偏列保留 ✓
- §5 趋势关系前瞻防自证:Task 4 `build_trend_forward_pairs` ✓
- §5 EPU/地缘 UI 克制 + 关系卡:Task 5 文案 + 月频卡 ✓
- §5 姿态归一化 ±0.25 + 显示原始分:Task 6、7 ✓
- §6 staleness 频率分级 + 滞后展示 + GVZ very-stale:Task 3、7 ✓
- §8 测试策略(行为测试、不弱化守护):各 Task TDD + Task 8 ✓

**2. Placeholder scan:** 无 TBD/TODO;每个代码步给完整实现与测试;无"add error handling"式占位。✓

**3. Type consistency:**
- `score_layers` 5 元组:Task 6 定义 → Task 7 解包一致 ✓
- `make_relationships` 10 参:Task 7 定义与调用一致;`make_inflation_relationship(breakeven_rows, gold_rows)` 二参 ✓
- layer dict 统一含 `id/name/source/frequency/state/latest/data_quality`;staleness 循环按 `frequency` 读取;新层(Task 4/5)与既有层(Task 7 补 `frequency`)齐备 ✓
- 关系 dict 形状:two-horizon(real_rate/dollar/inflation/price_trend)→ `make_horizon_relationship_card`;月频(central_bank/epu/gpr)→ `make_central_bank_relationship_card`(已容错 `start_month=None` 与无"吨"后缀)✓
- `factor_key="ma200"` 与 `attach_rolling_ma` 产出列名一致 ✓
- **无前向依赖**:T4/T5/T6 纯函数隔离单测;T7 才引用它们做集成 ✓

## 评审修订(2026-06-25,已核实后吸收)

1. **测试运行器** pytest→`unittest`(全命令改 `PYTHONPYCACHEPREFIX=… python3 -m unittest …`)。核实:本机 pytest 8.4.2 其实可用,但项目仅依赖 stdlib+openpyxl,不应依赖未声明工具;dotted unittest 调用已验证无需 `tests/__init__.py`。
2. **趋势测试样本** `n=260→700`。核实:260 条下 short/medium 滚动相关均为空 → `latest_corr=None` → 断言 TypeError;700 条 → 355/187 对。
3. **未来日期 staleness** `lag=max(0,…)` + future-date 测试。核实:储备最新 `2026-06-30`,今天 `06-25`,原实现显示 `-5 天`。
4. **稀疏宽表** 派生 `gold_rows/dollar_rows/vol_rows` 稠密子集,使行号 lookback==有效观测 lookback。
5. **GVZ 组件 staleness** 仓位层新增 `gvz_quality/gvz_lag` 并入卡片文案;核实:原 `latest.date` 取自 ETF,会用 fresh 掩盖 5/29 的 GVZ。
6. **FRED `.` 解析** `parse_number` 扩到 `.`。核实(部分反驳):DFII10/T10YIE 当前 0 个 `.` 行、252 个空串行,空串已被处理——此为零风险前瞻硬化,非修当前 bug。
7. **Co-Authored-By trailer** 在 Global Constraints 明确:snippet 省略、执行时每条 commit 必须追加。
