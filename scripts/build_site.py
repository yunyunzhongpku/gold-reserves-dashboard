from pathlib import Path
import csv
import math
from datetime import date, datetime
from html import escape

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "招商证券：黄金图表整理2607.xlsx"
MARKET_DIR = ROOT / "data" / "market"
BREAKEVEN_FILE = MARKET_DIR / "fred_t10yie.csv"
COT_FILE = MARKET_DIR / "cftc_gold_cot.csv"
OFFICIAL_RESERVES_MANUAL_FILE = MARKET_DIR / "official_reserves_manual.csv"
SITE_DIR = ROOT / "site"
OUTPUT_FILE = SITE_DIR / "index.html"

TEN_THOUSAND_TROY_OZ_TO_TONNES = 0.311034768
OFFICIAL_RESERVE_COLUMNS = [
    "date", "china_reserves_10k_oz", "global_reserves", "source"]

WIND_DAILY_FILE = MARKET_DIR / "wind_daily.csv"
DFII10_FILE = MARKET_DIR / "fred_dfii10.csv"
SHEET_REAL_RATE = "实际利率与金价"
SHEET_DOLLAR = "美元指数"
SHEET_VOLATILITY = "隐含波动率"
SHEET_ETF = "黄金ETF持有量&期现基差"
SHEET_RESERVES = "官方黄金储备"
SHEET_VALUATION = "中长期估值指标"
SHEET_EPU = "经济政策不确定性"
SHEET_GPR = "地缘政治风险"

STATE_LABELS = {
    "supportive": "支持",
    "neutral": "中性",
    "headwind": "压力",
    "planned": "待接入",
    "missing": "缺失",
}

QUALITY_LABELS = {
    "fresh": "fresh",
    "stale": "stale",
    "very-stale": "very-stale",
    "planned": "planned",
    "missing": "missing",
    "partial": "partial",
}

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


def format_date(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if value is None:
        return ""
    return str(value)[:10]


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def fmt_number(value, digits=2):
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def fmt_integer(value):
    if value is None:
        return "—"
    return f"{value:,.0f}"


def fmt_signed(value, digits=2, suffix=""):
    if value is None:
        return "—"
    return f"{value:+.{digits}f}{suffix}"


def fmt_signed_integer(value, suffix=""):
    if value is None:
        return "—"
    return f"{value:+,.0f}{suffix}"


def load_workbook():
    try:
        from openpyxl import load_workbook as open_workbook
    except ImportError as exc:
        raise RuntimeError("读取 Excel 数据需要 openpyxl，请在当前 Python 环境中安装后重试。") from exc

    if not DATA_FILE.exists():
        raise FileNotFoundError(f"未找到数据文件：{DATA_FILE}")

    return open_workbook(DATA_FILE, read_only=True, data_only=True)


def read_sheet_rows(workbook, sheet_name, columns, max_date=None):
    if sheet_name not in workbook.sheetnames:
        raise ValueError(f"未找到工作表：{sheet_name}")

    sheet = workbook[sheet_name]
    rows = []
    for raw in sheet.iter_rows(values_only=True):
        row_date = as_date(raw[0] if raw else None)
        if row_date is None:
            continue
        if max_date is not None and row_date > max_date:
            continue

        item = {"date": format_date(row_date), "_date": row_date}
        has_value = False
        for key, col_index in columns.items():
            value = raw[col_index] if len(raw) > col_index else None
            if is_number(value):
                item[key] = float(value)
                has_value = True
            else:
                item[key] = None

        if has_value:
            rows.append(item)

    rows.sort(key=lambda row: row["_date"])
    return rows


def read_csv_rows(path, numeric_columns):
    if not path.exists():
        return []

    rows = []
    with path.open(encoding="utf-8", newline="") as file:
        for raw in csv.DictReader(file):
            row_date = as_date(datetime.strptime(raw["date"], "%Y-%m-%d"))
            item = {"date": format_date(row_date), "_date": row_date}
            has_value = False
            for key in numeric_columns:
                value = raw.get(key, "")
                if value == "":
                    item[key] = None
                    continue
                item[key] = float(value)
                has_value = True
            for key, value in raw.items():
                if key not in item and key not in numeric_columns:
                    item[key] = value
            if has_value:
                rows.append(item)

    rows.sort(key=lambda row: row["_date"])
    return rows


def month_index(value):
    return value.year * 12 + value.month


def optional_csv_float(value):
    return None if value in (None, "") else float(value)


def read_official_reserve_rows(path, max_date=None):
    if not path.exists():
        return []

    rows = []
    seen_months = set()
    previous_date = None
    previous_month = None
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != OFFICIAL_RESERVE_COLUMNS:
            expected = ",".join(OFFICIAL_RESERVE_COLUMNS)
            actual = ",".join(reader.fieldnames or [])
            raise ValueError(
                f"官方储备 CSV 表头必须严格为 {expected}；实际为 {actual}")

        for raw in reader:
            row_date = datetime.strptime(raw["date"], "%Y-%m-%d").date()
            current_month = month_index(row_date)
            if current_month in seen_months:
                raise ValueError(f"官方储备月份重复：{raw['date']}")
            if previous_date is not None and row_date <= previous_date:
                raise ValueError("官方储备日期必须严格升序")
            if previous_month is not None and current_month != previous_month + 1:
                raise ValueError("官方储备月份必须连续")

            ounces = float(raw["china_reserves_10k_oz"])
            if not math.isfinite(ounces) or ounces <= 0:
                raise ValueError("中国官方黄金储备必须为有限正数")
            source = (raw.get("source") or "").strip()
            if not source:
                raise ValueError("中国官方黄金储备来源不能为空")
            global_reserves = optional_csv_float(raw.get("global_reserves"))
            if global_reserves is not None and (not math.isfinite(global_reserves) or global_reserves <= 0):
                raise ValueError("全球官方黄金储备必须为有限正数")
            rows.append({
                "date": format_date(row_date),
                "_date": row_date,
                "china_reserves_10k_oz": ounces,
                "china_reserves": ounces * TEN_THOUSAND_TROY_OZ_TO_TONNES,
                "global_reserves": global_reserves,
                "china_source": source,
                "global_source": source if global_reserves is not None else None,
            })
            seen_months.add(current_month)
            previous_date = row_date
            previous_month = current_month

    return [row for row in rows if max_date is None or row["_date"] <= max_date]


def merge_reserve_rows(base_rows, override_rows):
    merged = {}
    for row in base_rows:
        item = {
            **row,
            "china_reserves_10k_oz": row.get("china_reserves_10k_oz"),
            "china_source": row.get("china_source") or "Excel: 官方黄金储备",
            "global_source": row.get("global_source") or "Excel: 官方黄金储备",
        }
        merged[(row["_date"].year, row["_date"].month)] = item

    for row in override_rows:
        key = (row["_date"].year, row["_date"].month)
        item = merged.get(key, {
            "date": row["date"], "_date": row["_date"],
            "china_reserves": None, "global_reserves": None,
            "china_source": None, "global_source": None,
        })
        item["china_reserves_10k_oz"] = row["china_reserves_10k_oz"]
        item["china_reserves"] = row["china_reserves"]
        item["china_source"] = row["china_source"]
        if row["global_reserves"] is not None:
            item["global_reserves"] = row["global_reserves"]
            item["global_source"] = row["global_source"]
        merged[key] = item

    return sorted(merged.values(), key=lambda row: row["_date"])


def latest_with_value(rows, key):
    for row in reversed(rows):
        if row.get(key) is not None:
            return row
    return None


def merge_override_rows(base_rows, override_rows):
    if not override_rows:
        return base_rows

    first_override_date = override_rows[0]["_date"]
    rows = [row for row in base_rows if row["_date"] < first_override_date]
    rows.extend(override_rows)
    rows.sort(key=lambda row: row["_date"])
    return rows


def optional_change(current, previous, key):
    if current is None or previous is None:
        return None
    if current.get(key) is None or previous.get(key) is None:
        return None
    return current[key] - previous[key]


def change_over_observations(rows, key, lookback):
    valid = [row for row in rows if row.get(key) is not None]
    if len(valid) < 2:
        return None, None

    latest = valid[-1]
    prior_index = max(0, len(valid) - lookback - 1)
    prior = valid[prior_index]
    return latest[key] - prior[key], prior["date"]


def state_from_delta(delta, supportive_when="down", threshold=0.0):
    if delta is None or abs(delta) <= threshold:
        return "neutral"
    if supportive_when == "down":
        return "supportive" if delta < 0 else "headwind"
    return "supportive" if delta > 0 else "headwind"


def percentile_rank(rows, key, lookback=None):
    valid = [row for row in rows if row.get(key) is not None]
    if not valid:
        return None
    if lookback:
        valid = valid[-lookback:]
    if len(valid) < 2:
        return None

    latest_value = valid[-1][key]
    below_or_equal = sum(1 for row in valid if row[key] <= latest_value)
    return below_or_equal / len(valid)


def pct_return(current, previous):
    if current is None or previous in (None, 0):
        return None
    return current / previous - 1


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


def correlation(values):
    pairs = [(x, y) for x, y in values if x is not None and y is not None]
    if len(pairs) < 6:
        return None

    xs = [x for x, _ in pairs]
    ys = [y for _, y in pairs]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x * var_y) ** 0.5


