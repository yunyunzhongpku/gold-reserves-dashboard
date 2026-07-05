from __future__ import annotations

import argparse
import csv
import io
import zipfile
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
MARKET_DIR = ROOT / "data" / "market"
BREAKEVEN_FILE = MARKET_DIR / "fred_t10yie.csv"
DFII10_FILE = MARKET_DIR / "fred_dfii10.csv"
COT_FILE = MARKET_DIR / "cftc_gold_cot.csv"

FRED_T10YIE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=T10YIE"
FRED_DFII10_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"
CFTC_CURRENT_URL = "https://www.cftc.gov/dea/newcot/f_disagg.txt"
CFTC_HISTORY_URL = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip"
CFTC_GOLD_MARKET = "GOLD - COMMODITY EXCHANGE INC."
USER_AGENT = "Mozilla/5.0 gold-dashboard data refresh"


def fetch_bytes(url, user_agent=True):
    headers = {"User-Agent": USER_AGENT} if user_agent else {}
    request = Request(url, headers=headers)
    with urlopen(request, timeout=60) as response:
        return response.read()


def parse_number(value):
    value = value.strip()
    if value in ("", "."):
        return None
    return float(value)


def parse_int(value):
    value = value.strip()
    if value == "":
        return None
    return int(value)


def read_fred_t10yie():
    text = fetch_bytes(FRED_T10YIE_URL, user_agent=False).decode("utf-8")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        value = parse_number(row["T10YIE"])
        if value is None:
            continue
        rows.append({
            "date": row["observation_date"],
            "breakeven_10y": value,
        })
    return rows


def read_fred_dfii10():
    text = fetch_bytes(FRED_DFII10_URL, user_agent=False).decode("utf-8")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        value = parse_number(row["DFII10"])
        if value is None:
            continue
        rows.append({"date": row["observation_date"], "real_rate": value})
    return rows


def parse_cftc_text(text):
    rows = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 23:
            continue
        if row[0].strip() != CFTC_GOLD_MARKET:
            continue

        open_interest = parse_int(row[7])
        long_position = parse_int(row[13])
        short_position = parse_int(row[14])
        spread_position = parse_int(row[15])
        if open_interest is None or long_position is None or short_position is None:
            continue

        managed_money_net = long_position - short_position
        rows.append({
            "date": row[2].strip(),
            "market": row[0].strip(),
            "open_interest": open_interest,
            "managed_money_long": long_position,
            "managed_money_short": short_position,
            "managed_money_spread": spread_position,
            "managed_money_net": managed_money_net,
            "managed_money_net_to_oi": managed_money_net / open_interest,
        })
    return rows


def read_cftc_current():
    text = fetch_bytes(CFTC_CURRENT_URL).decode("latin-1")
    return parse_cftc_text(text)


def read_cftc_history(year):
    data = fetch_bytes(CFTC_HISTORY_URL.format(year=year))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        txt_names = [name for name in archive.namelist() if name.endswith(".txt")]
        if not txt_names:
            return []
        text = archive.read(txt_names[0]).decode("latin-1")
    return parse_cftc_text(text)


def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def refresh(start_year=None, end_year=None):
    breakeven_rows = read_fred_t10yie()
    write_csv(BREAKEVEN_FILE, breakeven_rows, ["date", "breakeven_10y"])

    real_rate_rows = read_fred_dfii10()
    write_csv(DFII10_FILE, real_rate_rows, ["date", "real_rate"])

    current_year = date.today().year
    start_year = start_year or current_year - 5
    end_year = end_year or current_year
    cot_by_date = {}
    for year in range(start_year, end_year + 1):
        for row in read_cftc_history(year):
            cot_by_date[row["date"]] = row
    for row in read_cftc_current():
        cot_by_date[row["date"]] = row

    cot_rows = [cot_by_date[key] for key in sorted(cot_by_date)]
    write_csv(
        COT_FILE,
        cot_rows,
        [
            "date",
            "market",
            "open_interest",
            "managed_money_long",
            "managed_money_short",
            "managed_money_spread",
            "managed_money_net",
            "managed_money_net_to_oi",
        ],
    )
    return breakeven_rows, real_rate_rows, cot_rows


def main():
    parser = argparse.ArgumentParser(description="Refresh supplemental gold dashboard market data.")
    parser.add_argument("--start-year", type=int, default=None)
    parser.add_argument("--end-year", type=int, default=None)
    args = parser.parse_args()

    breakeven_rows, real_rate_rows, cot_rows = refresh(start_year=args.start_year, end_year=args.end_year)
    print(f"Wrote {BREAKEVEN_FILE} ({len(breakeven_rows)} rows)")
    print(f"Wrote {DFII10_FILE} ({len(real_rate_rows)} rows)")
    print(f"Wrote {COT_FILE} ({len(cot_rows)} rows)")


if __name__ == "__main__":
    main()
