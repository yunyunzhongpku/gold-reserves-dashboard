"""Refresh the workbook's original Wind EDB series, with explicit reconciliation."""
from __future__ import annotations

import calendar
import csv
from datetime import date, datetime
import json
import math
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/market/wind_supplemental.csv"
WORKBOOK = ROOT / "data/招商证券：黄金图表整理2607.xlsx"
REVISIONS = ROOT / "data/market/wind_edb_revisions.json"
# Codes and units are the original workbook's embedded Wind metadata.
SERIES = {
    "spdr_holdings": ("S0105521", "黄金ETF持有量&期现基差", 1),
    "ishares_holdings": ("S5807691", "黄金ETF持有量&期现基差", 2),
    "epu": ("G1060817", "经济政策不确定性", 1),
    "gpr": ("L2715907", "地缘政治风险", 1),
    "global_reserves": ("L8751203", "官方黄金储备", 2),
}


def monthly_means(series, end):
    groups = {}
    for day, value in series.items():
        groups.setdefault(day[:7], {})[day] = value
    result = {}
    for month, days in sorted(groups.items()):
        year, number = map(int, month.split("-"))
        length = calendar.monthrange(year, number)[1]
        last = date(year, number, length)
        expected = {date(year, number, d).isoformat() for d in range(1, length + 1)}
        if last <= end and set(days) == expected:
            result[last.isoformat()] = sum(days.values()) / length
    return result


def reconcile_series(key, values, anchors, revisions):
    shared = set(values) & set(anchors)
    if not shared:
        raise ValueError(f"No reconciliation overlap for {key}")
    for day in sorted(shared):
        old, new = anchors[day], values[day]
        if abs(new - old) <= 1e-6 * max(1.0, abs(old)):
            continue
        reviewed = revisions.get(key, {}).get(day, {})
        if reviewed.get("old") == old and reviewed.get("new") == new:
            continue
        raise ValueError(f"Unreviewed Wind revision for {key} on {day}: {old} -> {new}")


def refresh(end=None):
    from openpyxl import load_workbook
    from tkf_wind import w

    end = date.fromisoformat(end) if isinstance(end, str) else end or date.today()
    w.start()
    values = {}
    for key, (code, _, _) in SERIES.items():
        response = w.edb(code, "1985-01-01", end.isoformat(), "Fill=Blank")
        if getattr(response, "ErrorCode", -1) != 0:
            raise RuntimeError(f"Wind EDB failed for {code}")
        series = {}
        for stamp, value in zip(response.Times, response.Data[0]):
            day = stamp.date() if isinstance(stamp, datetime) else stamp
            if day > end or value is None or not math.isfinite(float(value)):
                continue
            if float(value) < 0 or (float(value) == 0 and key not in {"epu", "gpr"}):
                raise ValueError(f"Invalid Wind EDB observation for {code}")
            series[day.isoformat()] = float(value)
        if key in {"epu", "gpr"}:
            series = monthly_means(series, end)
        if not series:
            raise ValueError(f"Wind EDB series is empty: {code}")
        values[key] = series

    revisions = json.loads(REVISIONS.read_text())["revisions"]
    workbook = load_workbook(WORKBOOK, read_only=True, data_only=True)
    try:
        for key, (_, sheet, column) in SERIES.items():
            anchors = {}
            for row in workbook[sheet].iter_rows(values_only=True):
                if not row or not isinstance(row[0], (date, datetime)):
                    continue
                day = row[0].strftime("%Y-%m-%d")
                # The July workbook was exported on July 6: later monthly rows
                # are incomplete observations, not completed-month anchors.
                if key in {"epu", "gpr", "global_reserves"} and day > "2026-06-30":
                    continue
                if isinstance(row[column], (int, float)):
                    anchors[day] = float(row[column])
            reconcile_series(key, values[key], anchors, revisions)
    finally:
        workbook.close()

    fields = ["date", *SERIES]
    rows = [{"date": day, **{key: values[key].get(day, "") for key in SERIES}}
            for day in sorted({day for series in values.values() for day in series})]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=OUTPUT.parent, delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, OUTPUT)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    for key, series in values.items():
        print(f"{key}: {max(series)} ({len(series)} observations)")
    return rows


if __name__ == "__main__":
    refresh()
