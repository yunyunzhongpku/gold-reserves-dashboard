# -*- coding: utf-8 -*-
"""只读关联资产研究：保存来源快照，按共同日期端点计算非重叠收益。"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import json
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RAW = HERE / "raw"
AS_OF = date(2026, 10, 8)
BEGIN = date(2016, 9, 1)
COMPLETE_MARKET_END = date(2026, 10, 7)
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CBBTCUSD&cosd=2016-09-01&coed=2026-10-07"
FRED_DEFINITION = "https://fred.stlouisfed.org/series/CBBTCUSD"
COPPER_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PCOPPUSDM&cosd=2016-09-01&coed=2026-10-07"
COINBASE_DOC = "https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles"
WIND_OPTIONS = "Days=Trading;TradingCalendar=NYSE;Fill=Blank"
ASSETS = ("gold", "silver", "bitcoin")
LABELS = {"gold": "黄金", "silver": "白银", "bitcoin": "比特币", "copper": "铜月度均价"}
PAIR_DEFS = (("gold", "silver"), ("gold", "bitcoin"), ("silver", "bitcoin"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")


def positive(value):
    if value in (None, "", "."):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def shift_months(day, months):
    serial = day.year * 12 + day.month - 1 + months
    year, month = divmod(serial, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def refresh_silver():
    from tkf_wind import w
    w.start()
    name = w.wss("SPTAGUSDOZ.IDC", "sec_name", "")
    if getattr(name, "ErrorCode", -1) != 0 or name.Data[0][0] != "伦敦银现":
        raise RuntimeError("白银代码名称核对失败")
    result = w.wsd("SPTAGUSDOZ.IDC", "close", BEGIN.isoformat(), COMPLETE_MARKET_END.isoformat(), WIND_OPTIONS)
    if getattr(result, "ErrorCode", -1) != 0:
        raise RuntimeError(f"Wind 白银历史查询失败，ErrorCode={result.ErrorCode}")
    rows = [{"date": str(t)[:10], "silver_usd_oz": positive(value)} for t, value in zip(result.Times, result.Data[0])]
    write_json(RAW / "silver_wind.json", {"provider": "Wind/TkfWindPy", "code": "SPTAGUSDOZ.IDC", "verified_sec_name": name.Data[0][0], "field": "close", "options": WIND_OPTIONS, "request_start": BEGIN.isoformat(), "request_end": COMPLETE_MARKET_END.isoformat(), "retrieved_at": datetime.now(timezone.utc).isoformat(), "rows": rows})


def refresh_bitcoin():
    # 本机已安装 requests；不读取密钥，不要求 FRED API key。
    import requests
    response = requests.get(FRED_URL, timeout=45)
    response.raise_for_status()
    records = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    if not records or "CBBTCUSD" not in records[0]:
        raise RuntimeError("FRED 返回内容不是 CBBTCUSD 数据 CSV")
    (RAW / "bitcoin_fred.csv").write_bytes(response.content)
    write_json(RAW / "bitcoin_fred_receipt.json", {"url": FRED_URL, "retrieved_at": datetime.now(timezone.utc).isoformat(), "provider": "Coinbase via FRED", "series": "CBBTCUSD", "frequency": "Daily 7-Day", "units": "U.S. dollars per bitcoin", "definition_url": FRED_DEFINITION, "time_definition": "All data is as of 5 PM PST. FRED does not explicitly document daylight-saving treatment in this note.", "sha256": digest(RAW / "bitcoin_fred.csv")})


def refresh_copper():
    import requests
    response = requests.get(COPPER_URL, timeout=45)
    response.raise_for_status()
    records = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    if not records or "PCOPPUSDM" not in records[0]:
        raise RuntimeError("FRED 返回内容不是 PCOPPUSDM 数据 CSV")
    (RAW / "copper_fred.csv").write_bytes(response.content)
    write_json(RAW / "copper_fred_receipt.json", {"url": COPPER_URL, "retrieved_at": datetime.now(timezone.utc).isoformat(), "provider": "IMF via FRED", "series": "PCOPPUSDM", "frequency": "Monthly", "units": "U.S. dollars per metric tonne", "definition_url": "https://fred.stlouisfed.org/series/PCOPPUSDM", "sha256": digest(RAW / "copper_fred.csv")})


def load_sources():
    anchor = ROOT / "data/market/wind_daily.csv"
    copied = RAW / "gold_anchor_wind_daily.csv"
    # 拷贝已保存正式输入作为研究锚；不刷新、不覆盖正式输入。
    if not copied.exists():
        copied.write_bytes(anchor.read_bytes())
    gold, dollar, silver, bitcoin, copper = {}, {}, {}, {}, {}
    seen = set()
    with copied.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            day = date.fromisoformat(row["date"])
            if not BEGIN <= day <= AS_OF:
                continue
            if day in seen:
                raise ValueError(f"黄金原始输入日期重复：{day}")
            seen.add(day)
            gold[day] = positive(row.get("gold_price"))
            dollar[day] = positive(row.get("dollar_index"))
    silver_raw = json.loads((RAW / "silver_wind.json").read_text(encoding="utf-8"))
    for row in silver_raw["rows"]:
        day = date.fromisoformat(row["date"])
        if BEGIN <= day <= AS_OF:
            if day in silver:
                raise ValueError(f"白银日期重复：{day}")
            silver[day] = positive(row["silver_usd_oz"])
    with (RAW / "bitcoin_fred.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            day = date.fromisoformat(row.get("observation_date") or row["DATE"])
            if BEGIN <= day <= AS_OF:
                if day in bitcoin:
                    raise ValueError(f"比特币日期重复：{day}")
                bitcoin[day] = positive(row["CBBTCUSD"])
    if not (RAW / "bitcoin_fred_receipt.json").exists():
        write_json(RAW / "bitcoin_fred_receipt.json", {"url": FRED_URL, "retrieved_at": datetime.fromtimestamp((RAW / "bitcoin_fred.csv").stat().st_mtime, timezone.utc).isoformat(), "provider": "Coinbase via FRED", "series": "CBBTCUSD", "frequency": "Daily 7-Day", "units": "U.S. dollars per bitcoin", "definition_url": FRED_DEFINITION, "time_definition": "All data is as of 5 PM PST. Source note does not specify daylight-saving treatment.", "sha256": digest(RAW / "bitcoin_fred.csv")})
    with (RAW / "copper_fred.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            label = date.fromisoformat(row.get("observation_date") or row["DATE"])
            # FRED 日期为统计月份首日；统一用已完成月末标记统计期，不伪装成日度报价。
            period_end = date(label.year, label.month, calendar.monthrange(label.year, label.month)[1])
            if BEGIN <= period_end <= COMPLETE_MARKET_END:
                if period_end in copper:
                    raise ValueError(f"铜统计月份重复：{period_end}")
                copper[period_end] = positive(row["PCOPPUSDM"])
    return {"gold": gold, "silver": silver, "bitcoin": bitcoin, "copper": copper, "dollar_index": dollar}


def pearson(x, y):
    if len(x) < 3 or len(x) != len(y):
        return None
    xm, ym = statistics.mean(x), statistics.mean(y)
    numerator = sum((a - xm) * (b - ym) for a, b in zip(x, y))
    denominator = math.sqrt(sum((a - xm) ** 2 for a in x) * sum((b - ym) ** 2 for b in y))
    return numerator / denominator if denominator else None


def ranks(values):
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    result = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + end - 1) / 2 + 1
        for position, _ in ordered[index:end]:
            result[position] = rank
        index = end
    return result


def describe_returns(rows):
    if not rows:
        return {"n": 0, "start": None, "end": None, "pearson": None, "spearman": None}
    left, right = [r["left_return"] for r in rows], [r["right_return"] for r in rows]
    return {"n": len(rows), "start": rows[0]["date"], "end": rows[-1]["date"], "pearson": pearson(left, right), "spearman": pearson(ranks(left), ranks(right))}


def common_endpoints(series, left, right, frequency):
    days = sorted(day for day in series[left].keys() & series[right].keys() if series[left][day] is not None and series[right][day] is not None)
    if frequency == "daily":
        return [{"period": day.isoformat(), "date": day.isoformat(), "left_price": series[left][day], "right_price": series[right][day]} for day in days]
    groups = {}
    for day in days:
        if frequency == "weekly":
            if day.weekday() > 4:
                continue
            end = day + timedelta(days=4 - day.weekday())
            # 只接受已完成周的周五或周四共同报价，不将周中当作一整周。
            if end > COMPLETE_MARKET_END or (end - day).days > 1:
                continue
        else:
            end = date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
            if end > COMPLETE_MARKET_END or (end - day).days > 4:
                continue
        groups[end] = {"period": end.isoformat(), "date": day.isoformat(), "left_price": series[left][day], "right_price": series[right][day]}
    return [groups[key] for key in sorted(groups)]


def returns_from_endpoints(points, frequency):
    rows = []
    for prior, current in zip(points, points[1:]):
        prior_day, current_day = date.fromisoformat(prior["date"]), date.fromisoformat(current["date"])
        if frequency == "daily" and (current_day - prior_day).days > 7:
            continue
        if frequency == "weekly" and (date.fromisoformat(current["period"]) - date.fromisoformat(prior["period"])).days != 7:
            continue
        if frequency == "monthly" and shift_months(date.fromisoformat(prior["period"]), 1).replace(day=1) != date.fromisoformat(current["period"]).replace(day=1):
            continue
        rows.append({"date": current["date"], "period": current["period"], "previous_date": prior["date"], "left_return": math.log(current["left_price"] / prior["left_price"]), "right_return": math.log(current["right_price"] / prior["right_price"]), "calendar_days": (current_day - prior_day).days})
    return rows


def rolling(rows, window):
    output = []
    for index in range(window - 1, len(rows)):
        section = rows[index - window + 1:index + 1]
        value = pearson([r["left_return"] for r in section], [r["right_return"] for r in section])
        if value is not None:
            output.append([rows[index]["date"], value])
    return output


def analyze_pair(series, left, right):
    out = {"left": left, "right": right}
    for freq in ("daily", "weekly", "monthly"):
        endpoints = common_endpoints(series, left, right, freq)
        returns = returns_from_endpoints(endpoints, freq)
        windows = {label: describe_returns([row for row in returns if date.fromisoformat(row["date"]) >= shift_months(AS_OF, -months)]) for label, months in (("1y", 12), ("3y", 36), ("5y", 60), ("10y", 120))}
        sizes = {"daily": (63, 126), "weekly": (36, 52), "monthly": (24, 36)}[freq]
        out[freq] = {"definition": "共同日期端点的非重叠对数收益；无前向填充或插值", "endpoints": endpoints, "returns": returns, "fixed": windows, "rolling": {str(window): rolling(returns, window) for window in sizes}}
    daily = out["daily"]["returns"]
    sensitivity = {}
    for lag in (-1, 0, 1):
        left_values, right_values = [], []
        for index, row in enumerate(daily):
            shifted = index + lag
            if 0 <= shifted < len(daily) and date.fromisoformat(row["date"]) >= shift_months(AS_OF, -120):
                left_values.append(row["left_return"])
                right_values.append(daily[shifted]["right_return"])
        sensitivity[str(lag)] = {"n": len(left_values), "pearson": pearson(left_values, right_values), "definition": f"右侧资产对数收益位移 {lag:+d} 个共同观测；用于日切敏感性，不能解释为预测因果"}
    out["daily_lag_sensitivity"] = sensitivity
    years = {}
    for year in range(2017, 2027):
        section = [row for row in out["weekly"]["returns"] if date.fromisoformat(row["date"]).year == year]
        if len(section) >= 20:
            years[str(year)] = describe_returns(section)
    out["annual_weekly"] = years
    return out


def fmt(value, digits=2):
    return "—" if value is None else f"{value:.{digits}f}"


def build_report(manifest, study):
    latest_ratio = manifest["gold_silver_ratio"]
    lines = ["# 黄金关联资产研究：白银、铜与比特币", "", "整理日期：2026-10-08。仅本地设计研究，未刷新正式黄金输入或发布。", "", "## 问题与方法", "", "比较同为美元计价资产的收益率联动，不用价格水平相关判断替代、避险或预测能力。白银与黄金的现货价格均以美元／金衡盎司计价；比特币以美元／枚计价，只有收益率可跨单位比较。金银比使用同日、同单位的黄金价格除以白银价格。铜仅作为月度均价趋势参考，不评分，也不把月均价与金价日度收盘混算相关。", "", "日度：先找双方共同日期，再从共同的前一期到当期计算对数收益，避免比特币 7 天交易与金属交易日历形成不同起止区间。周度：每个已完成自然周取周五的共同日期；周五休市时允许周四，不接受更早观测。月度：只取已完成月份最后四个自然日内的最后共同日期，剔除未结束月份。收益均为非重叠区间；滚动相关窗口之间仍重叠。", "", "黄金 Wind close 的精确报价钟点未核实。BTC 官方说明为 5 PM PST，未明确说明夏令时处理；共同日期表示同日标签，不能保证同一时刻。日度作为补充，周度与月度作为较稳妥的主要观察。来源：[FRED CBBTCUSD 官方定义](https://fred.stlouisfed.org/series/CBBTCUSD)，Wind 请求字段与选项详见 asset_sources.json。", "", "## 来源、覆盖与最新值", "", "|资产|主来源|最新观测或统计期|最新值|有效观测范围与数量|", "|---|---|---|---:|---|"]
    for key in (*ASSETS, "copper"):
        item = manifest["assets"][key]
        lines.append(f"|{LABELS[key]}|{item['provider']} / {item['code']}|{item['last_date']}|{fmt(item['latest_value'])} {item['unit']}|{item['first_date']} — {item['last_date']}；{item['valid_count']} 条|")
    lines.extend(["", f"最新同日金银比为 **{fmt(latest_ratio['latest_value'])}**，观测日 **{latest_ratio['last_date']}**；金价 {fmt(latest_ratio['gold'])} 与银价 {fmt(latest_ratio['silver'])} 均为当天美元／盎司。银价最新一天更晚时，不与旧金价拼成更新比值。", "", "铜：Wind 三个候选代码的名称字段均为空，未将返回错误码 0 视为身份核实成功。退回 [IMF / FRED PCOPPUSDM](https://fred.stlouisfed.org/series/PCOPPUSDM) 的全球铜月度均价，单位美元／公吨，属于名义价格的期间均值，不是 LME 3 个月合约日度收盘。原始 CSV 用每月首日标记统计月份，展示数据将统计期规范为已完成月末；未向日度前向填充。最新可用是 2026 年 7 月，距快照统计月末 69 天，明确标为滞后。", "", "## 收益率相关", "", "以下均为 Pearson 相关，括号内为非重叠收益样本数；固定区间截至快照日 2026-10-08，尾部按各频率最新已完成共同观测截止。铜不进入下面的日／周相关，避免月均价与日度收盘口径混算。", "", "|资产对 / 频率|近 1 年|近 3 年|近 5 年|近 10 年|", "|---|---:|---:|---:|---:|"])
    for pair in study["pairs"].values():
        name = f"{LABELS[pair['left']]} / {LABELS[pair['right']]}"
        for freq, flabel in (("daily", "日度"), ("weekly", "周度"), ("monthly", "月度")):
            values = [f"{fmt(pair[freq]['fixed'][label]['pearson'])}（{pair[freq]['fixed'][label]['n']}）" for label in ("1y", "3y", "5y", "10y")]
            lines.append(f"|{name} · {flabel}|" + "|".join(values) + "|")
    lines.extend(["", "研究实际尾部：黄金／白银与黄金／比特币日度截至 2026-10-06，白银／比特币日度截至 2026-10-07；三对周度均截至 2026-10-02，完整月度均截至 2026-09-30。FRED 比特币 2020-09-04 保留为缺失，未以零或旧值填补。", "", "计算证据：assets_daily.csv 为原始观测的日期并集，空白即缺失；asset_study.json 保存共同端点、逐期对数收益、固定窗口结果及滚动相关。运行 analyze_assets.py 可从已保存原始快照复算。", "", "## 稳健性与阶段差异", ""])
    for pair in study["pairs"].values():
        name = f"{LABELS[pair['left']]} / {LABELS[pair['right']]}"
        r52 = pair["weekly"]["rolling"]["52"]
        r36 = pair["weekly"]["rolling"]["36"]
        fixed = pair["weekly"]["fixed"]["10y"]
        annual = pair["annual_weekly"]
        high = max(annual.items(), key=lambda item: item[1]["pearson"])
        low = min(annual.items(), key=lambda item: item[1]["pearson"])
        lines.append(f"- {name}：近 10 年周度 Pearson {fmt(fixed['pearson'])}、Spearman {fmt(fixed['spearman'])}。最新 52 周相关 {fmt(r52[-1][1] if r52 else None)}、36 周相关 {fmt(r36[-1][1] if r36 else None)}；52 周历史范围 {fmt(min(p[1] for p in r52))} 至 {fmt(max(p[1] for p in r52))}。按年份周度结果最低 {low[0]} 年 {fmt(low[1]['pearson'])}、最高 {high[0]} 年 {fmt(high[1]['pearson'])}（2026 年仅截至最新观测，不是完整年）。")
        sensitivity = pair["daily_lag_sensitivity"]
        lines.append(f"  日切敏感性：右侧收益位移 −1 / 0 / +1 个共同观测时相关分别 {fmt(sensitivity['-1']['pearson'])} / {fmt(sensitivity['0']['pearson'])} / {fmt(sensitivity['1']['pearson'])}；位移结果仅提示钟点和交易节奏可能影响日度相关，不能用作预测结论。")
    lines.extend(["", "## 数据质量与解释边界", "", "- 日期键唯一；价格要求正值且有限；未对缺失或周末使用前向填充，未把新来源替换进正式数据。", "- 周度与月度端点逐对选择共同观测日；同一资产在不同资产对中可能有不同端点。逐期日期保存在研究 JSON，可以复核。", "- 最新银价与比特币观测可晚于已保存金价；三资产共同的日度研究截止日期、周度末端和月度末端分别标注，不借用更晚的其他资产日期。", "- 不同计价单位的价格水平图只用于各自走势；跨资产比较应使用收益率或明确基准日归一化。金银比才具有同单位相除的清楚口径。", "- 相关系数仅为样本描述；未新增显著性检验、交易策略、组合权重或历史时点因果模型。白银还受工业需求影响，比特币还受自身市场机制影响，不能预设其与黄金一直高度相关。", "- FRED 记录该 Coinbase 数据的复制限制；本产物保留为本地研究，不据此认定可对外发布原始序列。官方说明链接保存在来源清单。", "- Coinbase UTC 日 K 只保留单独的短样本用于核查接口，不与 FRED 主序列混拼。其 daily close 定义参见[官方 API 文档](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles)。", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-silver", action="store_true")
    parser.add_argument("--refresh-bitcoin", action="store_true")
    parser.add_argument("--refresh-copper", action="store_true")
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    if args.refresh_silver:
        refresh_silver()
    if args.refresh_bitcoin:
        refresh_bitcoin()
    if args.refresh_copper:
        refresh_copper()
    series = load_sources()
    days = sorted(set().union(*(values.keys() for values in series.values())))
    all_rows = []
    for day in days:
        row = {"date": day.isoformat(), **{key: series[key].get(day) for key in (*ASSETS, "copper", "dollar_index")}}
        row["gold_silver_ratio"] = row["gold"] / row["silver"] if row["gold"] is not None and row["silver"] is not None else None
        all_rows.append(row)
    with (HERE / "assets_daily.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("date", "gold", "silver", "bitcoin", "gold_silver_ratio", "dollar_index", "copper"), lineterminator="\n")
        writer.writeheader()
        writer.writerows(all_rows)
    metadata = {
        "gold": {"provider": "Wind 已保存生产输入", "code": "SPTAUUSDOZ.IDC", "unit": "美元/金衡盎司", "field": "close", "options": WIND_OPTIONS, "raw_file": "raw/gold_anchor_wind_daily.csv", "anchor_input": "data/market/wind_daily.csv", "quote_time": "Vendor close 精确钟点未核实；NYSE 仅为请求日历"},
        "silver": {"provider": "Wind/TkfWindPy", "code": "SPTAGUSDOZ.IDC", "verified_sec_name": "伦敦银现", "unit": "美元/金衡盎司", "field": "close", "options": WIND_OPTIONS, "raw_file": "raw/silver_wind.json", "quote_time": "Vendor close 精确钟点未核实；NYSE 仅为请求日历"},
        "bitcoin": {"provider": "Coinbase via FRED", "code": "CBBTCUSD", "unit": "美元/BTC", "frequency": "每日，7天", "raw_file": "raw/bitcoin_fred.csv", "download_url": FRED_URL, "definition_url": FRED_DEFINITION, "quote_time": "5 PM PST；官方备注未明确夏令时处理", "reproduction_note": "FRED notes: Reproduction of Coinbase data in any form is prohibited except with prior written permission of Coinbase. This artifact remains local research."},
        "copper": {"provider": "IMF via FRED", "code": "PCOPPUSDM", "unit": "美元/公吨", "frequency": "月频", "period_average": True, "raw_file": "raw/copper_fred.csv", "download_url": COPPER_URL, "definition_url": "https://fred.stlouisfed.org/series/PCOPPUSDM", "period_label": "原始首日标签规范为该统计月份月末；非日度收盘", "wind_candidate_audit": {"candidates": ["LMCAD03.LME", "LMCADY.LME", "LMCADS03.LME"], "ErrorCode": 0, "sec_name": None, "accepted": False}},
        "dollar_index": {"provider": "Wind 已保存生产输入", "code": "USDX.FX", "unit": "指数点", "raw_file": "raw/gold_anchor_wind_daily.csv", "anchor_input": "data/market/wind_daily.csv"},
    }
    for key, item in metadata.items():
        valid = [(day, value) for day, value in sorted(series[key].items()) if value is not None]
        item.update(first_date=valid[0][0].isoformat(), last_date=valid[-1][0].isoformat(), latest_value=valid[-1][1], valid_count=len(valid), missing_count=sum(value is None for value in series[key].values()), raw_sha256=digest(HERE / item["raw_file"]))
    ratio_rows = [row for row in all_rows if row["gold_silver_ratio"] is not None]
    last = ratio_rows[-1]
    manifest = {"as_of": AS_OF.isoformat(), "retrieved_at": datetime.now(timezone.utc).isoformat(), "scope": "仅设计原型研究，未修改正式输入或发布", "assets": metadata, "gold_silver_ratio": {"first_date": ratio_rows[0]["date"], "last_date": last["date"], "latest_value": last["gold_silver_ratio"], "gold": last["gold"], "silver": last["silver"], "definition": "同日美元/金衡盎司黄金价格 ÷ 白银价格；无填充", "valid_count": len(ratio_rows)}, "alignment": {"daily": "共同日期端点；相邻间隔超过7天则不算收益", "weekly": "已完成周的周五，缺失时允许周四；两资产使用同一日期", "monthly": "已完成月份最后4天内的最后共同日期；非重叠月度对数收益", "timezone_boundary": "同日标签不保证同一时刻；黄金/白银 vendor close 未验证精确钟点，BTC备注5 PM PST"}, "separate_check": {"provider": "Coinbase Exchange BTC-USD", "frequency": "UTC 日K", "definition_url": COINBASE_DOC, "raw_file": "raw/coinbase_btc_usd_sample.json", "main_series": False}}
    study = {"as_of": AS_OF.isoformat(), "method": manifest["alignment"], "pairs": {f"{left}_{right}": analyze_pair(series, left, right) for left, right in PAIR_DEFS}}
    write_json(HERE / "asset_sources.json", manifest)
    write_json(HERE / "asset_study.json", study)
    (HERE / "2026-10-08_关联资产_研究.md").write_text(build_report(manifest, study), encoding="utf-8")
    for key, pair in study["pairs"].items():
        fixed = pair["weekly"]["fixed"]
        print(key, "weekly", {window: {"r": round(item["pearson"], 4) if item["pearson"] is not None else None, "n": item["n"], "end": item["end"]} for window, item in fixed.items()})
    print("研究已保存：assets_daily.csv / asset_sources.json / asset_study.json / 2026-10-08_关联资产_研究.md")


if __name__ == "__main__":
    main()
