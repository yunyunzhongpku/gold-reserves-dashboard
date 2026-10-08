"""Build a reviewable design preview from saved gold data, without publishing."""

import json
import hashlib
import math
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from scripts import build_site as b
from scripts.asset_tabs import build_asset_page
from extend_preview import extend_snapshot


def series(rows, key, today):
    cutoff = b.shift_months(today, -120)
    return [
        [row["date"], row.get(key) if b.is_number(row.get(key)) and math.isfinite(row[key]) else None]
        for row in rows if cutoff <= date.fromisoformat(row["date"]) <= today
    ]


def make_snapshot(today=None):
    today = today or date.today()
    d = b.read_dashboard_data(today=today)
    if any(item["latest"].get("date") and date.fromisoformat(item["latest"]["date"]) > today
           for item in d["layers"]):
        raise ValueError("来源包含快照日期之后的最新判断；不生成带未来信息的预览")
    layers = {item["id"]: item for item in d["layers"]}
    rels = {item["id"]: item for item in d["relationships"]}
    position = layers["positioning_technical"]
    sub_rels = {item["id"]: item for item in rels["positioning_technical"]["sub_metrics"]}
    cot = b.read_csv_rows(b.COT_FILE, ["managed_money_net"], max_date=today)
    gvz = b.read_csv_rows(b.WIND_DAILY_FILE, ["gvz"], max_date=today)
    technical = d["technical_layer"]
    gap_rows = [
        {**row, "gap_ma200_pct": (row["gold_price"] / row["ma200"] - 1) * 100
         if b.is_number(row.get("ma200")) and row["ma200"] else None}
        for row in technical["chart_rows"]
    ]
    specs = [
        ("real_rate", "10 年实际利率", "机会成本", "%", "line", layers["real_rate"]["chart_rows"], "real_rate", layers["real_rate"], rels["real_rate"], "近 21 个有效观测", "实际利率上升时，持有无息黄金的机会成本通常增加。", "FRED · DFII10", "https://fred.stlouisfed.org/series/DFII10", "data/market/fred_dfii10.csv"),
        ("dollar", "美元指数", "计价因素", "点", "line", layers["dollar"]["chart_rows"], "dollar_index", layers["dollar"], rels["dollar"], "近 21 个有效观测", "美元走强通常增加美元计价黄金的压力。", "Wind · USDX.FX", "", "data/market/wind_daily.csv"),
        ("inflation_expectation", "10 年通胀补偿", "通胀预期代理", "%", "line", layers["inflation_expectation"]["chart_rows"], "breakeven_10y", layers["inflation_expectation"], rels["inflation_expectation"], "近 21 个有效观测", "名义国债与 TIPS 的收益率差，作为市场通胀预期的代理。", "FRED · T10YIE", "https://fred.stlouisfed.org/series/T10YIE", "data/market/fred_t10yie.csv"),
        ("official_reserves", "中国央行黄金储备净增", "官方部门 · 月频", "吨", "bar", d["reserve_rows"], "china_mom_change", layers["official_reserves"], rels["central_bank_purchases"], "较上月储备存量", "储备存量的月度净增，作为官方购金代理；不代表全球购金总量。", "SAFE · 官方储备资产", "https://www.safe.gov.cn/safe/2026/0206/27116.html", "data/market/official_reserves_manual.csv"),
        ("etf", "SPDR + iShares 黄金持仓", "资金需求代理", "吨", "line", position["chart_rows"], "etf_total", position, sub_rels["etf_holdings"], "近 21 个有效观测", "两只基金的合计持仓变化，不代表全球黄金 ETF 总流量。", "Wind · SPDR / iShares", "", "data/market/wind_supplemental.csv"),
        ("cot", "CFTC 资金管理者净多", "期货仓位 · 周频", "张", "line", cot, "managed_money_net", position, sub_rels["managed_money"], "近 4 个周度观测", "Managed Money 多仓减空仓；持仓扩张和拥挤风险需要分别观察。", "CFTC · 黄金 COT", "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm", "data/market/cftc_gold_cot.csv"),
        ("gvz", "黄金隐含波动率", "风险背景 · 日频", "点", "line", gvz, "gvz", position, sub_rels["gvz"], "", "波动预期反映风险水平，不直接给出金价涨跌方向。", "Wind · GVZ", "", "data/market/wind_daily.csv"),
        ("epu", "经济政策不确定性", "风险背景 · 月频", "点", "line", layers["epu"]["chart_rows"], "epu", layers["epu"], rels["epu"], "近 3 个完整月", "政策不确定性代理，月度背景观察，不参与驱动合成。", "Wind EDB · G1060817", "", "data/market/wind_supplemental.csv"),
        ("gpr", "地缘政治风险", "风险背景 · 月频", "点", "line", layers["gpr"]["chart_rows"], "gpr", layers["gpr"], rels["gpr"], "近 3 个完整月", "地缘政治风险代理，月度背景观察，不参与驱动合成。", "Wind EDB · L2715907", "", "data/market/wind_supplemental.csv"),
        ("china_stock", "中国官方黄金储备总量", "官方部门 · 月频", "吨", "line", d["reserve_rows"], "china_reserves", layers["official_reserves"], None, "", "这是黄金储备存量，与月度净增分别观察。", "SAFE · 官方储备资产", "https://www.safe.gov.cn/safe/2026/0206/27116.html", "data/market/official_reserves_manual.csv"),
        ("global_stock", "全球官方黄金储备总量", "官方部门 · 月频", "吨", "line", d["reserve_rows"], "global_reserves", layers["official_reserves"], None, "", "按全球储备序列自己的观测日显示；不能视为同期全球购金需求。", "Wind EDB · L8751203", "", "data/market/wind_supplemental.csv"),
        ("price_trend", "金价相对 200 日均线的偏离", "价格趋势 · 不计分", "%", "line", gap_rows, "gap_ma200_pct", technical, rels["price_trend"], "", "当前金价除以 200 日均线再减一，以百分比显示；关系检验使用已成熟的未来收益。", "Wind · 金价派生", "", "data/market/wind_daily.csv"),
    ]
    metrics = []
    for mid, name, category, unit, kind, rows, key, layer, rel, window, note, source, url, file in specs:
        values = series(rows, key, today)
        valid = [point for point in values if point[1] is not None]
        last = valid[-1] if valid else [None, None]
        frequency = "weekly" if mid == "cot" else "monthly" if mid in {"official_reserves", "epu", "gpr", "china_stock", "global_stock"} else "daily"
        quality, lag = b.classify_staleness(last[0], today, frequency)
        state = layer["state"]
        delta = layer.get("change")
        since = layer.get("change_since")
        if mid == "official_reserves":
            delta = None
        if mid in {"etf", "cot"}:
            state = position["sub_states"][mid]
            delta, since = b.change_over_observations(rows, key, 4 if mid == "cot" else 21)
        if mid in {"gvz", "epu", "gpr", "china_stock", "global_stock", "price_trend"}:
            state = "context"
        if mid in {"gvz", "china_stock", "global_stock", "price_trend"}:
            delta = None
        relation = None
        if rel:
            window_note = (
                "200 日均线偏离与未来 63 个有效观测收益；252 个成熟样本滚动相关"
                if mid == "price_trend" else
                "63 个配对观测的指标变化与同期黄金收益；252 个变化样本滚动相关"
                if rel.get("medium_term") else
                "3 个月储备净增与同期黄金收益；24 个月变化样本滚动相关"
                if mid == "official_reserves" else
                "3 个月指标变化与同期黄金收益；24 个月变化样本滚动相关"
                if mid in {"epu", "gpr"} else
                "21 个周度配对观测的净多变化与同期黄金收益；52 个变化样本滚动相关"
                if mid == "cot" else
                "21 个配对观测的指标变化与同期黄金收益；52 个变化样本滚动相关"
            )
            corr_values = series(rel["rolling_corr"], "corr", today)
            relation = {"value": rel["latest_corr"], "date": corr_values[-1][0] if corr_values else None,
                        "rows": corr_values, "window": window_note, "expected": rel["expected"]}
            if rel.get("short_term"):
                short_values = series(rel["short_term"]["rolling_corr"], "corr", today)
                relation["short"] = {
                    "value": rel["short_term"]["latest_corr"],
                    "date": short_values[-1][0] if short_values else None,
                    "rows": short_values,
                    "window": "200 日均线偏离与未来 21 个有效观测收益；126 个成熟样本滚动相关"
                    if mid == "price_trend" else "21 个配对观测的指标变化与同期黄金收益；126 个变化样本滚动相关",
                }
        files = [file]
        if mid in {"official_reserves", "china_stock", "global_stock", "etf", "epu", "gpr"}:
            files.append(str(b.DATA_FILE.relative_to(ROOT)))
        metrics.append({"id": mid, "name": name, "category": category, "unit": unit, "kind": kind,
                        "rows": values, "value": last[1], "date": last[0], "quality": quality, "lag": lag,
                        "frequency": frequency, "state": state, "delta": delta, "since": since,
                        "window": window, "note": note, "source": source, "sourceUrl": url,
                        "file": " + ".join(files), "key": key, "relation": relation})
    states = [l["state"] for l in d["driver_layers"] if l.get("data_quality") not in {"missing", "very-stale"} and l["state"] in {"supportive", "headwind", "neutral"}]
    snapshot = {"asOf": technical["latest"]["date"], "snapshotDate": today.isoformat(),
                "builtAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "metrics": metrics, "gold": {"name": "黄金现货", "unit": "美元/盎司", "kind": "line", "frequency": "daily",
                "rows": series(technical["chart_rows"], "gold_price", today), "value": technical["latest"]["gold_price"],
                "date": technical["latest"]["date"], "latest": technical["latest"], "technical": technical["technical"],
                "averages": {key: series(technical["chart_rows"], key, today) for key in ("ma20", "ma60", "ma200")}},
                "environment": {"label": d["posture"], "state": d["posture_state"], "active": d["active_layers"],
                "supportive": states.count("supportive"), "headwind": states.count("headwind"), "neutral": states.count("neutral"),
                "groups": {layer["id"]: {"name": layer["name"], "state": layer["state"] if layer.get("data_quality") not in {"missing", "very-stale"} else "missing"} for layer in d["driver_layers"]}},
                "changes": d["recent_changes"], "valuation": d["valuation"], "rules": [
                    "五组为实际利率、美元、通胀补偿、中国储备净增、资金与仓位；ETF 与 CFTC 在组内先合成一票。",
                    "支持 +1、中性 0、压力 −1；有效组投票之和除以有效组数，≥ +0.25 为偏多，≤ −0.25 为承压，其余中性。",
                    "缺失和严重滞后剔除；滞后观测仍可计入。有效组少于 3 组时显示数据不足。五组等权仅作启发式观察，未验证为交易模型。",
                    *b.make_driver_method_contracts()],
                "cotPercentile": position["latest"]["managed_money_net_percentile"]}
    snapshot = extend_snapshot(snapshot, b)
    yoy = json.loads((HERE / "research/yoy_study.json").read_text())
    if yoy["asOf"] != snapshot["snapshotDate"] or any(hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() != digest for filename, digest in yoy["inputHashes"].items()):
        raise ValueError("同比研究日期或输入已变更；请先重新生成同比研究")
    if [row[0][:7] for row in snapshot["liquidityComparison"]["goldRows"]] != yoy["overlapping"]["targetMonths"]:
        raise ValueError("同比图与研究共同月份不一致；请先复核缺失处理和研究样本")
    snapshot["research"]["yoyStudy"] = {key: yoy[key] for key in ("overlapping", "annual", "method")}
    return snapshot


def main():
    snapshot = make_snapshot()
    payload = json.dumps(snapshot, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("<", "\\u003c")
    template = (HERE / "preview.html").read_text(encoding="utf-8")
    html = template.replace("__STYLE__", (HERE / "preview.css").read_text(encoding="utf-8"))
    html = html.replace("__DATA__", payload).replace("__SCRIPT__", (HERE / "preview.js").read_text(encoding="utf-8"))
    (HERE / "2026-10-08_黄金跟踪_设计预览.html").write_text(html, encoding="utf-8")
    combined = build_asset_page(html, (ROOT / "data/etf_tracking.html").read_text(encoding="utf-8"))
    (HERE / "index.html").write_text(combined, encoding="utf-8")
    (HERE / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(f"设计预览已生成：{HERE / 'index.html'}；正式页面与数据未改动。")


if __name__ == "__main__":
    main()