def fmt_corr(value):
    if value is None:
        return "—"
    return f"{value:+.2f}"


def fmt_pct(value):
    if value is None:
        return "—"
    return f"{value * 100:+.1f}%"


def fmt_axis_value(value):
    if value is None:
        return "—"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 100:
        return f"{value:.0f}"
    if abs(value) >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def corr_tone(value, expected="negative"):
    if value is None:
        return "样本不足"
    if expected == "absolute":
        if abs(value) >= 0.35:
            return "有效"
        if abs(value) <= 0.1:
            return "失效"
        return "偏弱"
    if expected == "negative":
        if value <= -0.35:
            return "有效"
        if value >= 0.1:
            return "失效"
        return "偏弱"
    if value >= 0.35:
        return "有效"
    if value <= -0.1:
        return "失效"
    return "偏弱"


def corr_class(value, expected="negative"):
    tone = corr_tone(value, expected=expected)
    if tone == "有效":
        return "supportive"
    if tone == "失效":
        return "headwind"
    return "neutral"


def phase_label(start, end):
    if end is None:
        return f"{start.year}-至今"
    if start.year == end.year:
        return str(start.year)
    return f"{start.year}-{end.year}"


def filter_date_range(rows, start, end):
    return [
        row for row in rows
        if row.get("_date") is not None and row["_date"] >= start and (end is None or row["_date"] <= end)
    ]


def build_change_pairs(rows, factor_key, gold_key, lookback):
    pairs = []
    for i in range(lookback, len(rows)):
        current = rows[i]
        previous = rows[i - lookback]
        factor_current = current.get(factor_key)
        factor_previous = previous.get(factor_key)
        gold_current = current.get(gold_key)
        gold_previous = previous.get(gold_key)
        if factor_current is None or factor_previous is None or gold_current is None or gold_previous is None:
            continue
        pairs.append({
            "date": current["date"],
            "_date": current["_date"],
            "factor_change": factor_current - factor_previous,
            "gold_return": pct_return(gold_current, gold_previous),
        })
    return pairs


def rolling_corr(rows, x_key, y_key, window):
    output = []
    for i in range(window - 1, len(rows)):
        sample = rows[i - window + 1:i + 1]
        corr = correlation((row.get(x_key), row.get(y_key)) for row in sample)
        output.append({
            "date": rows[i]["date"],
            "_date": rows[i]["_date"],
            "corr": corr,
        })
    return [row for row in output if row["corr"] is not None]


def corr_for_range(rows, x_key, y_key, start, end):
    sample = filter_date_range(rows, start, end)
    return correlation((row.get(x_key), row.get(y_key)) for row in sample), len(sample)


def gold_price_asof(gold_rows, target_date):
    price = None
    for row in gold_rows:
        if row["_date"] > target_date:
            break
        if row.get("gold_price") is not None:
            price = row["gold_price"]
    return price


def build_reserve_relationship_rows(reserve_rows, gold_rows):
    rows = []
    changes = []
    for i, row in enumerate(reserve_rows):
        if i == 0:
            china_change = None
            global_change = None
        else:
            china_change = optional_change(row, reserve_rows[i - 1], "china_reserves")
            global_change = optional_change(row, reserve_rows[i - 1], "global_reserves")
        changes.append((china_change, global_change))

        if i < 3:
            continue
        china_changes = [change[0] for change in changes[i - 2:i + 1] if change[0] is not None]
        global_changes = [change[1] for change in changes[i - 2:i + 1] if change[1] is not None]
        china_3m = sum(china_changes) if china_changes else None
        global_3m = sum(global_changes) if global_changes else None
        gold_current = gold_price_asof(gold_rows, row["_date"])
        gold_previous = gold_price_asof(gold_rows, reserve_rows[i - 3]["_date"])
        gold_return_3m = pct_return(gold_current, gold_previous)
        if china_3m is None or gold_return_3m is None:
            continue
        rows.append({
            "date": row["date"],
            "_date": row["_date"],
            "china_3m_change": china_3m,
            "global_3m_change": global_3m,
            "gold_price": gold_current,
            "gold_return_3m": gold_return_3m,
        })
    return rows


def latest_significant_cycle_start(rows, key, threshold, min_months=3):
    runs = []
    run_start = None
    run_rows = []
    for row in rows:
        if row.get(key) is not None and row[key] >= threshold:
            if run_start is None:
                run_start = row
                run_rows = []
            run_rows.append(row)
        elif run_start is not None:
            if len(run_rows) >= min_months:
                runs.append(run_rows)
            run_start = None
            run_rows = []
    if run_start is not None and len(run_rows) >= min_months:
        runs.append(run_rows)
    if not runs:
        return None
    return runs[-1][0]["date"]


def weak_periods_from_rolling(rows, threshold=0.1, max_periods=3):
    periods = []
    active = []
    for row in rows:
        corr = row.get("corr")
        weak = corr is not None and abs(corr) <= threshold
        if weak:
            active.append(row)
        elif active:
            if len(active) >= 3:
                periods.append((active[0]["date"], active[-1]["date"]))
            active = []
    if len(active) >= 3:
        periods.append((active[0]["date"], active[-1]["date"]))
    return periods[-max_periods:]


def phase_gold_return(gold_rows, start, end):
    sample = filter_date_range([row for row in gold_rows if row.get("gold_price") is not None], start, end)
    if len(sample) < 2:
        return None
    return pct_return(sample[-1]["gold_price"], sample[0]["gold_price"])


def attach_gold_price(rows, gold_rows):
    attached = []
    for row in rows:
        gold_price = row.get("gold_price")
        if gold_price is None:
            gold_price = gold_price_asof(gold_rows, row["_date"])
        if gold_price is None:
            continue
        attached.append({**row, "gold_price": gold_price})
    return attached


def quality_badge(quality):
    return QUALITY_LABELS.get(quality, quality)


def state_badge(state):
    return STATE_LABELS.get(state, state)


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


def make_real_rate_layer(rows):
    latest = latest_with_value(rows, "real_rate")
    delta, since = change_over_observations(rows, "real_rate", 21)
    state = state_from_delta(delta, supportive_when="down", threshold=0.05)

    return {
        "id": "real_rate",
        "name": "实际利率",
        "source": "FRED: DFII10 (10Y TIPS real yield)",
        "frequency": "daily",
        "data_quality": "fresh",
        "state": state,
        "latest": {
            "date": latest["date"],
            "value": latest["real_rate"],
            "gold_price": latest.get("gold_price"),
        },
        "change": delta,
        "change_since": since,
        "value_label": f"{fmt_number(latest['real_rate'])}%",
        "change_label": fmt_signed(delta, suffix="pct"),
        "read": (
            f"10Y 实际利率最新 {fmt_number(latest['real_rate'])}%，"
            f"近约 1 个月变化 {fmt_signed(delta, suffix='pct')}。"
        ),
        "wrong_if": "实际利率继续上行并维持高位，黄金机会成本压力扩大。",
        "next_trigger": "更新 10Y TIPS real yield，并观察与金价的滚动相关是否重新增强。",
        "chart_key": "real_rate",
        "chart_rows": rows,
    }


def make_dollar_layer(rows):
    latest = latest_with_value(rows, "dollar_index")
    delta, since = change_over_observations(rows, "dollar_index", 21)
    state = state_from_delta(delta, supportive_when="down", threshold=0.5)

    return {
        "id": "dollar",
        "name": "美元",
        "source": "Wind: USDX.FX",
        "frequency": "daily",
        "data_quality": "fresh",
        "state": state,
        "latest": {
            "date": latest["date"],
            "value": latest["dollar_index"],
            "gold_price": latest.get("gold_price"),
        },
        "change": delta,
        "change_since": since,
        "value_label": fmt_number(latest["dollar_index"]),
        "change_label": fmt_signed(delta),
        "read": (
            f"美元指数最新 {fmt_number(latest['dollar_index'])}，"
            f"近约 1 个月变化 {fmt_signed(delta)}。"
        ),
        "wrong_if": "美元重新走强，且实际利率同步上行，美元计价黄金承压。",
        "next_trigger": "更新美元指数，并与实际利率层一起判断是否形成同向压力。",
        "chart_key": "dollar_index",
        "chart_rows": rows,
    }


