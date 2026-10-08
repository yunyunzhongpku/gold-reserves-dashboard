"""Rebuild a research-only US/China/euro-area M2 panel from documented sources.

No production data is changed. Run with --refresh-public to download public CSVs,
or --refresh-china-wind to query the verified Chinese M2 code. The default reads
saved raw data, so a research snapshot can be reproduced without network access.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
AS_OF = date(2026, 10, 8)
START = "2000-01"
FIXED_FX_MONTH = "2020-12"
PUBLIC = {
    "us_m2": ("M2NS", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=M2NS"),
    "cny_per_usd": ("DEXCHUS", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXCHUS"),
    "usd_per_eur": ("DEXUSEU", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXUSEU"),
    "ea_m2": ("BSI.M.U2.N.V.M20.X.1.U2.2300.Z01.E", "https://data-api.ecb.europa.eu/service/data/BSI/M.U2.N.V.M20.X.1.U2.2300.Z01.E?startPeriod=2000-01&endPeriod=2026-09&format=csvdata"),
}


def number(value):
    if value in (None, "", "."):
        return None
    result = float(value)
    if not math.isfinite(result):
        return None
    return result


def month_end(month):
    year, mm = map(int, month.split("-"))
    return date(year, mm, calendar.monthrange(year, mm)[1]).isoformat()


def raw_metadata(path, url, series):
    data = path.read_bytes()
    return {"file": str(path.relative_to(HERE)), "url": url, "series": series,
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def refresh_public():
    RAW.mkdir(exist_ok=True)
    for key, (series, url) in PUBLIC.items():
        # FRED's public CSV is deliberately requested without an artificial header.
        with urlopen(url, timeout=60) as response:
            payload = response.read()
            headers = {name: response.headers.get(name) for name in ("Date", "Last-Modified", "ETag", "Content-Type")}
        if len(payload) < 100 or b"<html" in payload[:500].lower():
            raise ValueError(f"{key}: response is not a data CSV")
        (RAW / f"{key}.csv").write_bytes(payload)
        (RAW / f"{key}_request.json").write_text(json.dumps({"url": url, "series": series, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "as_of_filter": AS_OF.isoformat(), "http_headers": headers}, ensure_ascii=False, indent=2), encoding="utf8")
        print(f"Saved {key}: {len(payload)} bytes")


def refresh_china_wind():
    from tkf_wind import w

    started = w.start()
    if getattr(started, "ErrorCode", 0):
        raise RuntimeError("Wind failed to start")
    # M0001384 is M2 balance; M0001385 is the growth rate and is not used.
    response = w.edb("M0001384", "2000-01-01", AS_OF.isoformat(), "Fill=Blank")
    if response.ErrorCode or len(response.Data) != 1 or not response.Times:
        raise RuntimeError("Wind Chinese M2 request failed or returned no series")
    rows = []
    for when, value in zip(response.Times, response.Data[0]):
        when = str(when)[:10]
        parsed = number(value)
        if when > AS_OF.isoformat() or parsed is None:
            continue
        if parsed <= 0:
            raise ValueError("Chinese M2 balance must be positive")
        rows.append({"date": when, "m2_cny_100m": parsed})
    if len(rows) < 240:
        raise ValueError("Insufficient Chinese M2 history")
    RAW.mkdir(exist_ok=True)
    with (RAW / "china_m2_wind.csv").open("w", newline="", encoding="utf8") as out:
        writer = csv.DictWriter(out, fieldnames=["date", "m2_cny_100m"])
        writer.writeheader()
        writer.writerows(rows)
    (RAW / "china_m2_request.json").write_text(json.dumps({
        "provider": "Wind EDB via tkf_wind", "series": "M0001384",
        "name": "中国:M2", "unit": "亿元人民币", "frequency": "月频",
        "seasonal_adjustment": "非季调", "start": "2000-01-01",
        "end": AS_OF.isoformat(), "options": "Fill=Blank",
        "error_code": response.ErrorCode, "response_fields": response.Fields,
        "response_codes": response.Codes, "valid_observations": len(rows),
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "code_primary_verification": "原2607工作簿Wind隐藏元数据：中国:M2，M0001384，单位亿元；root独立审计确认。",
        "code_documentation": "https://pdf.dfcfw.com/pdf/H3_AP202111081527725354_1.pdf",
        "unit_verification": "2026-08余额3568100亿元=356.81万亿元，与央行2026-08发布值核对；未取得Wind元数据的季调字段，非季调判断来自官方余额统计口径。",
    }, ensure_ascii=False, indent=2), encoding="utf8")


def parse_fred(key, daily=False, invert=False):
    result = {}
    sid = PUBLIC[key][0]
    with (RAW / f"{key}.csv").open(encoding="utf-8-sig", newline="") as src:
        for row in csv.DictReader(src):
            when = row["observation_date"]
            value = number(row[sid])
            month = when[:7]
            if month < START or when > AS_OF.isoformat() or value is None:
                continue
            if value <= 0:
                raise ValueError(f"{key} has a non-positive observation")
            # Daily series are reduced to the last published trading observation
            # in each month. No missing observation is forward-filled.
            if daily:
                if month_end(month) > AS_OF.isoformat():
                    continue
                if month in result and result[month][1] > when:
                    continue
            elif month_end(month) > AS_OF.isoformat():
                continue
            result[month] = (1 / value if invert else value, when)
    return result


def parse_ecb():
    result = {}
    with (RAW / "ea_m2.csv").open(encoding="utf-8-sig", newline="") as src:
        for row in csv.DictReader(src):
            month = row["TIME_PERIOD"]
            value = number(row["OBS_VALUE"])
            if month < START or month_end(month) > AS_OF.isoformat() or value is None:
                continue
            if (row["ADJUSTMENT"], row["UNIT"], row["UNIT_MULT"], row["DATA_TYPE"], row["BS_ITEM"]) != ("N", "EUR", "6", "1", "M20"):
                raise ValueError("ECB metadata changed: refuse to reinterpret the series")
            if value <= 0:
                raise ValueError("ECB M2 must be positive")
            if month in result:
                raise ValueError("ECB returned duplicate month")
            result[month] = (value / 1000, month_end(month))  # million to billion EUR
    return result


def parse_china():
    result = {}
    with (RAW / "china_m2_wind.csv").open(encoding="utf-8-sig", newline="") as src:
        for row in csv.DictReader(src):
            when = row["date"]
            month = when[:7]
            if month < START or month_end(month) > AS_OF.isoformat():
                continue
            value = number(row["m2_cny_100m"])
            if value is None:
                continue
            if value <= 0 or month in result:
                raise ValueError("Chinese M2 non-positive or duplicate month")
            result[month] = (value / 10, when)  # 100 million to billion CNY
    return result


def build_panel():
    us = parse_fred("us_m2")
    cn = parse_china()
    ea = parse_ecb()
    cn_fx = parse_fred("cny_per_usd", daily=True, invert=True)
    ea_fx = parse_fred("usd_per_eur", daily=True)
    for data in (us, cn, ea, cn_fx, ea_fx):
        if FIXED_FX_MONTH not in data:
            raise ValueError("Fixed-FX baseline month is missing")
    fixed_cn, fixed_ea = cn_fx[FIXED_FX_MONTH][0], ea_fx[FIXED_FX_MONTH][0]
    months = sorted(set(us) | set(cn) | set(ea))
    panel, lookup = [], {}
    for month in months:
        money = [us.get(month), cn.get(month), ea.get(month)]
        fx = [(1.0, month_end(month)), cn_fx.get(month), ea_fx.get(month)]
        row = {"date": month_end(month), "month": month,
               "us_m2_usd_bn": money[0][0] if money[0] else None,
               "cn_m2_cny_bn": money[1][0] if money[1] else None,
               "ea_m2_eur_bn": money[2][0] if money[2] else None,
               "usd_per_cny": fx[1][0] if fx[1] else None,
               "usd_per_eur": fx[2][0] if fx[2] else None,
               "cny_fx_date": fx[1][1] if fx[1] else None,
               "eur_fx_date": fx[2][1] if fx[2] else None,
               "us_period_basis": "monthly average", "cn_period_basis": "month end",
               "ea_period_basis": "month end"}
        complete = all(money) and all(fx)
        if complete:
            row["m2_current_fx_usd_bn"] = sum(m[0] * f[0] for m, f in zip(money, fx))
            row["m2_fixed_2020_12_fx_usd_bn"] = money[0][0] + money[1][0] * fixed_cn + money[2][0] * fixed_ea
            row["us_share_current_fx_pct"] = 100 * money[0][0] / row["m2_current_fx_usd_bn"]
            row["cn_share_current_fx_pct"] = 100 * money[1][0] * fx[1][0] / row["m2_current_fx_usd_bn"]
            row["ea_share_current_fx_pct"] = 100 * money[2][0] * fx[2][0] / row["m2_current_fx_usd_bn"]
        else:
            for field in ("m2_current_fx_usd_bn", "m2_fixed_2020_12_fx_usd_bn", "us_share_current_fx_pct", "cn_share_current_fx_pct", "ea_share_current_fx_pct"):
                row[field] = None
        prev = lookup.get(f"{int(month[:4])-1:04d}-{month[5:]}")
        for field in ("m2_current_fx_yoy_pct", "m2_fixed_fx_yoy_pct", "money_balance_contribution_yoy_pp", "fx_translation_contribution_yoy_pp", "us_m2_yoy_pct", "cn_m2_yoy_pct", "ea_m2_yoy_pct"):
            row[field] = None
        if complete and prev and prev["m2_current_fx_usd_bn"]:
            row["m2_current_fx_yoy_pct"] = 100 * (row["m2_current_fx_usd_bn"] / prev["m2_current_fx_usd_bn"] - 1)
            row["m2_fixed_fx_yoy_pct"] = 100 * (row["m2_fixed_2020_12_fx_usd_bn"] / prev["m2_fixed_2020_12_fx_usd_bn"] - 1)
            p_money = [prev["us_m2_usd_bn"], prev["cn_m2_cny_bn"], prev["ea_m2_eur_bn"]]
            p_fx = [1, prev["usd_per_cny"], prev["usd_per_eur"]]
            # Symmetric exact identity: d(MF) = mean(F)dM + mean(M)dF.
            dm = sum((f[0] + pf) / 2 * (m[0] - pm) for m, f, pm, pf in zip(money, fx, p_money, p_fx))
            df = sum((m[0] + pm) / 2 * (f[0] - pf) for m, f, pm, pf in zip(money, fx, p_money, p_fx))
            row["money_balance_contribution_yoy_pp"] = 100 * dm / prev["m2_current_fx_usd_bn"]
            row["fx_translation_contribution_yoy_pp"] = 100 * df / prev["m2_current_fx_usd_bn"]
            for name, m, pm in zip(("us", "cn", "ea"), money, p_money):
                row[f"{name}_m2_yoy_pct"] = 100 * (m[0] / pm - 1)
            if abs(dm + df - (row["m2_current_fx_usd_bn"] - prev["m2_current_fx_usd_bn"])) > 1e-7:
                raise AssertionError("FX decomposition failed exact reconciliation")
        # Stable aliases for the downstream asset relationship study.
        row["total_usd_bn"] = row["m2_current_fx_usd_bn"]
        row["total_fixed_usd_bn"] = row["m2_fixed_2020_12_fx_usd_bn"]
        row["local_contribution_pct"] = row["money_balance_contribution_yoy_pp"]
        row["fx_contribution_pct"] = row["fx_translation_contribution_yoy_pp"]
        panel.append(row)
        lookup[month] = row
    with (HERE / "liquidity_monthly.csv").open("w", encoding="utf8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(panel[0]))
        writer.writeheader()
        writer.writerows(panel)
    common = [r for r in panel if r["m2_current_fx_usd_bn"] is not None]
    manifest = {
        "title": "三大经济体 M2 研究组合", "as_of": AS_OF.isoformat(),
        "scope": ["United States M2", "China M2", "euro-area M2 (changing composition)"],
        "not_global_total": True, "not_central_bank_balance_sheets": True,
        "formula_current_fx": "US M2 USD + China M2 CNY × USD/CNY + euro-area M2 EUR × USD/EUR",
        "formula_fixed_fx": "same local-currency balances; CNY and EUR exchange rates fixed at 2020-12 last valid trading observation",
        "no_arbitrary_weights_or_deductions": True, "seasonal_adjustment": "all non-seasonally-adjusted",
        "period_alignment_limit": "US M2 is monthly average; Chinese/ECB M2 are end-month stocks. Same-month alignment is an approximation, not synchronous point-in-time liquidity.",
        "vintage_limit": "Current retrieved revisions, not a historical release-vintage panel. Do not claim an investable historical predictor or optimize a lead/lag.",
        "comparability_limit": "National M2 definitions differ, euro-area membership changes, China M2 statistical scope changed in 2011/2018. Fixed FX removes translation changes only, not local-definition/reclassification effects.",
        "fixed_fx_month": FIXED_FX_MONTH, "fixed_usd_per_cny": fixed_cn, "fixed_usd_per_eur": fixed_ea,
        "fx_period_basis": "FRED H.10 daily New York noon buying rate, last valid observation of each completed calendar month",
        "decomposition": "YoY symmetric midpoint exact identity, each region Δ(MF)=mean(F)ΔM+mean(M)ΔF; aggregate contributions divided by prior-year dollar total",
        "missing_policy": "no zero fill, no interpolation, no cross-month forward fill; aggregate requires every constituent and same-month FX",
        "common_history": {"first": common[0]["date"], "last": common[-1]["date"], "observations": len(common)},
        "latest": common[-1],
        "sources": [raw_metadata(RAW / f"{key}.csv", url, sid) for key, (sid, url) in PUBLIC.items()] + [raw_metadata(RAW / "china_m2_wind.csv", "Wind EDB via tkf_wind", "M0001384")],
        "documentation": [
            {"name": "US M2NS definitions and monthly frequency", "url": "https://fred.stlouisfed.org/series/M2NS"},
            {"name": "Federal Reserve H.6 definitions and monthly-average basis", "url": "https://www.federalreserve.gov/releases/h6/current/default.htm"},
            {"name": "Federal Reserve H.6 monthly-average basis", "url": "https://www.federalreserve.gov/releases/h6/about.htm"},
            {"name": "ECB official M2 dimensions", "url": "https://data.ecb.europa.eu/data/datasets/BSI/BSI.M.U2.N.V.M20.X.1.U2.2300.Z01.E"},
            {"name": "FRED H.10 CNY/USD", "url": "https://fred.stlouisfed.org/series/DEXCHUS"},
            {"name": "FRED H.10 USD/EUR", "url": "https://fred.stlouisfed.org/series/DEXUSEU"},
            {"name": "PBC official 2016 balances, unit and 2011 scope note", "url": "https://www.pbc.gov.cn/diaochatongjisi/fileDir/resource/cms/2017/01/2017011815321483353.pdf"},
        ],
        "rejected_sources": [
            {"name": "FRED MYAGM2CNM189N", "reason": "China M2 discontinued/frozen in 2019; cannot represent current China history"},
            {"name": "NBS official monthly API", "reason": "HTTP 403 on this retrieval; no data used"},
            {"name": "old workbook gold/M2 valuation", "reason": "mixed denominator formula across 2024 and extra gold-reserve scaling; no reuse of percentile or level"},
        ],
    }
    (HERE / "liquidity_sources.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({"history": manifest["common_history"], "latest": manifest["latest"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-public", action="store_true")
    parser.add_argument("--refresh-china-wind", action="store_true")
    args = parser.parse_args()
    if args.refresh_public:
        refresh_public()
    if args.refresh_china_wind:
        refresh_china_wind()
    build_panel()
