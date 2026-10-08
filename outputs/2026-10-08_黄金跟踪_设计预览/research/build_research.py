"""Reproducible descriptive research; reads reviewed local inputs only."""

import calendar
import csv
import hashlib
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE_MONTH = "2020-12"
ASSETS = {"gold": "黄金", "silver": "白银", "bitcoin": "比特币"}


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def read_rows(path, today):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [{key: value if key == "date" else number(value) for key, value in row.items()}
                for row in csv.DictReader(handle)
                if row.get("date") and date.fromisoformat(row["date"]) <= today]


def end_month(month):
    year, month_number = map(int, month.split("-"))
    return date(year, month_number, calendar.monthrange(year, month_number)[1]).isoformat()


def shift_month(month, count):
    year, month_number = map(int, month.split("-"))
    total = year * 12 + month_number - 1 + count
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def corr(x, y):
    if len(x) < 3 or len(x) != len(y):
        return None
    mx, my = mean(x), mean(y)
    vx, vy = sum((v - mx) ** 2 for v in x), sum((v - my) ** 2 for v in y)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(vx * vy) if vx > 0 and vy > 0 else None


def residuals(values, control):
    mv, mc = mean(values), mean(control)
    variance = sum((c - mc) ** 2 for c in control)
    slope = sum((v - mv) * (c - mc) for v, c in zip(values, control)) / variance if variance else 0
    return [v - mv - slope * (c - mc) for v, c in zip(values, control)]


def season_center(values, months):
    centers = {m: mean(v for v, month in zip(values, months) if month == m) for m in set(months)}
    return [v - centers[m] for v, m in zip(values, months)]


def partial_corr(points, seasonal=False):
    if len(points) < 12:
        return None
    x, y, z = ([p[index] for p in points] for index in (1, 2, 3))
    if seasonal:
        months = [p[0][5:7] for p in points]
        x, y, z = (season_center(v, months) for v in (x, y, z))
    return corr(residuals(x, z), residuals(y, z))


def block_interval(points, repetitions=600):
    """Six-month circular block bootstrap; includes temporal dependence."""
    n = len(points)
    if n < 36:
        return None
    rng = random.Random(20261008)
    estimates = []
    for _ in range(repetitions):
        sampled = []
        while len(sampled) < n:
            start = rng.randrange(n)
            sampled.extend(points[(start + j) % n] for j in range(6))
        result = partial_corr(sampled[:n])
        if result is not None:
            estimates.append(result)
    estimates.sort()
    return [estimates[int(len(estimates) * .025)], estimates[min(len(estimates)-1, int(len(estimates) * .975))]] if estimates else None


def monthly_prices(rows, key, today):
    grouped = {}
    for row in sorted(rows, key=lambda r: r["date"]):
        if row.get(key) and row[key] > 0 and end_month(row["date"][:7]) < today.isoformat():
            grouped[row["date"][:7]] = row
    return {month: {"value": row[key], "date": row["date"]}
            for month, row in grouped.items()
            if (date.fromisoformat(end_month(month)) - date.fromisoformat(row["date"])).days <= 7}


def log_changes(values):
    changes = {}
    for month, value in values.items():
        previous = values.get(shift_month(month, -1))
        if previous and previous > 0 and value > 0:
            changes[month] = math.log(value / previous)
    return changes


def monthly_fx_contribution(rows):
    by_month = {row["date"][:7]: row for row in rows}
    result = {}
    for month, row in by_month.items():
        previous = by_month.get(shift_month(month, -1))
        if not previous or not previous.get("total_usd_bn"):
            continue
        contribution = sum((row[money] + previous[money]) / 2 * (row[fx] - previous[fx])
                           for money, fx in (("cn_m2_cny_bn", "usd_per_cny"), ("ea_m2_eur_bn", "usd_per_eur")))
        result[month] = contribution / previous["total_usd_bn"]
    return result


def points_for(asset, money, dollar, lag=0):
    return [(month, money[shift_month(month, -lag)], value, dollar[month])
            for month, value in sorted(asset.items())
            if shift_month(month, -lag) in money and month in dollar]


def describe(points, intervals=True):
    return {"n": len(points), "start": points[0][0] if points else None,
            "end": points[-1][0] if points else None,
            "correlation": corr([p[1] for p in points], [p[2] for p in points]),
            "partial_dollar": partial_corr(points),
            "partial_dollar_calendar": partial_corr(points, seasonal=True),
            "interval": block_interval(points) if intervals else None}