def make_missing_inflation_layer():
    return {
        "id": "inflation_expectation",
        "name": "通胀预期",
        "source": "FRED: T10YIE (10-Year Breakeven Inflation Rate)",
        "frequency": "daily",
        "data_quality": "missing",
        "state": "missing",
        "latest": {"date": None, "value": None},
        "change": None,
        "change_since": None,
        "value_label": "缺数据",
        "change_label": "—",
        "read": "当前未找到本地 FRED T10YIE 数据，暂不对通胀预期层打分。",
        "wrong_if": "通胀预期快速回落而实际利率上行，黄金的通胀保护逻辑会减弱。",
        "next_trigger": "刷新 FRED T10YIE 后，观察通胀预期与实际利率的方向差。",
        "chart_key": None,
        "chart_rows": [],
    }


def make_inflation_layer(rows):
    latest = latest_with_value(rows, "breakeven_10y")
    if latest is None:
        return make_missing_inflation_layer()

    delta, since = change_over_observations(rows, "breakeven_10y", 21)
    state = state_from_delta(delta, supportive_when="up", threshold=0.05)

    return {
        "id": "inflation_expectation",
        "name": "通胀预期",
        "source": "FRED: T10YIE (10-Year Breakeven Inflation Rate)",
        "frequency": "daily",
        "data_quality": "fresh",
        "state": state,
        "latest": {
            "date": latest["date"],
            "value": latest["breakeven_10y"],
        },
        "change": delta,
        "change_since": since,
        "value_label": f"{fmt_number(latest['breakeven_10y'])}%",
        "change_label": fmt_signed(delta, suffix="pct"),
        "read": (
            f"10Y breakeven 最新 {fmt_number(latest['breakeven_10y'])}%，"
            f"近约 1 个月变化 {fmt_signed(delta, suffix='pct')}。"
        ),
        "wrong_if": "通胀预期快速回落而实际利率上行，黄金的通胀保护逻辑会减弱。",
        "next_trigger": "观察 10Y breakeven 与实际利率是否同向上行，区分通胀补偿和真实利率压力。",
        "chart_key": "breakeven_10y",
        "chart_rows": rows,
    }


def make_reserve_layer(rows):
    latest = rows[-1]
    previous = rows[-2] if len(rows) >= 2 else None
    china_change = optional_change(latest, previous, "china_reserves")
    global_change = optional_change(latest, previous, "global_reserves")

    state = state_from_delta(china_change, supportive_when="up", threshold=0.0)

    chart_rows = []
    for i, row in enumerate(rows):
        if i == 0:
            china_mom_change = None
            global_mom_change = None
        else:
            china_mom_change = optional_change(row, rows[i - 1], "china_reserves")
            global_mom_change = optional_change(row, rows[i - 1], "global_reserves")
        chart_rows.append({
            **row,
            "china_mom_change": china_mom_change,
            "global_mom_change": global_mom_change,
        })

    if latest.get("global_reserves") is None or global_change is None:
        global_read = "全球官方黄金储备本期未更新。"
    else:
        global_read = (
            f"全球官方黄金储备 {fmt_number(latest['global_reserves'])} 吨，"
            f"环比 {fmt_signed(global_change, suffix=' 吨')}。"
        )

    return {
        "id": "official_reserves",
        "name": "央行购金",
        "source": latest.get("china_source") or "Excel: 官方黄金储备",
        "frequency": "monthly",
        "data_quality": "fresh",
        "state": state,
        "latest": {
            "date": latest["date"],
            "value": latest["china_reserves"],
            "china_reserves": latest["china_reserves"],
            "global_reserves": latest["global_reserves"],
            "china_change": china_change,
            "global_change": global_change,
            "china_source": latest.get("china_source") or "Excel: 官方黄金储备",
            "global_source": (
                latest.get("global_source")
                if latest.get("global_reserves") is not None else None),
        },
        "change": china_change,
        "change_since": previous["date"] if previous else None,
        "value_label": f"中国 {fmt_number(latest['china_reserves'])} 吨",
        "change_label": fmt_signed(china_change, suffix=" 吨"),
        "read": (
            f"中国官方黄金储备 {fmt_number(latest['china_reserves'])} 吨，"
            f"环比 {fmt_signed(china_change, suffix=' 吨')}；"
            f"{global_read}"
        ),
        "wrong_if": "中国与全球官方储备同时转为持续净卖出，慢变量支撑减弱。",
        "next_trigger": "等待下一期官方储备数据，重点看中国与全球是否同向增加。",
        "chart_key": "china_mom_change",
        "chart_type": "bar",
        "chart_label": "央行购金环比柱状图",
        "chart_rows": chart_rows,
    }


def make_positioning_layer(etf_rows, vol_rows, gold_rows, cot_rows, today=None):
    today = today or date.today()
    for row in etf_rows:
        if row.get("spdr_holdings") is not None and row.get("ishares_holdings") is not None:
            row["etf_total"] = row["spdr_holdings"] + row["ishares_holdings"]
        else:
            row["etf_total"] = None

    latest_etf = latest_with_value(etf_rows, "etf_total")
    etf_delta, etf_since = change_over_observations(etf_rows, "etf_total", 21)
    latest_vol = latest_with_value(vol_rows, "gvz")
    latest_gold = latest_with_value(gold_rows, "gold_price")
    gold_delta, _ = change_over_observations(gold_rows, "gold_price", 60)

    gvz_date = latest_vol["date"] if latest_vol else None
    gvz_quality, gvz_lag = classify_staleness(gvz_date, today, "daily")

    etf_state = state_from_delta(etf_delta, supportive_when="up", threshold=3.0)
    gold_state = state_from_delta(gold_delta, supportive_when="up", threshold=0.0)

    latest_cot = latest_with_value(cot_rows, "managed_money_net")
    cot_delta, cot_since = change_over_observations(cot_rows, "managed_money_net", 4)
    cot_percentile = percentile_rank(cot_rows, "managed_money_net", 156)
    if latest_cot is None:
        cot_state = "neutral"
        cot_sentence = "CFTC Managed Money 数据缺失，仓位拥挤度暂不计入。"
    else:
        if cot_percentile is not None and cot_percentile > 0.9:
            cot_state = "headwind"
        elif cot_delta is not None and cot_delta < -15000:
            cot_state = "headwind"
        elif cot_delta is not None and cot_delta > 15000:
            cot_state = "supportive"
        else:
            cot_state = "neutral"
        cot_sentence = (
            f"Managed Money 黄金净多 {fmt_integer(latest_cot['managed_money_net'])} 张，"
            f"近 4 周变化 {fmt_signed_integer(cot_delta, suffix=' 张')}，"
            f"三年分位 {fmt_number((cot_percentile or 0) * 100)}%。"
        )

    state_score = sum({"supportive": 1, "neutral": 0, "headwind": -1}[item] for item in [etf_state, gold_state, cot_state])
    if state_score >= 2:
        state = "supportive"
    elif state_score <= -2:
        state = "headwind"
    else:
        state = "neutral"

    gvz_percentile = percentile_rank(vol_rows, "gvz", 756) if latest_vol else None
    return {
        "id": "positioning_technical",
        "name": "仓位与技术",
        "source": "Wind: ETF(SPDR/iShares)、GVZ；CFTC: Disaggregated COT Gold",
        "frequency": "daily",
        "data_quality": "fresh" if latest_cot else "partial",
        "state": state,
        "latest": {
            "date": latest_etf["date"],
            "value": latest_etf["etf_total"],
            "etf_total": latest_etf["etf_total"],
            "spdr_holdings": latest_etf["spdr_holdings"],
            "ishares_holdings": latest_etf["ishares_holdings"],
            "etf_change": etf_delta,
            "gvz": latest_vol["gvz"] if latest_vol else None,
            "gvz_percentile": gvz_percentile,
            "gvz_date": gvz_date,
            "gvz_quality": gvz_quality,
            "gvz_lag": gvz_lag,
            "gold_price": latest_gold["gold_price"] if latest_gold else None,
            "gold_change_60": gold_delta,
            "cot_date": latest_cot["date"] if latest_cot else None,
            "managed_money_net": latest_cot["managed_money_net"] if latest_cot else None,
            "managed_money_long": latest_cot["managed_money_long"] if latest_cot else None,
            "managed_money_short": latest_cot["managed_money_short"] if latest_cot else None,
            "managed_money_net_change": cot_delta,
            "managed_money_net_percentile": cot_percentile,
        },
        "change": etf_delta,
        "change_since": etf_since,
        "value_label": f"ETF {fmt_number(latest_etf['etf_total'])} 吨",
        "change_label": fmt_signed(etf_delta, suffix=" 吨"),
        "read": (
            f"SPDR+iShares 黄金 ETF 合计 {fmt_number(latest_etf['etf_total'])} 吨，"
            f"近约 1 个月变化 {fmt_signed(etf_delta, suffix=' 吨')}；"
            f"GVZ {fmt_number(latest_vol['gvz'] if latest_vol else None)}，"
            f"三年分位 {fmt_number((gvz_percentile or 0) * 100)}%。"
            f"{cot_sentence}"
            f"GVZ 数据 {gvz_date or '—'}({QUALITY_LABELS.get(gvz_quality, gvz_quality)},滞后 {gvz_lag if gvz_lag is not None else '—'} 天)。"
        ),
        "wrong_if": "ETF 持有量持续流出、价格跌破中期趋势，且 Managed Money 净多回落或拥挤度过高。",
        "next_trigger": "每周更新 CFTC COT，观察 ETF 流量与 Managed Money 净多是否同向。",
        "chart_key": "etf_total",
        "chart_rows": etf_rows,
    }


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


