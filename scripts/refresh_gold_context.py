"""Refresh the authoritative page's M2 and reference assets, without research reruns.

Sources/units/FX anchor match the reviewed October 8 page. Unknown historical
changes fail before production writes. Coinbase/FRED reproduction was explicitly
authorized by the user on 2026-10-12; this does not grant readers upstream rights.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen

if __package__:
    from .gold_page import m2_inputs as m2
    from .refresh_wind_data import latest_closed_market_date, MARKET_OPTIONS
else:
    from gold_page import m2_inputs as m2
    from refresh_wind_data import latest_closed_market_date, MARKET_OPTIONS

ROOT = Path(__file__).resolve().parents[1]
MONTHLY = ROOT / "data/market/gold_context_monthly.csv"
ASSETS = ROOT / "data/market/gold_context_assets.csv"
SOURCES = ROOT / "data/market/gold_context_sources.json"
VERSION = "three-m2-fixed202012-v1"
ASSET_FIELDS = ["date", "silver", "bitcoin", "copper"]
TEXT_FIELDS = {"date", "month", "cny_fx_date", "eur_fx_date", "us_period_basis", "cn_period_basis", "ea_period_basis"}
ASSET_CODES = {"silver": "SPTAGUSDOZ.IDC", "bitcoin": "CBBTCUSD", "copper": "PCOPPUSDM"}


def previous_month(today):
    return today.replace(day=1) - timedelta(days=1)


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def rows_csv(rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def read_rows(path, text_fields=TEXT_FIELDS):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    seen = set()
    for row in rows:
        date.fromisoformat(row["date"])
        if row["date"] in seen:
            raise ValueError(f"Duplicate context date: {row['date']}")
        seen.add(row["date"])
        for key in row:
            if key not in text_fields:
                row[key] = float(row[key]) if row[key] else None
                if row[key] is not None and not math.isfinite(row[key]):
                    raise ValueError(f"Non-finite context input: {row['date']} {key}")
    if [row["date"] for row in rows] != sorted(seen):
        raise ValueError("Context dates must be ascending")
    return rows


def reconcile(previous, current):
    by_day = {row["date"]: row for row in current}
    changes = []
    for old in previous:
        new = by_day.get(old["date"])
        if new is None:
            changes.append(f"{old['date']}: period disappeared")
            continue
        for key, a in old.items():
            if key == "date" or a in (None, ""):
                continue
            b = new.get(key)
            same = (b is not None and isinstance(b, (int, float)) and abs(a - b) <= 1e-10 * max(1, abs(a))) if isinstance(a, (int, float)) else a == b
            if not same:
                changes.append(f"{old['date']} {key}: {a} -> {b}")
    if changes:
        raise ValueError("Unreviewed gold-context revisions:\n" + "\n".join(changes))


def validate_monthly(rows, metadata):
    if not positive(metadata.get("fixed_usd_per_cny")) or not positive(metadata.get("fixed_usd_per_eur")) or metadata.get("fixed_fx_month") != "2020-12":
        raise ValueError("Reviewed M2 FX anchor is missing or changed")
    valid = []
    for row in rows:
        if row["date"] != m2.month_end(row["date"][:7]):
            raise ValueError("M2 date is not the statistical month end")
        keys = ("us_m2_usd_bn", "cn_m2_cny_bn", "ea_m2_eur_bn", "usd_per_cny", "usd_per_eur")
        complete = all(positive(row.get(key)) for key in keys)
        totals = (row.get("total_usd_bn"), row.get("total_fixed_usd_bn"))
        if not complete:
            if any(value is not None for value in totals):
                raise ValueError("Partial M2 basket must not be presented as total")
            continue
        expected = (row[keys[0]] + row[keys[1]] * row[keys[3]] + row[keys[2]] * row[keys[4]],
                    row[keys[0]] + row[keys[1]] * metadata["fixed_usd_per_cny"] + row[keys[2]] * metadata["fixed_usd_per_eur"])
        if any(not positive(b) or not math.isclose(a, b, rel_tol=1e-12) for a, b in zip(expected, totals)):
            raise ValueError(f"M2 total calculation mismatch: {row['date']}")
        for field in ("cny_fx_date", "eur_fx_date"):
            stamp = row[field]
            if not stamp or stamp[:7] != row["date"][:7] or stamp > row["date"]:
                raise ValueError("M2 FX cannot be filled from another month or future")
        valid.append(row)
    if len(valid) < 240:
        raise ValueError("Insufficient complete M2 history")
    valid_dates = {r["date"] for r in valid}
    if any(valid[0]["date"] <= r["date"] <= valid[-1]["date"] and r["date"] not in valid_dates for r in rows):
        raise ValueError("Internal M2 gap; refuse a silently incomplete history")
    return valid


def parse_fred(payload, code, end, monthly=False):
    result = {}
    for row in csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))):
        stamp, raw = row["observation_date"], row[code]
        day = m2.month_end(stamp[:7]) if monthly else stamp
        if day > end.isoformat():
            continue
        value = None if raw in ("", ".") else float(raw)
        if value is not None and not positive(value):
            raise ValueError(f"Invalid {code} observation: {day}")
        if day in result:
            raise ValueError(f"Duplicate {code} observation: {day}")
        result[day] = value
    if not any(positive(value) for value in result.values()):
        raise ValueError(f"Empty FRED series: {code}")
    return result


def fetch_assets(today):
    from tkf_wind import w
    now = datetime.now(timezone.utc)
    market_end = min(today, latest_closed_market_date(now))
    w.start()
    named = w.wss(ASSET_CODES["silver"], "sec_name", "")
    if getattr(named, "ErrorCode", -1) or named.Data[0][0] != "伦敦银现":
        raise RuntimeError("Wind silver code-name verification failed")
    response = w.wsd(ASSET_CODES["silver"], "close", "2016-09-01", market_end.isoformat(), MARKET_OPTIONS)
    if getattr(response, "ErrorCode", -1) or len(response.Data) != 1 or not response.Times:
        raise RuntimeError("Wind silver request failed")
    silver = {}
    for stamp, value in zip(response.Times, response.Data[0]):
        day = str(stamp)[:10]
        if day > market_end.isoformat():
            continue
        if day in silver:
            raise ValueError("Duplicate silver date")
        silver[day] = float(value) if value is not None and math.isfinite(float(value)) else None
        if silver[day] is not None and not positive(silver[day]):
            raise ValueError("Silver must be positive")
    if not any(positive(v) for v in silver.values()):
        raise ValueError("Wind silver contains no valid observations")
    # The retained source labels observations at 17:00 PST; add a two-hour buffer.
    btc_end = min(today, (now - timedelta(hours=27)).date())
    datasets = {"silver": silver}
    metadata = {"silver": {"code": ASSET_CODES["silver"], "provider": "Wind/TkfWindPy", "field": "close", "options": MARKET_OPTIONS, "unit": "美元/金衡盎司", "request_end": market_end.isoformat()}}
    for key in ("bitcoin", "copper"):
        code = ASSET_CODES[key]
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={code}&cosd=2016-09-01"
        with urlopen(url, timeout=45) as response:
            payload = response.read()
        datasets[key] = parse_fred(payload, code, previous_month(today) if key == "copper" else btc_end, monthly=key == "copper")
        metadata[key] = {"code": code, "provider": "Coinbase via FRED" if key == "bitcoin" else "IMF via FRED", "definition_url": f"https://fred.stlouisfed.org/series/{code}", "download_url": url, "sha256": hashlib.sha256(payload).hexdigest(), "unit": "美元/BTC" if key == "bitcoin" else "美元/公吨", "quote_time": "5 PM PST; upstream daylight-saving treatment unspecified" if key == "bitcoin" else "完整日历月均价"}
    for key, series in datasets.items():
        valid = [(day, value) for day, value in sorted(series.items()) if positive(value)]
        metadata[key].update(first_date=valid[0][0], last_date=valid[-1][0], latest_value=valid[-1][1], valid_count=len(valid))
        print(f"{key}: {valid[-1][0]} {valid[-1][1]}", flush=True)
    days = sorted(set().union(*(s.keys() for s in datasets.values())))
    return [{"date": day, **{key: series.get(day) for key, series in datasets.items()}} for day in days], metadata


def install_files(writes):
    prepared, installed, previous = [], [], {}
    try:
        for target, content in writes:
            previous[target] = target.read_bytes() if target.exists() else None
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                stream.write(content)
                prepared.append((Path(stream.name), target))
        for temporary, target in prepared:
            os.replace(temporary, target)
            installed.append(target)
    except BaseException:
        for target in reversed(installed):
            if previous[target] is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(previous[target])
        raise
    finally:
        for temporary, _ in prepared:
            temporary.unlink(missing_ok=True)


def load_context(today):
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    if sources["method_version"] != VERSION:
        raise ValueError("Unreviewed context method version")
    for path in (MONTHLY, ASSETS):
        if hashlib.sha256(path.read_bytes()).hexdigest() != sources["inputHashes"][path.name]:
            raise ValueError(f"Context inputs differ from manifest: {path.name}")
    money = read_rows(MONTHLY)
    assets = read_rows(ASSETS, {"date"})
    validate_monthly(money, sources["liquidity"])
    return ([row for row in money if row["date"] <= previous_month(today).isoformat()],
            [row for row in assets if row["date"] <= today.isoformat()], sources)


def refresh(today=None, initialize_reviewed=False):
    today = today or date.today()
    existing = [path.exists() for path in (MONTHLY, ASSETS, SOURCES)]
    if any(existing) and not all(existing):
        raise ValueError("Partial context baseline; preserve files and review manually")
    if not all(existing) and not initialize_reviewed:
        raise ValueError("First context baseline requires reviewed migration")
    with tempfile.TemporaryDirectory(prefix="gold-context-") as tmp:
        m2.HERE, m2.RAW, m2.AS_OF = Path(tmp), Path(tmp) / "raw", today
        m2.refresh_public()
        m2.refresh_china_wind()
        money, liquidity = m2.build_panel()
        validate_monthly(money, liquidity)
        assets, metadata = fetch_assets(today)
    if all(existing):
        old_money, old_assets, old_sources = load_context(today)
        reconcile(old_money, money)
        reconcile(old_assets, assets)
        if old_sources["method_version"] != VERSION:
            raise ValueError("Method version requires manual review")
    monthly_bytes = rows_csv(money, list(money[0]))
    assets_bytes = rows_csv(assets, ASSET_FIELDS)
    sources = {"method_version": VERSION, "as_of": today.isoformat(), "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "liquidity": liquidity, "assets": metadata, "inputHashes": {MONTHLY.name: hashlib.sha256(monthly_bytes).hexdigest(), ASSETS.name: hashlib.sha256(assets_bytes).hexdigest()}, "research_snapshot": "data/gold_context_research.json (2026-10-08; not rerun daily)", "bitcoin_publication": "User explicitly confirmed upstream reproduction authorization on 2026-10-12; readers acquire no upstream redistribution rights."}
    install_files([(MONTHLY, monthly_bytes), (ASSETS, assets_bytes), (SOURCES, (json.dumps(sources, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8"))])
    print(f"M2 complete month: {liquidity['common_history']['last']}; context written atomically", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize-reviewed", action="store_true", help="Create the first explicitly reviewed baseline only; never bypass revisions.")
    args = parser.parse_args()
    refresh(initialize_reviewed=args.initialize_reviewed)