def make_research(today):
    money_rows = read_rows(HERE / "liquidity_monthly.csv", today)
    related_rows = read_rows(HERE / "assets_daily.csv", today)
    gold_rows = read_rows(ROOT / "data/market/wind_daily.csv", today)
    liquidity_sources = json.loads((HERE / "liquidity_sources.json").read_text())
    asset_sources = json.loads((HERE / "asset_sources.json").read_text())
    prices = {"gold": monthly_prices(gold_rows, "gold_price", today),
              **{key: monthly_prices(related_rows, key, today) for key in ASSETS if key != "gold"}}
    dollar = monthly_prices(gold_rows, "dollar_index", today)
    dollar_changes = log_changes({m: p["value"] for m, p in dollar.items()})
    money_values = {"usd": {r["date"][:7]: r["total_usd_bn"] for r in money_rows if r.get("total_usd_bn")},
                    "fixed": {r["date"][:7]: r["total_fixed_usd_bn"] for r in money_rows if r.get("total_fixed_usd_bn")}}
    changes = {key: log_changes(values) for key, values in money_values.items()}
    fx_changes = monthly_fx_contribution(money_rows)
    output = {"asOf": today.isoformat(), "liquiditySources": liquidity_sources, "assetSources": asset_sources,
              "moneyRows": money_rows, "results": {}, "lags": {}, "periods": {}, "rolling": {},
              "method": {"frequency": "monthly, non-overlapping log changes / asset log returns",
                         "control": "contemporaneous dollar-index log return; partial correlation, not causality",
                         "seasonalSensitivity": "remove full-sample calendar-month means from money/asset/dollar changes; descriptive only",
                         "interval": "95% circular moving-block bootstrap, 6-month blocks, 600 repetitions, seed 20261008",
                         "lagPolicy": "preselected lags 0/1/3/6 months; common target-month intersection across every lag and both FX methods; no optimized chart shifts; latest revisions do not support an investable forecast",
                         "prices": "same month, last own valid observation within 7 days of month end; US M2 monthly average versus other stocks at month end"}}
    inputs = [HERE / filename for filename in ("liquidity_monthly.csv", "assets_daily.csv", "liquidity_sources.json", "asset_sources.json", "build_research.py")]
    inputs.append(ROOT / "data/market/wind_daily.csv")
    output["inputHashes"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    output["priceSource"] = {"file": "data/market/wind_daily.csv", "provider": "Wind 已保存生产输入",
                             "fields": {"gold_price": "SPTAUUSDOZ.IDC", "dollar_index": "USDX.FX"},
                             "first": gold_rows[0]["date"], "last": gold_rows[-1]["date"], "rows": len(gold_rows)}
    for asset, label in ASSETS.items():
        returns = log_changes({m: p["value"] for m, p in prices[asset].items()})
        sets = {kind: points_for(returns, values, dollar_changes) for kind, values in changes.items()}
        shared = set(p[0] for p in sets["usd"]) & set(p[0] for p in sets["fixed"])
        sets = {kind: [p for p in points if p[0] in shared] for kind, points in sets.items()}
        output["results"][asset] = {"name": label, **{kind: describe(points) for kind, points in sets.items()},
                                    "fxDollarCorrelation": corr([fx_changes[m] for m in sorted(shared) if m in fx_changes], [dollar_changes[m] for m in sorted(shared) if m in fx_changes])}
        lag_sets = {lag: {kind: points_for(returns, values, dollar_changes, lag) for kind, values in changes.items()} for lag in (0,1,3,6)}
        lag_shared = set.intersection(*(set(p[0] for p in points) for by_kind in lag_sets.values() for points in by_kind.values()))
        output["lags"][asset] = [{"lag": lag, **{kind: describe([p for p in points if p[0] in lag_shared], intervals=False) for kind, points in by_kind.items()}} for lag, by_kind in lag_sets.items()]
        output["periods"][asset] = [{"label": f"{first}–{last}", **{kind: describe([p for p in points if first <= int(p[0][:4]) <= last], intervals=False) for kind, points in sets.items()}} for first, last in ((2003,2012),(2013,2019),(2020,2026))]
        points = sets["fixed"]
        rolling = []
        for i in range(35, len(points)):
            window = points[i-35:i+1]
            if shift_month(window[0][0], 35) == window[-1][0]:
                rolling.append([end_month(window[-1][0]), corr([p[1] for p in window], [p[2] for p in window])])
        output["rolling"][asset] = rolling
    (HERE / "research_results.json").write_text(json.dumps(output, ensure_ascii=False, allow_nan=False, indent=2))
    lines = ["# 黄金与货币背景：可复核的描述性检验", "", f"整理日期：{today}。研究组合：美国、中国、欧元区 M2；不等于全球流动性总量。", "", "## 方法", "", "以非重叠月度对数变化比较货币增长和资产收益；先看当期汇率与固定 2020-12 汇率，再控制同期美元指数。月度 M2 全为非季调，另去除全样本日历月均值做季节性敏感性检查，不是官方季调，也不是历史时点可用的调整。偏相关的 95% 区间采用六个月分块重采样；不以同期相关冒充因果或预测。", "", "## 同期结果", "", "|资产|月份数|当期汇率相关|固定汇率相关|固定汇率控制美元后|偏相关95%区间|再去除日历月份后|", "|---|---:|---:|---:|---:|---|---:|"]
    fmt = lambda x: "—" if x is None else f"{x:.3f}"
    for result in output["results"].values():
        fixed = result["fixed"]
        interval = fixed["interval"]
        ci = "—" if not interval else f"[{fmt(interval[0])}, {fmt(interval[1])}]"
        lines.append(f"|{result['name']}|{fixed['n']}|{fmt(result['usd']['correlation'])}|{fmt(fixed['correlation'])}|{fmt(fixed['partial_dollar'])}|{ci}|{fmt(fixed['partial_dollar_calendar'])}|")
    lines += ["", "## 边界", "", "当前修订序列，没有按历史披露日和版本做交易回测。固定汇率只去掉折算效应，不会消除各国统计口径变化、季节性或金融结构差异。美国为月均余额，中欧为月末余额。黄金比值含有金价自身，不能用与同期金价的相关证明预测能力。比特币和金属的日切时点不同。相关检验样本和图区选择分别标示，未挑选最有利区间或时滞。", "", "数据和精确方法见同目录 liquidity_sources.json、asset_sources.json、research_results.json 和 build_research.py。"]
    (HERE / "2026-10-08_黄金与货币背景_实证研究.md").write_text("\n".join(lines))
    return output


if __name__ == "__main__":
    result = make_research(date(2026,10,8))
    print(json.dumps(result["results"], ensure_ascii=False, indent=2))