def make_valuation_snapshot(rows):
    latest = rows[-1] if rows else None
    if latest is None:
        return None
    return {
        "date": latest["date"],
        "gold_price": latest.get("gold_price"),
        "gold_to_m2": latest.get("gold_to_m2"),
        "valuation_percentile": latest.get("valuation_percentile"),
    }


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


def make_central_bank_relationship(reserve_rows, gold_rows):
    relationship_rows = build_reserve_relationship_rows(reserve_rows, gold_rows)
    rolling = rolling_corr(relationship_rows, "china_3m_change", "gold_return_3m", 24)
    latest_corr = rolling[-1]["corr"] if rolling else None
    start_month = latest_significant_cycle_start(
        relationship_rows,
        "china_3m_change",
        threshold=30,
        min_months=3,
    )

    phases = []
    phase_defs = [
        ("2018-2020", date(2018, 1, 1), date(2020, 12, 31)),
        ("2021-2022", date(2021, 1, 1), date(2022, 12, 31)),
        ("2023-2024H1", date(2023, 1, 1), date(2024, 6, 30)),
        ("2024H2-至今", date(2024, 7, 1), None),
    ]
    for label, start, end in phase_defs:
        corr, observations = corr_for_range(relationship_rows, "china_3m_change", "gold_return_3m", start, end)
        phases.append({
            "label": label,
            "corr": corr,
            "tone": corr_tone(corr, expected="positive"),
            "observations": observations,
            "gold_return": phase_gold_return(gold_rows, start, end),
        })

    latest_row = relationship_rows[-1] if relationship_rows else {}
    return {
        "id": "central_bank_purchases",
        "name": "央行购金",
        "metric": "中国官方黄金储备 3个月增量 vs 黄金 3个月收益",
        "expected": "positive",
        "latest_corr": latest_corr,
        "latest_tone": corr_tone(latest_corr, expected="positive"),
        "start_month": start_month,
        "weak_periods": weak_periods_from_rolling(rolling),
        "trend_rows": relationship_rows,
        "factor_key": "china_3m_change",
        "factor_label": "购金3个月增量",
        "gold_key": "gold_price",
        "rolling_corr": rolling,
        "phases": phases,
        "latest": {
            "date": latest_row.get("date"),
            "factor_change": latest_row.get("china_3m_change"),
            "gold_return": latest_row.get("gold_return_3m"),
        },
        "read": (
            f"什么时候开始显著推动：按中国官方黄金储备 3 个月增量连续超过 30 吨识别，"
            f"最近一轮从 {start_month or '—'} 开始。"
        ),
    }


def make_real_rate_relationship(real_rate_rows):
    return make_two_horizon_relationship(
        "real_rate",
        "实际利率",
        "实际利率变化 vs 黄金收益",
        real_rate_rows,
        "real_rate",
        expected="negative",
        short_label="实际利率短期",
        medium_label="实际利率中期",
        read_suffix="负值越强，实际利率对黄金的解释越有效。",
    )


def make_two_horizon_relationship(
    relationship_id,
    name,
    metric,
    rows,
    factor_key,
    expected,
    short_label,
    medium_label,
    read_suffix,
):
    short_pairs = build_change_pairs(rows, factor_key, "gold_price", 21)
    medium_pairs = build_change_pairs(rows, factor_key, "gold_price", 63)
    short_rolling = rolling_corr(short_pairs, "factor_change", "gold_return", 126)
    medium_rolling = rolling_corr(medium_pairs, "factor_change", "gold_return", 252)
    short_latest = short_rolling[-1]["corr"] if short_rolling else None
    medium_latest = medium_rolling[-1]["corr"] if medium_rolling else None

    phase_defs = [
        ("2003-2011", date(2003, 1, 1), date(2011, 12, 31)),
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
            "label": label,
            "short_corr": short_corr,
            "medium_corr": medium_corr,
            "short_tone": corr_tone(short_corr, expected=expected),
            "medium_tone": corr_tone(medium_corr, expected=expected),
            "observations": min(short_obs, medium_obs),
            "gold_return": phase_gold_return(rows, start, end),
        })

    return {
        "id": relationship_id,
        "name": name,
        "metric": metric,
        "expected": expected,
        "short_term": {
            "label": short_label,
            "window": "21日变化 vs 21日黄金收益，126日滚动相关",
            "latest_corr": short_latest,
            "tone": corr_tone(short_latest, expected=expected),
            "rolling_corr": short_rolling,
        },
        "medium_term": {
            "label": medium_label,
            "window": "63日变化 vs 63日黄金收益，252日滚动相关",
            "latest_corr": medium_latest,
            "tone": corr_tone(medium_latest, expected=expected),
            "rolling_corr": medium_rolling,
        },
        "latest_corr": medium_latest,
        "latest_tone": corr_tone(medium_latest, expected=expected),
        "rolling_corr": medium_rolling,
        "trend_rows": [row for row in rows if row.get(factor_key) is not None and row.get("gold_price") is not None],
        "factor_key": factor_key,
        "factor_label": name,
        "gold_key": "gold_price",
        "phases": phases,
        "weak_periods": weak_periods_from_rolling(medium_rolling),
        "read": (
            f"短期滚动相关 {fmt_corr(short_latest)}，中期滚动相关 {fmt_corr(medium_latest)}；"
            f"{read_suffix}"
        ),
    }


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


def make_dollar_relationship(dollar_rows):
    return make_two_horizon_relationship(
        "dollar",
        "美元",
        "美元指数变化 vs 黄金收益",
        dollar_rows,
        "dollar_index",
        expected="negative",
        short_label="美元短期",
        medium_label="美元中期",
        read_suffix="负值越强，美元走强对黄金的压制越稳定。",
    )


def make_inflation_relationship(breakeven_rows, gold_rows):
    rows = attach_gold_price(breakeven_rows, gold_rows)
    return make_two_horizon_relationship(
        "inflation_expectation",
        "通胀预期",
        "10Y breakeven 变化 vs 黄金收益",
        rows,
        "breakeven_10y",
        expected="positive",
        short_label="通胀预期短期",
        medium_label="通胀预期中期",
        read_suffix="正值越强，通胀补偿对黄金的解释越稳定。",
    )


def make_positioning_sub_metric(metric_id, name, rows, factor_key, expected, gold_rows=None):
    relationship_rows = attach_gold_price(rows, gold_rows) if gold_rows else rows
    pairs = build_change_pairs(relationship_rows, factor_key, "gold_price", 21)
    rolling = rolling_corr(pairs, "factor_change", "gold_return", 52)
    latest_corr = rolling[-1]["corr"] if rolling else None
    phase_defs = [
        ("2021-2022", date(2021, 1, 1), date(2022, 12, 31)),
        ("2023-2024", date(2023, 1, 1), date(2024, 12, 31)),
        ("2025-至今", date(2025, 1, 1), None),
    ]
    phases = []
    for label, start, end in phase_defs:
        corr, observations = corr_for_range(pairs, "factor_change", "gold_return", start, end)
        phases.append({
            "label": label,
            "corr": corr,
            "tone": corr_tone(corr, expected=expected),
            "observations": observations,
            "gold_return": phase_gold_return(relationship_rows, start, end),
        })
    return {
        "id": metric_id,
        "name": name,
        "expected": expected,
        "latest_corr": latest_corr,
        "latest_tone": corr_tone(latest_corr, expected=expected),
        "rolling_corr": rolling,
        "trend_rows": [
            row for row in relationship_rows
            if row.get(factor_key) is not None and row.get("gold_price") is not None
        ],
        "factor_key": factor_key,
        "factor_label": name,
        "gold_key": "gold_price",
        "phases": phases,
        "weak_periods": weak_periods_from_rolling(rolling),
    }


def make_positioning_relationship(etf_rows, cot_rows, vol_rows, gold_rows):
    for row in etf_rows:
        if row.get("spdr_holdings") is not None and row.get("ishares_holdings") is not None:
            row["etf_total"] = row["spdr_holdings"] + row["ishares_holdings"]
    sub_metrics = [
        make_positioning_sub_metric(
            "etf_holdings",
            "ETF 持仓",
            etf_rows,
            "etf_total",
            expected="positive",
            gold_rows=gold_rows,
        ),
        make_positioning_sub_metric(
            "managed_money",
            "Managed Money 净多",
            cot_rows,
            "managed_money_net",
            expected="positive",
            gold_rows=gold_rows,
        ),
        make_positioning_sub_metric(
            "gvz",
            "GVZ 波动率",
            vol_rows,
            "gvz",
            expected="absolute",
        ),
    ]
    latest_corrs = [item["latest_corr"] for item in sub_metrics if item["latest_corr"] is not None]
    latest_corr = sum(latest_corrs) / len(latest_corrs) if latest_corrs else None
    return {
        "id": "positioning_technical",
        "name": "仓位与技术",
        "metric": "ETF、CFTC Managed Money、GVZ 与黄金收益",
        "expected": "mixed",
        "latest_corr": latest_corr,
        "latest_tone": "分项观察",
        "sub_metrics": sub_metrics,
        "read": "仓位与技术层拆成 ETF 持仓、Managed Money 净多和 GVZ 波动率，分别看其对黄金短期收益的解释力。",
    }


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


