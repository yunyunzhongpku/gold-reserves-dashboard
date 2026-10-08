"""Attach reviewed research to the isolated preview; no production mutation."""

import csv
import calendar
import hashlib
import json
import math
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESEARCH = HERE / "research"


def liquidity_yoy_comparison(money_rows, gold_daily, today):
    """Matched arithmetic YoY; requires 13 complete consecutive monthly points."""
    def month_end(month):
        year, number = map(int, month.split("-"))
        return date(year, number, calendar.monthrange(year, number)[1]).isoformat()

    def shift_month(month, offset):
        year, number = map(int, month.split("-"))
        total = year * 12 + number - 1 + offset
        return f"{total // 12:04d}-{total % 12 + 1:02d}"

    def positive(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0

    money = {}
    for row in money_rows:
        when = row["date"]
        month = when[:7]
        value = row.get("total_fixed_usd_bn")
        if when == month_end(month) and when <= today.isoformat() and positive(value):
            money[month] = value
    gold_latest = {}
    for row in sorted(gold_daily, key=lambda row: row["date"]):
        when = row["date"]
        month = when[:7]
        if when <= today.isoformat() and month_end(month) <= today.isoformat() and positive(row.get("gold_price")):
            gold_latest[month] = row
    gold = {month: row["gold_price"] for month, row in gold_latest.items()
            if 0 <= (date.fromisoformat(month_end(month)) - date.fromisoformat(row["date"])).days <= 7}
    money_yoy, gold_yoy = [], []
    for month in sorted(set(money) & set(gold)):
        window = [shift_month(month, -offset) for offset in range(13)]
        if not all(point in money and point in gold for point in window):
            continue
        previous = shift_month(month, -12)
        gold_value = (gold[month] / gold[previous] - 1) * 100
        money_value = (money[month] / money[previous] - 1) * 100
        if math.isfinite(gold_value) and math.isfinite(money_value):
            when = month_end(month)
            gold_yoy.append([when, gold_value])
            money_yoy.append([when, money_value])
    comparison = {
        "goldRows": gold_yoy, "date": gold_yoy[-1][0] if gold_yoy else None,
        "start": gold_yoy[0][0] if gold_yoy else None, "n": len(gold_yoy),
        "goldValue": gold_yoy[-1][1] if gold_yoy else None,
        "moneyValue": money_yoy[-1][1] if money_yoy else None,
        "note": "同月算术同比＝(当月值÷12个月前值−1)×100；两条序列均须连续13个月完整。黄金取月底前7天内最后有效报价；M2为美中欧非季调余额、汇率固定2020-12。同比单位均为%，无需指数基期。美国月均与中欧月末余额存在时间口径差异。",
        "goldFieldMeta": {
            "name": "黄金同比", "unit": "%", "frequency": "monthly",
            "key": "gold_yoy_pct", "sourceKey": "gold_price",
            "file": "data/market/wind_daily.csv", "source": "Wind · 伦敦金现",
            "sourceUrl": "", "transform": "月底前7天内最后有效金价 ÷ 12个月前同口径金价 − 1，再乘100；要求连续13个月完整",
        },
    }
    return money_yoy, comparison


def extend_snapshot(snapshot, builder):
    today = date.fromisoformat(snapshot["snapshotDate"])
    research = json.loads((RESEARCH / "research_results.json").read_text())
    if research["asOf"] != snapshot["snapshotDate"]:
        raise ValueError("研究日期与预览快照日期不一致；请先重新生成研究")
    root = HERE.parents[1]
    if any(hashlib.sha256((root / filename).read_bytes()).hexdigest() != digest for filename, digest in research["inputHashes"].items()):
        raise ValueError("研究输入或方法已变更；请先重新生成研究，避免混用同日旧结果")
    with (RESEARCH / "assets_daily.csv").open(encoding="utf-8-sig", newline="") as handle:
        asset_rows = list(csv.DictReader(handle))
    money = research["moneyRows"]
    metadata = research["assetSources"]["assets"]
    study = json.loads((RESEARCH / "asset_study.json").read_text())
    base = next(row for row in money if row["date"].startswith("2020-12"))
    gold_daily = builder.read_csv_rows(builder.WIND_DAILY_FILE, ["gold_price"], max_date=today)
    gold_monthly = {}
    for row in gold_daily:
        if builder.is_number(row.get("gold_price")):
            gold_monthly[row["date"][:7]] = row
    base_gold = gold_monthly["2020-12"]["gold_price"]
    money_yoy_rows, liquidity_comparison = liquidity_yoy_comparison(money, gold_daily, today)

    def metric(mid, name, unit, rows, frequency, source, source_url, file, key, note, category, relation=None, change_text=None, research_id=None):
        valid = [row for row in rows if row[1] is not None]
        last = valid[-1] if valid else [None, None]
        quality, lag = builder.classify_staleness(last[0], today, frequency)
        return {"id": mid, "name": name, "category": category, "unit": unit, "kind": "line",
                "rows": [row for row in rows if row[0] <= today.isoformat()], "value": last[1], "date": last[0],
                "quality": quality, "lag": lag, "frequency": frequency, "state": "context", "delta": None,
                "since": None, "window": "", "note": note, "source": source, "sourceUrl": source_url,
                "file": file, "key": key, "relation": relation, "changeText": change_text, "researchId": research_id}

    ratio_rows = []
    for row in money:
        gold = gold_monthly.get(row["date"][:7])
        if gold and (date.fromisoformat(row["date"]) - date.fromisoformat(gold["date"])).days <= 7:
            value = gold["gold_price"] / row["total_fixed_usd_bn"] / (base_gold / base["total_fixed_usd_bn"]) * 100
            ratio_rows.append([row["date"], value])
    latest = money[-1]
    rolling = research["rolling"]["gold"]
    money_relation = {"value": rolling[-1][1] if rolling else None, "date": rolling[-1][0] if rolling else None,
                      "rows": rolling, "window": "36 个非重叠月度样本：固定汇率 M2 对数增速与同期黄金对数收益", "expected": "研究背景，不预设相关方向"}
    additions = [
        metric("liquidity_fixed", "M2 余额指数 · 固定汇率", "指数", [[r["date"], r["total_fixed_usd_bn"] / base["total_fixed_usd_bn"] * 100] for r in money], "monthly", "Fed / PBoC / ECB", "https://fred.stlouisfed.org/series/M2NS", "research/liquidity_monthly.csv", "total_fixed_usd_bn", "固定 2020-12 汇率，基期＝100；观察本币货币余额变化，不等于全球流动性总量。", "货币背景 · 不计分", money_relation, f"同比 {latest['m2_fixed_fx_yoy_pct']:+.2f}% · 固定汇率", "liquidity"),
        metric("liquidity_usd", "M2 余额 · 美元折算", "万亿美元", [[r["date"], r["total_usd_bn"] / 1000] for r in money], "monthly", "Fed / PBoC / ECB + H.10", "https://data.ecb.europa.eu/data/datasets/BSI/BSI.M.U2.N.V.M20.X.1.U2.2300.Z01.E", "research/liquidity_monthly.csv", "total_usd_bn", "三地本币 M2 按各月汇率直接折美元加总；无额外权重或扣减，包含汇率折算影响。", "货币背景 · 不计分", None, f"同比 {latest['m2_current_fx_yoy_pct']:+.2f}% · 当期汇率", "liquidity"),
        metric("gold_liquidity", "金价 / M2 · 固定口径", "指数", ratio_rows, "monthly", "Wind 金价 + 三地 M2", "https://www.federalreserve.gov/releases/h6/current/default.htm", "research/liquidity_monthly.csv + data/market/wind_daily.csv", "gold_over_fixed_m2_index", "月末金价除以美中欧固定汇率 M2，2020-12＝100；仅作比值观察，不代表公允价值或黄金市值比例。", "货币背景 · 不计分", None, None, "liquidity"),
    ]
    additions[0].update(key="m2_fixed_fx_index_2020_12_100", sourceKey="total_fixed_usd_bn", transform="固定汇率 M2 十亿美元余额 ÷ 2020-12 余额 × 100")
    additions[1].update(key="m2_current_fx_usd_trillions", sourceKey="total_usd_bn", transform="当期汇率 M2 十亿美元余额 ÷ 1000，转为万亿美元")
    additions[2].update(sourceKey="gold_price, total_fixed_usd_bn", transform="金价 ÷ 固定汇率 M2，再除以 2020-12 的同口径比值 × 100")
    yoy_metric = metric("liquidity_yoy", "固定汇率 M2 同比", "%", money_yoy_rows, "monthly", "Fed / PBoC / ECB · 同比派生", "https://data.ecb.europa.eu/data/datasets/BSI/BSI.M.U2.N.V.M20.X.1.U2.2300.Z01.E", "research/liquidity_monthly.csv", "m2_fixed_fx_yoy_pct", liquidity_comparison["note"], "货币背景 · 不计分", None, None, "liquidity")
    yoy_metric.update(sourceKey="total_fixed_usd_bn", transform="(当月固定汇率M2十亿美元余额 ÷ 12个月前同口径余额 − 1) × 100；与黄金同比采用同月、连续13个月完整样本")
    additions.append(yoy_metric)
    snapshot["liquidityComparison"] = liquidity_comparison
    references = [
        ("silver", "白银现货", "美元/盎司", "daily", "SPTAGUSDOZ.IDC", "Wind · 伦敦银现", "与黄金同为美元/金衡盎司；只作关联资产参考，不参与黄金驱动计分。"),
        ("gold_silver_ratio", "金银比", "倍", "daily", "gold_silver_ratio", "Wind · 同日金银价格", "同日金价 ÷ 银价；上行表示黄金相对白银更强，不能直接解释为贵或便宜。"),
        ("copper", "铜 · 月度均价", "美元/吨", "monthly", "PCOPPUSDM", "IMF · FRED", "月度均价，节奏与金属日收盘不同；仅参考商品走势，不参与黄金驱动计分。"),
        ("bitcoin", "比特币", "美元/BTC", "daily", "CBBTCUSD", "Coinbase · FRED", "7 天交易，报价时点为 5 PM PST；历史联动会变化，仅作走势参考。"),
    ]
    for mid, name, unit, frequency, code, source, note in references:
        rows = []
        for row in asset_rows:
            raw = row.get(mid)
            if not raw:
                if mid == "bitcoin" and metadata[mid]["first_date"] <= row["date"] <= metadata[mid]["last_date"]:
                    rows.append([row["date"], None])
                continue
            value = float(raw)
            if row["date"] <= today.isoformat():
                rows.append([row["date"], value])
        relation = None
        pair = study["pairs"].get("gold_" + mid)
        if pair:
            weekly = pair["weekly"]
            rolling_asset = weekly["rolling"]["52"]
            relation = {"value": rolling_asset[-1][1], "date": rolling_asset[-1][0], "rows": rolling_asset,
                        "window": "52 个非重叠周度样本：资产与黄金的同期对数收益；共同周五，缺失时允许共同周四", "expected": "同期历史联动，不是因果驱动", "frequency": "weekly"}
        change_text = None
        valid_rows = [row for row in rows if row[1] is not None]
        if len(valid_rows) >= 22 and frequency == "daily":
            change_text = f"近 21 个有效观测 {((valid_rows[-1][1]/valid_rows[-22][1])-1)*100:+.1f}%"
        if mid == "copper" and len(rows) >= 13:
            change_text = f"同比 {((rows[-1][1]/rows[-13][1])-1)*100:+.1f}% · 完整月均价"
        source_url = metadata.get(mid, {}).get("definition_url", "https://fred.stlouisfed.org/series/" + code if mid in {"copper", "bitcoin"} else "")
        additions.append(metric(mid, name, unit, rows, frequency, source, source_url, "research/assets_daily.csv", mid, note, "关联资产 · 只作参考", relation, change_text, "assets"))
    snapshot["metrics"].extend(additions)
    snapshot.pop("valuation", None)
    snapshot["research"] = {"liquidity": {key: research[key] for key in ("results", "lags", "periods", "method", "priceSource")},
                            "liquiditySources": research["liquiditySources"], "assetSources": research["assetSources"],
                            "assetStudy": {key: {"weekly": {"fixed": value["weekly"]["fixed"]}, "annual_weekly": value["annual_weekly"], "daily_lag_sensitivity": value["daily_lag_sensitivity"]} for key, value in study["pairs"].items()},
                            "oldValuation": {"status": "口径断点，停止展示历史分位", "breakDate": "2024-01", "denominator": "美国 M2 + (中国、欧元区、英国、日本折美元 M2)/3", "numerator": "金价 × 全球黄金矿产储量 / 10000", "unitConcern": "公式未将吨换算为金衡盎司；不是黄金市值/M2的经济比例", "source": "2609 工作簿 · 中长期估值指标 I/J/K 列"}}
    return snapshot