def read_dashboard_data(today=None):
    today = today or date.today()
    workbook = load_workbook()
    try:
        reserve_rows = read_sheet_rows(workbook, SHEET_RESERVES, {"china_reserves": 1, "global_reserves": 2}, max_date=today)
        etf_rows = read_sheet_rows(workbook, SHEET_ETF, {"spdr_holdings": 1, "ishares_holdings": 2}, max_date=today)
        valuation_rows = read_sheet_rows(workbook, SHEET_VALUATION, {"gold_price": 7, "gold_to_m2": 9, "valuation_percentile": 10}, max_date=today)
        epu_rows = read_sheet_rows(workbook, SHEET_EPU, {"epu": 1}, max_date=today)
        gpr_rows = read_sheet_rows(workbook, SHEET_GPR, {"gpr": 1}, max_date=today)
    finally:
        workbook.close()

    manual_reserve_rows = read_official_reserve_rows(
        OFFICIAL_RESERVES_MANUAL_FILE, max_date=today)
    reserve_rows = merge_reserve_rows(reserve_rows, manual_reserve_rows)
    source_files = [str(DATA_FILE.relative_to(ROOT))]
    if manual_reserve_rows:
        source_files.append(str(OFFICIAL_RESERVES_MANUAL_FILE.relative_to(ROOT)))

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
        "source_file": " + ".join(source_files),
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


def make_sparkline(rows, key, color="#2563eb", width=360, height=96, limit=120):
    points = [(row["date"], row.get(key)) for row in rows if row.get(key) is not None][-limit:]
    if len(points) < 2:
        return '<div class="empty-chart">暂无足够数据</div>'

    values = [value for _, value in points]
    min_value = min(values)
    max_value = max(values)
    span = max_value - min_value or 1
    left_pad = 42
    right_pad = 10
    top_pad = 12
    bottom_pad = 22
    chart_width = width - left_pad - right_pad
    chart_height = height - top_pad - bottom_pad

    coords = []
    for i, (_, value) in enumerate(points):
        x = left_pad + chart_width * i / (len(points) - 1)
        y = top_pad + chart_height * (1 - (value - min_value) / span)
        coords.append((x, y))

    path = " ".join(f"{'M' if i == 0 else 'L'} {x:.1f} {y:.1f}" for i, (x, y) in enumerate(coords))
    first_label = points[0][0][2:7]
    last_label = points[-1][0][2:7]
    return f"""
    <svg class="sparkline" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(key)} trend">
      <line x1="{left_pad}" y1="{top_pad}" x2="{left_pad}" y2="{height - bottom_pad}" class="y-axis"></line>
      <line x1="{left_pad}" y1="{height - bottom_pad}" x2="{width - right_pad}" y2="{height - bottom_pad}" class="x-axis"></line>
      <text x="4" y="{top_pad + 4}" class="chart-label">{fmt_axis_value(max_value)}</text>
      <text x="4" y="{height - bottom_pad}" class="chart-label">{fmt_axis_value(min_value)}</text>
      <path d="{path}" fill="none" stroke="{color}" stroke-width="2.5"></path>
      <circle cx="{coords[-1][0]:.1f}" cy="{coords[-1][1]:.1f}" r="3.5" fill="{color}"></circle>
      <text x="{left_pad}" y="{height - 3}" class="chart-label">{first_label}</text>
      <text x="{width - right_pad}" y="{height - 3}" text-anchor="end" class="chart-label">{last_label}</text>
    </svg>
    """


def make_mini_bar_chart(rows, change_key, aria_label, bar_class="china-bar", width=360, height=118, limit=24):
    chart_rows = [row for row in rows if row.get(change_key) is not None][-limit:]
    changes = [row[change_key] for row in chart_rows]
    if not changes:
        return '<div class="empty-chart">暂无足够数据</div>'

    max_abs = max(abs(value) for value in changes) or 1
    left_pad = 42
    right_pad = 10
    top_pad = 14
    bottom_pad = 24
    chart_width = width - left_pad - right_pad
    chart_height = height - top_pad - bottom_pad
    zero_y = top_pad + chart_height / 2
    scale = (chart_height / 2 - 4) / max_abs
    slot_width = chart_width / len(chart_rows)
    bar_width = max(4, min(12, slot_width * 0.62))

    bars = []
    for i, row in enumerate(chart_rows):
        change = row[change_key]
        bar_height = max(1, abs(change) * scale)
        x = left_pad + slot_width * i + (slot_width - bar_width) / 2
        y = zero_y - bar_height if change >= 0 else zero_y
        bars.append(f"""
        <rect class="{bar_class}" x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" rx="2"></rect>
        """)

    first_label = chart_rows[0]["date"][2:7]
    last_label = chart_rows[-1]["date"][2:7]
    return f"""
    <svg class="mini-bar-chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(aria_label)}">
      <line x1="{left_pad}" y1="{top_pad}" x2="{left_pad}" y2="{height - bottom_pad}" class="y-axis"></line>
      <line x1="{left_pad}" y1="{zero_y:.1f}" x2="{width - right_pad}" y2="{zero_y:.1f}" class="x-axis"></line>
      <text x="4" y="{top_pad + 4}" class="chart-label">+{fmt_axis_value(max_abs)}</text>
      <text x="4" y="{height - bottom_pad}" class="chart-label">-{fmt_axis_value(max_abs)}</text>
      {''.join(bars)}
      <text x="{left_pad}" y="{height - 4}" class="chart-label">{first_label}</text>
      <text x="{width - right_pad}" y="{height - 4}" text-anchor="end" class="chart-label">{last_label}</text>
    </svg>
    """


def make_bar_chart(rows, change_key, aria_label, bar_class):
    chart_rows = [row for row in rows if row.get(change_key) is not None][-24:]
    changes = [row[change_key] for row in chart_rows]
    if not changes:
        return '<div class="empty-chart">暂无足够数据</div>'

    max_abs = max(abs(value) for value in changes) or 1
    bars = []
    x = 40
    width = 28
    gap = 10
    mid_y = 110
    scale = 78 / max_abs

    for row in chart_rows:
        change = row[change_key]
        height = abs(change) * scale
        y = mid_y - height if change >= 0 else mid_y
        label = row["date"][2:7]
        text_y = y - 6 if change >= 0 else y + height + 15
        bars.append(f"""
        <g>
          <rect class="{bar_class}" x="{x}" y="{y:.1f}" width="{width}" height="{height:.1f}" rx="3"></rect>
          <text x="{x + width / 2}" y="218" text-anchor="middle" class="chart-label">{label}</text>
          <text x="{x + width / 2}" y="{text_y:.1f}" text-anchor="middle" class="bar-value">{change:+.1f}</text>
        </g>
        """)
        x += width + gap

    svg_width = max(720, x + 20)
    return f"""
    <svg class="bar-chart" viewBox="0 0 {svg_width} 240" role="img" aria-label="{escape(aria_label)}">
      <line x1="30" y1="24" x2="30" y2="210" class="y-axis"></line>
      <line x1="30" y1="{mid_y}" x2="{svg_width - 20}" y2="{mid_y}" class="x-axis"></line>
      {''.join(bars)}
    </svg>
    """


def make_dual_axis_chart(
    rows,
    factor_key,
    factor_label,
    gold_key="gold_price",
    gold_label="黄金价格",
    width=720,
    height=220,
    limit=180,
    factor_color="#366b9f",
    gold_color="#b88728",
):
    points = [
        row for row in rows
        if row.get(factor_key) is not None and row.get(gold_key) is not None
    ][-limit:]
    if len(points) < 2:
        return '<div class="empty-chart">暂无足够双轴走势数据</div>'

    left_pad = 56
    right_pad = 66
    top_pad = 22
    bottom_pad = 32
    chart_width = width - left_pad - right_pad
    chart_height = height - top_pad - bottom_pad

    factor_values = [row[factor_key] for row in points]
    gold_values = [row[gold_key] for row in points]
    factor_min = min(factor_values)
    factor_max = max(factor_values)
    gold_min = min(gold_values)
    gold_max = max(gold_values)
    factor_span = factor_max - factor_min or 1
    gold_span = gold_max - gold_min or 1

    def x_for(index):
        return left_pad + chart_width * index / (len(points) - 1)

    def y_for(value, min_value, span):
        return top_pad + chart_height * (1 - (value - min_value) / span)

    factor_coords = [
        (x_for(i), y_for(row[factor_key], factor_min, factor_span))
        for i, row in enumerate(points)
    ]
    gold_coords = [
        (x_for(i), y_for(row[gold_key], gold_min, gold_span))
        for i, row in enumerate(points)
    ]
    factor_path = " ".join(
        f"{'M' if i == 0 else 'L'} {x:.1f} {y:.1f}" for i, (x, y) in enumerate(factor_coords)
    )
    gold_path = " ".join(
        f"{'M' if i == 0 else 'L'} {x:.1f} {y:.1f}" for i, (x, y) in enumerate(gold_coords)
    )
    first_label = points[0]["date"][2:7]
    last_label = points[-1]["date"][2:7]

    return f"""
    <div class="dual-axis-wrap">
      <div class="dual-axis-legend">
        <span><i style="background:{factor_color}"></i>{escape(factor_label)} {fmt_axis_value(points[-1][factor_key])}</span>
        <span><i style="background:{gold_color}"></i>{escape(gold_label)} {fmt_axis_value(points[-1][gold_key])}</span>
      </div>
      <svg class="dual-axis-chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(factor_label)}与{escape(gold_label)}双轴走势">
        <line x1="{left_pad}" y1="{top_pad}" x2="{left_pad}" y2="{height - bottom_pad}" class="left-axis"></line>
        <line x1="{width - right_pad}" y1="{top_pad}" x2="{width - right_pad}" y2="{height - bottom_pad}" class="right-axis"></line>
        <line x1="{left_pad}" y1="{height - bottom_pad}" x2="{width - right_pad}" y2="{height - bottom_pad}" class="x-axis"></line>
        <text x="6" y="{top_pad + 4}" class="chart-label">{fmt_axis_value(factor_max)}</text>
        <text x="6" y="{height - bottom_pad}" class="chart-label">{fmt_axis_value(factor_min)}</text>
        <text x="{width - 6}" y="{top_pad + 4}" text-anchor="end" class="chart-label">{fmt_axis_value(gold_max)}</text>
        <text x="{width - 6}" y="{height - bottom_pad}" text-anchor="end" class="chart-label">{fmt_axis_value(gold_min)}</text>
        <path d="{factor_path}" fill="none" stroke="{factor_color}" stroke-width="2.3"></path>
        <path d="{gold_path}" fill="none" stroke="{gold_color}" stroke-width="2.3"></path>
        <circle cx="{factor_coords[-1][0]:.1f}" cy="{factor_coords[-1][1]:.1f}" r="3.1" fill="{factor_color}"></circle>
        <circle cx="{gold_coords[-1][0]:.1f}" cy="{gold_coords[-1][1]:.1f}" r="3.1" fill="{gold_color}"></circle>
        <text x="{left_pad}" y="{height - 8}" class="chart-label">{first_label}</text>
        <text x="{width - right_pad}" y="{height - 8}" text-anchor="end" class="chart-label">{last_label}</text>
      </svg>
    </div>
    """


def make_corr_chart(series_defs, aria_label, width=720, height=180, limit=96):
    top_pad = 18
    bottom_pad = 34
    left_pad = 38
    right_pad = 16
    chart_width = width - left_pad - right_pad
    chart_height = height - top_pad - bottom_pad

    def y_for(value):
        clipped = max(-1, min(1, value))
        return top_pad + chart_height * (1 - (clipped + 1) / 2)

    zero_y = y_for(0)
    paths = []
    legends = []
    visible_points = []
    for series in series_defs:
        points = [row for row in series["rows"] if row.get("corr") is not None][-limit:]
        if len(points) < 2:
            continue
        visible_points.extend(points)
        coords = []
        for i, row in enumerate(points):
            x = left_pad + chart_width * i / (len(points) - 1)
            coords.append((x, y_for(row["corr"])))
        path = " ".join(f"{'M' if i == 0 else 'L'} {x:.1f} {y:.1f}" for i, (x, y) in enumerate(coords))
        dash = ' stroke-dasharray="5 4"' if series.get("dash") else ""
        paths.append(
            f'<path d="{path}" fill="none" stroke="{series["color"]}" stroke-width="2.4"{dash}></path>'
            f'<circle cx="{coords[-1][0]:.1f}" cy="{coords[-1][1]:.1f}" r="3.2" fill="{series["color"]}"></circle>'
        )
        legends.append(
            f'<span><i style="background:{series["color"]}"></i>{escape(series["label"])} {fmt_corr(points[-1]["corr"])}</span>'
        )

    if not paths:
        return '<div class="empty-chart">暂无足够滚动相关数据</div>'

    visible_points.sort(key=lambda row: row["_date"])
    first_label = visible_points[0]["date"][2:7]
    last_label = visible_points[-1]["date"][2:7]

    return f"""
    <div class="corr-wrap">
      <div class="corr-legend">{''.join(legends)}</div>
      <svg class="corr-chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(aria_label)}">
        <line x1="{left_pad}" y1="{zero_y:.1f}" x2="{width - right_pad}" y2="{zero_y:.1f}" class="zero-axis"></line>
        <line x1="{left_pad}" y1="{top_pad}" x2="{left_pad}" y2="{height - bottom_pad}" class="y-axis"></line>
        <line x1="{left_pad}" y1="{height - bottom_pad}" x2="{width - right_pad}" y2="{height - bottom_pad}" class="x-axis"></line>
        <text x="8" y="{top_pad + 4}" class="chart-label">+1</text>
        <text x="8" y="{zero_y + 4:.1f}" class="chart-label">0</text>
        <text x="8" y="{height - bottom_pad}" class="chart-label">-1</text>
        <text x="{left_pad}" y="{height - 8}" class="chart-label">{first_label}</text>
        <text x="{width - right_pad}" y="{height - 8}" text-anchor="end" class="chart-label">{last_label}</text>
        {''.join(paths)}
      </svg>
    </div>
    """


def tone_badge(tone):
    if tone == "有效":
        klass = "supportive"
    elif tone == "失效":
        klass = "headwind"
    else:
        klass = "neutral"
    return f'<span class="tone tone-{klass}">{escape(tone)}</span>'


def make_central_bank_relationship_card(relationship):
    weak_periods = relationship.get("weak_periods") or []
    if weak_periods:
        weak_text = "；".join(f"{start} 至 {end}" for start, end in weak_periods)
    else:
        weak_text = "未识别到连续 3 个月以上的低相关段"

    phase_rows = "".join(
        f"""
        <tr>
          <td>{escape(phase['label'])}</td>
          <td>{fmt_corr(phase['corr'])}</td>
          <td>{tone_badge(phase['tone'])}</td>
          <td>{fmt_pct(phase['gold_return'])}</td>
          <td>{phase['observations']}</td>
        </tr>
        """
        for phase in relationship["phases"]
    )
    chart = make_corr_chart(
        [{"label": "24个月滚动相关", "rows": relationship["rolling_corr"], "color": "#237a57"}],
        "央行购金与黄金收益滚动相关",
        limit=84,
    )
    trend_chart = make_dual_axis_chart(
        relationship["trend_rows"],
        relationship["factor_key"],
        relationship["factor_label"],
        relationship["gold_key"],
        limit=84,
        factor_color="#237a57",
    )

    return f"""
    <article class="relationship-card">
      <div class="relationship-head">
        <div>
          <div class="eyebrow">关系检验</div>
          <h3>{escape(relationship['name'])}</h3>
        </div>
        {tone_badge(relationship['latest_tone'])}
      </div>
      <div class="metric-strip">
        <div><span>起始显著推动</span><strong>{escape(relationship.get('start_month') or '—')}</strong></div>
        <div><span>当前滚动相关</span><strong>{fmt_corr(relationship['latest_corr'])}</strong></div>
        <div><span>最近 3 个月</span><strong>{fmt_signed(relationship['latest']['factor_change'])}</strong></div>
      </div>
      <p class="relationship-read">{escape(relationship['read'])} 什么时候不好用：{escape(weak_text)}。</p>
      <h4>双轴走势</h4>
      {trend_chart}
      <h4>滚动相关</h4>
      {chart}
      <h4>阶段表现</h4>
      <table class="phase-table">
        <thead><tr><th>阶段</th><th>相关系数</th><th>判断</th><th>黄金收益</th><th>样本(月)</th></tr></thead>
        <tbody>{phase_rows}</tbody>
      </table>
    </article>
    """


def make_horizon_relationship_card(relationship):
    weak_periods = relationship.get("weak_periods") or []
    if weak_periods:
        weak_text = "；".join(f"{start} 至 {end}" for start, end in weak_periods[-2:])
    else:
        weak_text = "未识别到连续低相关段"

    phase_rows = "".join(
        f"""
        <tr>
          <td>{escape(phase['label'])}</td>
          <td>{fmt_corr(phase['short_corr'])} {tone_badge(phase['short_tone'])}</td>
          <td>{fmt_corr(phase['medium_corr'])} {tone_badge(phase['medium_tone'])}</td>
          <td>{fmt_pct(phase['gold_return'])}</td>
          <td>{phase['observations']}</td>
        </tr>
        """
        for phase in relationship["phases"]
    )
    chart = make_corr_chart(
        [
            {
                "label": relationship["short_term"]["label"],
                "rows": relationship["short_term"]["rolling_corr"],
                "color": "#366b9f",
            },
            {
                "label": relationship["medium_term"]["label"],
                "rows": relationship["medium_term"]["rolling_corr"],
                "color": "#b88728",
                "dash": True,
            },
        ],
        f"{relationship['name']}与黄金收益滚动相关",
        limit=180,
    )
    trend_chart = make_dual_axis_chart(
        relationship["trend_rows"],
        relationship["factor_key"],
        relationship["factor_label"],
        relationship["gold_key"],
        limit=180,
    )

    return f"""
    <article class="relationship-card">
      <div class="relationship-head">
        <div>
          <div class="eyebrow">关系检验</div>
          <h3>{escape(relationship['name'])}</h3>
        </div>
        {tone_badge(relationship['latest_tone'])}
      </div>
      <div class="metric-strip">
        <div><span>{escape(relationship['short_term']['label'])}</span><strong>{fmt_corr(relationship['short_term']['latest_corr'])}</strong></div>
        <div><span>{escape(relationship['medium_term']['label'])}</span><strong>{fmt_corr(relationship['medium_term']['latest_corr'])}</strong></div>
        <div><span>低相关窗口</span><strong>{escape(weak_text)}</strong></div>
      </div>
      <p class="relationship-read">{escape(relationship['read'])}</p>
      <h4>双轴走势</h4>
      {trend_chart}
      <h4>滚动相关</h4>
      {chart}
      <h4>阶段表现</h4>
      <table class="phase-table">
        <thead><tr><th>阶段</th><th>短期相关</th><th>中期相关</th><th>黄金收益</th><th>样本(日)</th></tr></thead>
        <tbody>{phase_rows}</tbody>
      </table>
    </article>
    """


def make_positioning_relationship_card(relationship):
    phase_rows = []
    for sub_metric in relationship["sub_metrics"]:
        for phase in sub_metric["phases"]:
            phase_rows.append(f"""
        <tr>
          <td>{escape(sub_metric['name'])}</td>
          <td>{escape(phase['label'])}</td>
          <td>{fmt_corr(phase['corr'])}</td>
          <td>{tone_badge(phase['tone'])}</td>
          <td>{fmt_pct(phase['gold_return'])}</td>
          <td>{phase['observations']}</td>
        </tr>
            """)

    metric_strip = "".join(
        f"""
        <div><span>{escape(sub_metric['name'])}</span><strong>{fmt_corr(sub_metric['latest_corr'])}</strong></div>
        """
        for sub_metric in relationship["sub_metrics"]
    )
    chart = make_corr_chart(
        [
            {
                "label": relationship["sub_metrics"][0]["name"],
                "rows": relationship["sub_metrics"][0]["rolling_corr"],
                "color": "#237a57",
            },
            {
                "label": relationship["sub_metrics"][1]["name"],
                "rows": relationship["sub_metrics"][1]["rolling_corr"],
                "color": "#366b9f",
                "dash": True,
            },
            {
                "label": relationship["sub_metrics"][2]["name"],
                "rows": relationship["sub_metrics"][2]["rolling_corr"],
                "color": "#b88728",
            },
        ],
        "仓位与技术指标与黄金收益滚动相关",
        limit=120,
    )
    trend_chart_items = []
    for sub_metric in relationship["sub_metrics"]:
        trend_chart = make_dual_axis_chart(
            sub_metric["trend_rows"],
            sub_metric["factor_key"],
            sub_metric["factor_label"],
            sub_metric["gold_key"],
            limit=120,
        )
        trend_chart_items.append(f"""
        <div class="dual-axis-item">
          <h5>{escape(sub_metric['name'])}</h5>
          {trend_chart}
        </div>
        """)
    trend_charts = "".join(trend_chart_items)

    return f"""
    <article class="relationship-card relationship-card-wide">
      <div class="relationship-head">
        <div>
          <div class="eyebrow">关系检验</div>
          <h3>{escape(relationship['name'])}</h3>
        </div>
        <span class="tone tone-neutral">{escape(relationship['latest_tone'])}</span>
      </div>
      <div class="metric-strip">{metric_strip}</div>
      <p class="relationship-read">{escape(relationship['read'])}</p>
      <h4>双轴走势</h4>
      <div class="dual-axis-grid">{trend_charts}</div>
      <h4>滚动相关</h4>
      {chart}
      <h4>阶段表现</h4>
      <table class="phase-table">
        <thead><tr><th>子指标</th><th>阶段</th><th>相关系数</th><th>判断</th><th>黄金收益</th><th>样本</th></tr></thead>
        <tbody>{''.join(phase_rows)}</tbody>
      </table>
    </article>
    """


def make_relationship_section(relationships):
    cards = []
    for relationship in relationships:
        if relationship["id"] in {"central_bank_purchases", "epu", "gpr"}:
            cards.append(make_central_bank_relationship_card(relationship))
        elif relationship["id"] in {"real_rate", "dollar", "inflation_expectation", "price_trend"}:
            cards.append(make_horizon_relationship_card(relationship))
        elif relationship["id"] == "positioning_technical":
            cards.append(make_positioning_relationship_card(relationship))

    return f"""
  <section class="wide relationship-section">
    <div class="section-title-row">
      <div>
        <h2>因子关系检验</h2>
        <p>用同一批数值数据检验“指标是否好用”：看当前滚动相关，也看阶段表现。正负方向按常识方向设定，但结论只来自相关系数。</p>
      </div>
    </div>
    <div class="relationship-grid">
      {''.join(cards)}
    </div>
  </section>
    """


def layer_card(layer):
    chart = ""
    if layer["chart_key"]:
        if layer.get("chart_type") == "bar":
            chart = make_mini_bar_chart(
                layer["chart_rows"],
                layer["chart_key"],
                layer.get("chart_label", f"{layer['name']}环比柱状图"),
            )
        else:
            chart = make_sparkline(layer["chart_rows"], layer["chart_key"])

    return f"""
    <article class="layer-card state-{layer['state']}">
      <div class="layer-head">
        <div>
          <div class="eyebrow">{escape(layer['name'])}</div>
          <h3>{escape(layer['value_label'])}</h3>
        </div>
        <div class="badges">
          <span class="state">{state_badge(layer['state'])}</span>
          <span class="quality">{quality_badge(layer['data_quality'])}</span>
        </div>
      </div>
      <p>{escape(layer['read'])}</p>
      {chart}
      <dl class="mini-meta">
        <div><dt>数据日期</dt><dd>{escape(layer['latest']['date'] or '—')}</dd></div>
        <div><dt>最近变化</dt><dd>{escape(layer['change_label'])}</dd></div>
        <div><dt>来源</dt><dd>{escape(layer['source'])}</dd></div>
      </dl>
    </article>
    """


def make_change_list(layers):
    items = []
    for layer in layers:
        if layer["state"] == "planned":
            items.append(f"{layer['name']}：{layer['read']}")
        else:
            items.append(f"{layer['name']}：{layer['read']}")
    return items[:5]


def build_html(dashboard):
    layers = dashboard["layers"]
    cards = "\n".join(layer_card(layer) for layer in layers)
    relationship_section = make_relationship_section(dashboard["relationships"])
    changes = "".join(f"<li>{escape(item)}</li>" for item in make_change_list(layers))
    wrong_if = "".join(f"<li>{escape(layer['wrong_if'])}</li>" for layer in layers)
    next_triggers = "".join(f"<li>{escape(layer['next_trigger'])}</li>" for layer in layers)
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

    valuation = dashboard["valuation"] or {}
    valuation_text = (
        f"长期估值：金价/M2 {fmt_number(valuation.get('gold_to_m2'), 3)}，"
        f"估值分位 {fmt_number((valuation.get('valuation_percentile') or 0) * 100)}%，"
        f"数据日期 {valuation.get('date', '—')}。"
    )

    china_chart = make_bar_chart(
        dashboard["reserve_rows"],
        "china_mom_change",
        "中国央行黄金储备环比变化柱状图",
        "china-bar",
    )
    global_chart = make_bar_chart(
        dashboard["reserve_rows"],
        "global_mom_change",
        "全球央行黄金储备环比变化柱状图",
        "global-bar",
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(dashboard['title'])}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8f5;
      --panel: #ffffff;
      --line: #dfe3da;
      --text: #172019;
      --muted: #677064;
      --support: #237a57;
      --neutral: #7a6a26;
      --headwind: #b94343;
      --planned: #687080;
      --gold: #b88728;
      --blue: #366b9f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.55;
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 20px 48px;
    }}
    h1, h2, h3, p {{ margin: 0; }}
    .hero {{
      border-bottom: 1px solid var(--line);
      padding-bottom: 20px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) 220px;
      gap: 20px;
      align-items: end;
    }}
    .kicker, .eyebrow {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
    }}
    h1 {{
      font-size: 34px;
      line-height: 1.15;
      margin-top: 6px;
      letter-spacing: 0;
    }}
    .subtitle {{
      margin-top: 8px;
      color: var(--muted);
      max-width: 760px;
      font-size: 14px;
    }}
    .posture {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 14px;
      text-align: right;
    }}
    .posture .label {{ color: var(--muted); font-size: 12px; }}
    .posture .value {{ font-size: 30px; font-weight: 800; }}
    .posture .score {{ color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
      margin-top: 20px;
    }}
    .layer-card {{
      min-width: 0;
      border: 1px solid var(--line);
      border-top: 3px solid var(--planned);
      border-radius: 8px;
      background: var(--panel);
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .state-supportive {{ border-top-color: var(--support); }}
    .state-neutral {{ border-top-color: var(--neutral); }}
    .state-headwind {{ border-top-color: var(--headwind); }}
    .state-planned {{ border-top-color: var(--planned); }}
    .layer-head {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: start;
    }}
    .layer-card h3 {{
      font-size: 18px;
      line-height: 1.2;
      margin-top: 4px;
    }}
    .layer-card p {{
      color: #334036;
      font-size: 13px;
      min-height: 60px;
    }}
    .badges {{
      display: flex;
      flex-direction: column;
      gap: 4px;
      align-items: flex-end;
      flex-shrink: 0;
    }}
    .state, .quality {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 2px 7px;
      font-size: 11px;
      color: var(--muted);
      background: #f7f8f5;
      white-space: nowrap;
    }}
    .mini-meta {{
      display: grid;
      gap: 6px;
      margin: 0;
      padding-top: 8px;
      border-top: 1px solid var(--line);
      font-size: 11px;
    }}
    .mini-meta div {{
      display: grid;
      grid-template-columns: 58px 1fr;
      gap: 8px;
    }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; min-width: 0; overflow-wrap: anywhere; }}
    .section-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      margin-top: 18px;
    }}
    .panel {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 16px;
    }}
    .panel h2 {{
      font-size: 16px;
      margin-bottom: 10px;
    }}
    ul {{
      margin: 0;
      padding-left: 18px;
      color: #334036;
      font-size: 13px;
    }}
    li + li {{ margin-top: 6px; }}
    .wide {{
      margin-top: 18px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 16px;
      overflow-x: auto;
    }}
    .wide h2 {{ font-size: 16px; margin-bottom: 10px; }}
    .chart-pair {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
    }}
    .chart-box {{
      min-width: 520px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }}
    .chart-box h3 {{
      font-size: 14px;
      margin-bottom: 6px;
    }}
    .sparkline, .mini-bar-chart, .bar-chart, .corr-chart, .dual-axis-chart {{
      width: 100%;
      height: auto;
      display: block;
    }}
    .section-title-row {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 14px;
    }}
    .section-title-row p {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 4px;
      max-width: 760px;
    }}
    .relationship-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
    }}
    .relationship-card {{
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: #fcfdfb;
    }}
    .relationship-card-wide {{
      grid-column: 1 / -1;
    }}
    .relationship-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .relationship-head h3 {{
      font-size: 18px;
      margin-top: 3px;
    }}
    .relationship-card h4 {{
      margin: 14px 0 6px;
      font-size: 13px;
      color: var(--muted);
    }}
    .relationship-read {{
      color: #334036;
      font-size: 13px;
      margin-top: 10px;
    }}
    .metric-strip {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }}
    .metric-strip div {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px;
      min-width: 0;
      background: #ffffff;
    }}
    .metric-strip span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      margin-bottom: 3px;
    }}
    .metric-strip strong {{
      display: block;
      font-size: 16px;
      line-height: 1.2;
      overflow-wrap: anywhere;
    }}
    .tone {{
      display: inline-block;
      border-radius: 6px;
      padding: 2px 7px;
      font-size: 11px;
      border: 1px solid var(--line);
      white-space: nowrap;
    }}
    .tone-supportive {{ color: var(--support); background: #ecf6f0; }}
    .tone-neutral {{ color: var(--neutral); background: #faf6e6; }}
    .tone-headwind {{ color: var(--headwind); background: #fbeeee; }}
    .corr-wrap {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #ffffff;
    }}
    .corr-legend {{
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }}
    .corr-legend span {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
    }}
    .corr-legend i {{
      display: inline-block;
      width: 16px;
      height: 3px;
      border-radius: 4px;
    }}
    .dual-axis-wrap {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #ffffff;
    }}
    .dual-axis-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }}
    .dual-axis-item {{
      min-width: 0;
    }}
    .dual-axis-item h5 {{
      margin: 0 0 6px;
      color: var(--muted);
      font-size: 12px;
    }}
    .dual-axis-legend {{
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }}
    .dual-axis-legend span {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
    }}
    .dual-axis-legend i {{
      display: inline-block;
      width: 16px;
      height: 3px;
      border-radius: 4px;
    }}
    .phase-table td:nth-child(2),
    .phase-table td:nth-child(3) {{
      white-space: nowrap;
    }}
    .x-axis, .y-axis, .zero-axis, .left-axis, .right-axis {{ stroke: #c5cbbf; stroke-width: 1; }}
    .right-axis {{ stroke: var(--gold); }}
    .left-axis {{ stroke: var(--blue); }}
    .zero-axis {{ stroke-dasharray: 3 4; }}
    .chart-label, .bar-value {{
      fill: var(--muted);
      font-size: 11px;
    }}
    .china-bar {{ fill: var(--support); }}
    .global-bar {{ fill: var(--gold); }}
    table {{
      border-collapse: collapse;
      width: 100%;
      font-size: 13px;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 9px 8px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }}
    .method {{
      margin-top: 18px;
      color: var(--muted);
      font-size: 12px;
    }}
    .empty-chart {{
      min-height: 96px;
      color: var(--muted);
      display: flex;
      align-items: center;
      justify-content: center;
      border: 1px dashed var(--line);
      border-radius: 8px;
      font-size: 12px;
    }}
    @media (max-width: 980px) {{
      .hero, .section-grid, .chart-pair {{ grid-template-columns: 1fr; }}
      .relationship-grid {{ grid-template-columns: 1fr; }}
      .dual-axis-grid {{ grid-template-columns: 1fr; }}
      .cards {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .posture {{ text-align: left; }}
      .chart-box {{ min-width: 0; }}
    }}
    @media (max-width: 560px) {{
      main {{ padding: 20px 14px 36px; }}
      h1 {{ font-size: 28px; }}
      .cards {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<main>
  <section class="hero">
    <div>
      <div class="kicker">Gold dashboard · data first</div>
      <h1>{escape(dashboard['title'])}</h1>
      <p class="subtitle">只使用本地数值数据生成状态判断；不读取任何文字观点或人工结论。当前版本覆盖实际利率、美元、FRED 通胀预期、央行购金、ETF/波动率与 CFTC 仓位。</p>
    </div>
    <aside class="posture state-{dashboard['posture_state']}">
      <div class="label">当前姿态</div>
      <div class="value">{escape(dashboard['posture'])}</div>
      <div class="score">score {dashboard['score']:+d} / {dashboard['active_layers']} · tendency {dashboard['tendency']:+.2f}</div>
    </aside>
  </section>

  <section class="cards">
    {cards}
  </section>

  <section class="section-grid">
    <div class="panel">
      <h2>What Changed</h2>
      <ul>{changes}</ul>
    </div>
    <div class="panel">
      <h2>失效条件</h2>
      <ul>{wrong_if}</ul>
    </div>
    <div class="panel">
      <h2>下一观察点</h2>
      <ul>{next_triggers}</ul>
    </div>
  </section>

  {relationship_section}

  <section class="wide">
    <h2>央行购金：最近 24 个月环比变化</h2>
    <div class="chart-pair">
      <div class="chart-box">
        <h3>中国央行黄金储备环比（吨）</h3>
        {china_chart}
      </div>
      <div class="chart-box">
        <h3>全球官方黄金储备环比（吨）</h3>
        {global_chart}
      </div>
    </div>
  </section>

  <section class="wide">
    <h2>数据质量</h2>
    <table>
      <thead>
        <tr>
          <th>层级</th>
          <th>状态</th>
          <th>滞后</th>
          <th>数据日期</th>
          <th>来源</th>
        </tr>
      </thead>
      <tbody>{quality_rows}</tbody>
    </table>
    <p class="method">{escape(valuation_text)}</p>
    <p class="method">Last built: {escape(dashboard['updated_at'])} · Source: {escape(dashboard['source_file'])}</p>
  </section>
</main>
</body>
</html>
"""


def main():
    SITE_DIR.mkdir(exist_ok=True)
    dashboard = read_dashboard_data()
    html = build_html(dashboard)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
