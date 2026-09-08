#!/usr/bin/env python3
"""Compare XAU/USD strength across Asian, European, and US trading sessions."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import lzma
import math
import shutil
import struct
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://datafeed.dukascopy.com/datafeed"
INSTRUMENT = "XAUUSD"
DECIMAL_FACTOR = 1000
SIDES = ("BID", "ASK")
UTC = ZoneInfo("UTC")
NEW_YORK = ZoneInfo("America/New_York")
MIN_ACTIVE_SHARE = 0.80
WGC_SESSION_SOURCE = (
    "https://www.gold.org/goldhub/research/gold-mid-year-outlook-2026"
)
DUKASCOPY_HOURS_SOURCE = (
    "https://www.dukascopy.com/swiss/english/forex/"
    "forex-trading-accounts/link/"
)


@dataclass(frozen=True)
class SessionDefinition:
    key: str
    label: str
    start_day_offset: int
    start_hour: int
    end_day_offset: int
    end_hour: int
    duration_hours: int


SESSIONS = {
    "asia": SessionDefinition("asia", "亚盘", -1, 18, 0, 3, 9),
    "europe": SessionDefinition("europe", "欧盘", 0, 3, 0, 8, 5),
    "us": SessionDefinition("us", "美盘", 0, 8, 0, 17, 9),
}
SESSION_ORDER = tuple(SESSIONS)
SESSION_LABELS = {key: value.label for key, value in SESSIONS.items()}


def utc_datetime(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=UTC)


def session_bounds_utc(
    trade_date: dt.date,
    session: SessionDefinition,
) -> tuple[dt.datetime, dt.datetime]:
    start_date = trade_date + dt.timedelta(days=session.start_day_offset)
    end_date = trade_date + dt.timedelta(days=session.end_day_offset)
    start_local = dt.datetime.combine(
        start_date,
        dt.time(session.start_hour),
        tzinfo=NEW_YORK,
    )
    end_local = dt.datetime.combine(
        end_date,
        dt.time(session.end_hour),
        tzinfo=NEW_YORK,
    )
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def monthly_candle_url(month_start: dt.date, side: str) -> str:
    side = side.upper()
    if side not in SIDES:
        raise ValueError(f"unsupported side: {side}")
    return (
        f"{BASE_URL}/{INSTRUMENT}/{month_start.year:04d}/"
        f"{month_start.month - 1:02d}/{side}_candles_hour_1.bi5"
    )


def daily_candle_url(day: dt.date, side: str) -> str:
    side = side.upper()
    if side not in SIDES:
        raise ValueError(f"unsupported side: {side}")
    return (
        f"{BASE_URL}/{INSTRUMENT}/{day.year:04d}/{day.month - 1:02d}/"
        f"{day.day:02d}/{side}_candles_min_1.bi5"
    )


def _download(
    url: str,
    timeout: int = 30,
    retries: int = 6,
) -> bytes | None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "gold-reserves-dashboard-research/1.0"},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code not in {408, 429, 500, 502, 503, 504}:
                raise
            last_error = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        if attempt + 1 < retries:
            time.sleep(min(12.0, 0.75 * (2**attempt)))
    raise RuntimeError(f"download failed after {retries} attempts: {url}") from last_error


def _cached_payload(url: str, path: Path) -> bytes | None:
    missing_path = path.with_suffix(path.suffix + ".missing")
    if path.exists():
        return path.read_bytes()
    if missing_path.exists():
        return None
    payload = _download(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    if payload is None:
        missing_path.touch()
        return None
    path.write_bytes(payload)
    return payload


def decode_selected_candles(
    payload: bytes,
    period_start: dt.date | dt.datetime,
    selected_offsets: set[int] | None = None,
) -> dict[int, dict]:
    """Decode Dukascopy BI5 candles, optionally retaining selected offsets."""
    raw = lzma.decompress(payload)
    record_size = struct.calcsize(">IIIIIf")
    if len(raw) % record_size:
        raise ValueError(
            f"invalid BI5 payload size: {len(raw)} is not divisible by {record_size}"
        )

    rows = {}
    for seconds, open_raw, close_raw, low_raw, high_raw, volume in struct.iter_unpack(
        ">IIIIIf", raw
    ):
        open_value = open_raw / DECIMAL_FACTOR
        close_value = close_raw / DECIMAL_FACTOR
        low_value = low_raw / DECIMAL_FACTOR
        high_value = high_raw / DECIMAL_FACTOR
        if (
            low_value > min(open_value, close_value)
            or high_value < max(open_value, close_value)
            or low_value > high_value
        ):
            raise ValueError(
                f"impossible OHLC record for {period_start}: "
                f"{open_value}, {close_value}, {low_value}, {high_value}"
            )
        if selected_offsets is not None and seconds not in selected_offsets:
            continue
        rows[seconds] = {
            "open": open_value,
            "close": close_value,
            "low": low_value,
            "high": high_value,
            "volume": float(volume),
        }
    return rows


def _merge_sides(
    bid: dict[int, dict],
    ask: dict[int, dict],
    period_start: dt.datetime,
    period_minutes: int,
) -> tuple[dict[dt.datetime, dict], int]:
    points = {}
    invalid = 0
    for seconds in sorted(set(bid) & set(ask)):
        bid_row = bid[seconds]
        ask_row = ask[seconds]
        if (
            bid_row["open"] <= 0
            or ask_row["open"] <= 0
            or bid_row["close"] <= 0
            or ask_row["close"] <= 0
            or bid_row["open"] > ask_row["open"]
            or bid_row["close"] > ask_row["close"]
        ):
            invalid += 1
            continue
        timestamp = period_start + dt.timedelta(seconds=seconds)
        points[timestamp] = {
            "bid_open": bid_row["open"],
            "ask_open": ask_row["open"],
            "bid_close": bid_row["close"],
            "ask_close": ask_row["close"],
            "bid_volume": bid_row["volume"],
            "ask_volume": ask_row["volume"],
            "period_minutes": period_minutes,
        }
    return points, invalid


def _load_month(
    month_start: dt.date,
    cache_dir: Path,
) -> tuple[dt.date, dict[dt.datetime, dict], dict]:
    parsed = {}
    compressed_bytes = 0
    files_present = 0
    for side in SIDES:
        path = (
            cache_dir
            / INSTRUMENT
            / f"{month_start.year:04d}"
            / f"{month_start.month:02d}"
            / f"{side}_candles_hour_1.bi5"
        )
        payload = _cached_payload(monthly_candle_url(month_start, side), path)
        if payload is None:
            return month_start, {}, {
                "available": False,
                "compressed_bytes": compressed_bytes,
                "files_present": files_present,
                "invalid_points": 0,
            }
        files_present += 1
        compressed_bytes += len(payload)
        parsed[side] = decode_selected_candles(payload, month_start)
    period_start = utc_datetime(month_start.year, month_start.month, 1)
    points, invalid = _merge_sides(
        parsed["BID"], parsed["ASK"], period_start, period_minutes=60
    )
    return month_start, points, {
        "available": True,
        "compressed_bytes": compressed_bytes,
        "files_present": files_present,
        "invalid_points": invalid,
    }


def _load_day(
    day: dt.date,
    cache_dir: Path,
) -> tuple[dt.date, dict[dt.datetime, dict], dict]:
    parsed = {}
    compressed_bytes = 0
    files_present = 0
    for side in SIDES:
        path = (
            cache_dir
            / INSTRUMENT
            / f"{day.year:04d}"
            / f"{day.month:02d}"
            / f"{day.day:02d}_{side}_candles_min_1.bi5"
        )
        payload = _cached_payload(daily_candle_url(day, side), path)
        if payload is None:
            return day, {}, {
                "available": False,
                "compressed_bytes": compressed_bytes,
                "files_present": files_present,
                "invalid_points": 0,
            }
        files_present += 1
        compressed_bytes += len(payload)
        parsed[side] = decode_selected_candles(payload, day)
    period_start = utc_datetime(day.year, day.month, day.day)
    points, invalid = _merge_sides(
        parsed["BID"], parsed["ASK"], period_start, period_minutes=1
    )
    return day, points, {
        "available": True,
        "compressed_bytes": compressed_bytes,
        "files_present": files_present,
        "invalid_points": invalid,
    }


def _date_range(start: dt.date, end: dt.date):
    current = start
    while current <= end:
        yield current
        current += dt.timedelta(days=1)


def _month_range(start: dt.date, end: dt.date):
    current = start.replace(day=1)
    final = end.replace(day=1)
    while current <= final:
        yield current
        if current.month == 12:
            current = dt.date(current.year + 1, 1, 1)
        else:
            current = dt.date(current.year, current.month + 1, 1)


def candidate_trade_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    return [day for day in _date_range(start, end) if day.weekday() < 5]


def required_utc_dates(
    trade_dates: list[dt.date],
) -> set[dt.date]:
    required = set()
    for trade_date in trade_dates:
        for session in SESSIONS.values():
            start, end = session_bounds_utc(trade_date, session)
            required.add(start.date())
            required.add((end - dt.timedelta(minutes=1)).date())
    return required


def download_market_points(
    trade_dates: list[dt.date],
    cache_dir: Path,
    workers: int = 2,
) -> tuple[dict[dt.datetime, dict], dict]:
    required_dates = required_utc_dates(trade_dates)
    first_required = min(required_dates)
    last_required = max(required_dates)
    current_month = dt.datetime.now(UTC).date().replace(day=1)
    months = list(_month_range(first_required, last_required))
    historical_months = [month for month in months if month < current_month]
    daily_months = {month for month in months if month >= current_month}

    points = {}
    quality = {
        "monthly_h1_requests": len(historical_months) * 2,
        "daily_m1_requests": 0,
        "files_present": 0,
        "compressed_bytes": 0,
        "invalid_or_crossed_points": 0,
        "h1_months_available": 0,
        "h1_months_fallback": 0,
        "m1_days_available": 0,
        "m1_days_missing": 0,
    }

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(_load_month, month, cache_dir): month
            for month in historical_months
        }
        for completed, future in enumerate(as_completed(futures), 1):
            month, month_points, meta = future.result()
            quality["files_present"] += meta["files_present"]
            quality["compressed_bytes"] += meta["compressed_bytes"]
            quality["invalid_or_crossed_points"] += meta["invalid_points"]
            if meta["available"]:
                quality["h1_months_available"] += 1
                points.update(month_points)
            else:
                quality["h1_months_fallback"] += 1
                daily_months.add(month)
            if completed % 12 == 0 or completed == len(futures):
                print(
                    f"Loaded {completed}/{len(futures)} complete H1 months",
                    flush=True,
                )

    daily_dates = sorted(
        day for day in required_dates if day.replace(day=1) in daily_months
    )
    quality["daily_m1_requests"] = len(daily_dates) * 2
    if daily_dates:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(_load_day, day, cache_dir): day
                for day in daily_dates
            }
            for completed, future in enumerate(as_completed(futures), 1):
                _, day_points, meta = future.result()
                quality["files_present"] += meta["files_present"]
                quality["compressed_bytes"] += meta["compressed_bytes"]
                quality["invalid_or_crossed_points"] += meta["invalid_points"]
                if meta["available"]:
                    quality["m1_days_available"] += 1
                    points.update(day_points)
                else:
                    quality["m1_days_missing"] += 1
                if completed % 10 == 0 or completed == len(futures):
                    print(
                        f"Loaded {completed}/{len(futures)} current/fallback M1 days",
                        flush=True,
                    )

    quality["selected_bid_ask_points"] = len(points)
    quality["first_timestamp_utc"] = (
        min(points).isoformat() if points else None
    )
    quality["last_timestamp_utc"] = (
        max(points).isoformat() if points else None
    )
    return points, quality


def _point_is_active(point: dict | None) -> bool:
    if not point:
        return False
    return point.get("bid_volume", 0.0) + point.get("ask_volume", 0.0) > 0


def _session_coverage(
    points: dict[dt.datetime, dict],
    start: dt.datetime,
    end: dt.datetime,
) -> tuple[float, float]:
    expected_minutes = (end - start).total_seconds() / 60
    covered_minutes = 0
    active_minutes = 0
    for timestamp, point in points.items():
        if not (start <= timestamp < end):
            continue
        period_minutes = int(point["period_minutes"])
        covered_minutes += period_minutes
        if _point_is_active(point):
            active_minutes += period_minutes
    return (
        min(1.0, covered_minutes / expected_minutes),
        min(1.0, active_minutes / expected_minutes),
    )


def _end_point(
    points: dict[dt.datetime, dict],
    end: dt.datetime,
) -> tuple[dt.datetime, dict] | tuple[None, None]:
    for minutes in (1, 60):
        timestamp = end - dt.timedelta(minutes=minutes)
        point = points.get(timestamp)
        if point and int(point.get("period_minutes", minutes)) == minutes:
            return timestamp, point
    return None, None


def session_return(
    trade_date: dt.date,
    session: SessionDefinition,
    points: dict[dt.datetime, dict],
) -> dict | None:
    start, end = session_bounds_utc(trade_date, session)
    entry = points.get(start)
    exit_timestamp, exit_point = _end_point(points, end)
    if not entry or not exit_point:
        return None
    if not _point_is_active(entry) or not _point_is_active(exit_point):
        return None
    presence_share, active_share = _session_coverage(points, start, end)
    if presence_share < 0.99 or active_share < MIN_ACTIVE_SHARE:
        return None

    start_mid = (entry["bid_open"] + entry["ask_open"]) / 2
    end_mid = (exit_point["bid_close"] + exit_point["ask_close"]) / 2
    gross_return = end_mid / start_mid - 1
    net_return = exit_point["bid_close"] / entry["ask_open"] - 1
    return {
        "trade_date": trade_date.isoformat(),
        "session": session.key,
        "session_label": session.label,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "exit_bar_timestamp_utc": exit_timestamp.isoformat(),
        "duration_hours": session.duration_hours,
        "start_bid": entry["bid_open"],
        "start_ask": entry["ask_open"],
        "start_mid": start_mid,
        "end_bid": exit_point["bid_close"],
        "end_ask": exit_point["ask_close"],
        "end_mid": end_mid,
        "gross_return": gross_return,
        "gross_return_bps": gross_return * 10_000,
        "gross_log_return_bps": math.log(end_mid / start_mid) * 10_000,
        "net_return": net_return,
        "net_return_bps": net_return * 10_000,
        "spread_cost_bps": (gross_return - net_return) * 10_000,
        "start_spread_usd_per_oz": entry["ask_open"] - entry["bid_open"],
        "end_spread_usd_per_oz": exit_point["ask_close"] - exit_point["bid_close"],
        "presence_share": presence_share,
        "active_share": active_share,
    }


def build_session_rows(
    trade_dates: list[dt.date],
    points: dict[dt.datetime, dict],
) -> pd.DataFrame:
    rows = []
    for trade_date in trade_dates:
        for session in SESSIONS.values():
            row = session_return(trade_date, session, points)
            if row is not None:
                rows.append(row)
    return pd.DataFrame(rows)


def build_curves(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = rows.copy()
    if "session_label" not in frame:
        frame["session_label"] = frame["session"].map(SESSION_LABELS)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    counts = frame.groupby("trade_date")["session"].nunique()
    complete_dates = counts[counts == len(SESSIONS)].index
    complete = frame.loc[frame["trade_date"].isin(complete_dates)].copy()
    complete["session"] = pd.Categorical(
        complete["session"], categories=SESSION_ORDER, ordered=True
    )
    complete = complete.sort_values(["trade_date", "session"]).reset_index(drop=True)
    complete["session"] = complete["session"].astype(str)
    curve = complete[
        ["trade_date", "session", "session_label", "gross_return", "net_return"]
    ].copy()
    curve["gross_nav"] = curve.groupby("session", sort=False)[
        "gross_return"
    ].transform(lambda values: (1 + values).cumprod())
    curve["net_nav"] = curve.groupby("session", sort=False)["net_return"].transform(
        lambda values: (1 + values).cumprod()
    )
    return complete, curve


def block_bootstrap_mean_ci(
    values: pd.Series,
    dates: pd.Series,
    iterations: int = 3000,
    seed: int = 20260724,
) -> tuple[float, float]:
    valid = pd.DataFrame({"value": values, "date": dates}).dropna()
    valid["month"] = pd.to_datetime(valid["date"]).dt.to_period("M")
    blocks = [group["value"].to_numpy() for _, group in valid.groupby("month")]
    if len(blocks) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = np.empty(iterations)
    for index in range(iterations):
        selected = rng.integers(0, len(blocks), size=len(blocks))
        sample = np.concatenate([blocks[position] for position in selected])
        means[index] = sample.mean()
    low, high = np.quantile(means, [0.025, 0.975])
    return float(low), float(high)


def _max_drawdown(nav: pd.Series) -> float:
    running_peak = nav.cummax()
    return float((nav / running_peak - 1).min())


def summarize_session(
    session_rows: pd.DataFrame,
    session_curve: pd.DataFrame,
) -> dict:
    returns = session_rows["gross_return"].astype(float)
    bps = session_rows["gross_return_bps"].astype(float)
    dates = session_rows["trade_date"]
    model = sm.OLS(bps.to_numpy(), np.ones(len(bps))).fit(
        cov_type="HAC", cov_kwds={"maxlags": 5}
    )
    nonzero = bps[bps != 0]
    sign_p = (
        float(stats.binomtest(int((nonzero > 0).sum()), len(nonzero), 0.5).pvalue)
        if len(nonzero)
        else float("nan")
    )
    ci_low, ci_high = block_bootstrap_mean_ci(bps, dates)
    elapsed_days = max(
        1,
        (
            pd.to_datetime(dates).max() - pd.to_datetime(dates).min()
        ).days,
    )
    final_gross_nav = float(session_curve["gross_nav"].iloc[-1])
    final_net_nav = float(session_curve["net_nav"].iloc[-1])
    annualized_return = final_gross_nav ** (365.25 / elapsed_days) - 1
    annualized_volatility = float(returns.std(ddof=1) * math.sqrt(252))
    annualized_sharpe = (
        float(returns.mean() / returns.std(ddof=1) * math.sqrt(252))
        if returns.std(ddof=1) > 0
        else float("nan")
    )
    cutoff = bps.abs().quantile(0.99)
    trimmed = bps.loc[bps.abs() <= cutoff]
    duration_hours = float(session_rows["duration_hours"].iloc[0])
    return {
        "n": int(len(session_rows)),
        "mean_bps": float(bps.mean()),
        "median_bps": float(bps.median()),
        "mean_bps_per_hour": float(bps.mean() / duration_hours),
        "positive_share": float((returns > 0).mean()),
        "standard_deviation_bps": float(bps.std(ddof=1)),
        "annualized_volatility": annualized_volatility,
        "annualized_sharpe_no_risk_free": annualized_sharpe,
        "cumulative_gross_return": final_gross_nav - 1,
        "cumulative_net_return": final_net_nav - 1,
        "annualized_gross_return": float(annualized_return),
        "gross_max_drawdown": _max_drawdown(session_curve["gross_nav"]),
        "net_max_drawdown": _max_drawdown(session_curve["net_nav"]),
        "mean_spread_cost_bps_per_round_trip": float(
            session_rows["spread_cost_bps"].mean()
        ),
        "hac_t": float(model.tvalues[0]),
        "hac_p": float(model.pvalues[0]),
        "sign_test_p": sign_p,
        "month_block_ci_low_bps": ci_low,
        "month_block_ci_high_bps": ci_high,
        "mean_bps_excluding_largest_1pct_absolute_days": float(trimmed.mean()),
    }


def build_annual_table(complete: pd.DataFrame) -> pd.DataFrame:
    frame = complete.copy()
    frame["year"] = frame["trade_date"].dt.year
    rows = []
    for (year, session), group in frame.groupby(["year", "session"], observed=True):
        rows.append(
            {
                "year": int(year),
                "session": session,
                "session_label": SESSION_LABELS[session],
                "n": int(len(group)),
                "gross_return": float((1 + group["gross_return"]).prod() - 1),
                "net_return": float((1 + group["net_return"]).prod() - 1),
                "mean_bps": float(group["gross_return_bps"].mean()),
                "positive_share": float((group["gross_return"] > 0).mean()),
            }
        )
    return pd.DataFrame(rows).sort_values(["year", "session"]).reset_index(drop=True)


def _paired_summaries(complete: pd.DataFrame) -> dict:
    pivot = complete.pivot(
        index="trade_date", columns="session", values="gross_return_bps"
    )
    comparisons = {
        "asia_minus_europe": pivot["asia"] - pivot["europe"],
        "asia_minus_us": pivot["asia"] - pivot["us"],
        "europe_minus_us": pivot["europe"] - pivot["us"],
    }
    results = {}
    for name, values in comparisons.items():
        model = sm.OLS(values.to_numpy(), np.ones(len(values))).fit(
            cov_type="HAC", cov_kwds={"maxlags": 5}
        )
        ci_low, ci_high = block_bootstrap_mean_ci(
            values.reset_index(drop=True),
            pd.Series(values.index),
        )
        results[name] = {
            "n": int(len(values)),
            "mean_difference_bps": float(values.mean()),
            "median_difference_bps": float(values.median()),
            "hac_p": float(model.pvalues[0]),
            "month_block_ci_low_bps": ci_low,
            "month_block_ci_high_bps": ci_high,
        }
    return results


def _wgc_h1_2026_check(complete: pd.DataFrame) -> dict:
    sample = complete.loc[
        complete["trade_date"].between(
            pd.Timestamp("2026-01-01"), pd.Timestamp("2026-06-26")
        )
    ]
    if sample.empty:
        return {}
    results = {}
    for session, group in sample.groupby("session"):
        results[session] = {
            "n": int(len(group)),
            "cumulative_gross_return": float(
                (1 + group["gross_return"]).prod() - 1
            ),
        }
    return results


def _wind_daily_crosscheck(complete: pd.DataFrame) -> dict:
    wind_path = ROOT / "data" / "market" / "wind_daily.csv"
    if not wind_path.exists():
        return {}
    us_end = complete.loc[
        complete["session"] == "us", ["trade_date", "end_mid"]
    ].copy()
    us_end["date"] = us_end["trade_date"].dt.strftime("%Y-%m-%d")
    wind = pd.read_csv(wind_path, usecols=["date", "gold_price"]).dropna()
    overlap = us_end.merge(wind, on="date")
    if len(overlap) < 2:
        return {}
    return {
        "overlap_days": int(len(overlap)),
        "level_correlation": float(
            overlap[["end_mid", "gold_price"]].corr().iloc[0, 1]
        ),
        "note": (
            "Only validates instrument identity and price scale; "
            "daily cutoffs are not identical."
        ),
    }


def _boundary_stitch_check(complete: pd.DataFrame) -> dict:
    wide = complete.pivot(index="trade_date", columns="session")
    compounded = (1 + wide["gross_return"]).prod(axis=1) - 1
    full_window = wide["end_mid"]["us"] / wide["start_mid"]["asia"] - 1
    residual_bps = (compounded - full_window) * 10_000
    return {
        "mean_residual_bps": float(residual_bps.mean()),
        "median_residual_bps": float(residual_bps.median()),
        "p99_absolute_residual_bps": float(residual_bps.abs().quantile(0.99)),
        "maximum_absolute_residual_bps": float(residual_bps.abs().max()),
        "note": (
            "Residual comes from the close-to-next-open gap at the two "
            "hourly session boundaries."
        ),
    }


def build_results(
    start: dt.date,
    end: dt.date,
    trade_dates: list[dt.date],
    raw_rows: pd.DataFrame,
    complete: pd.DataFrame,
    curve: pd.DataFrame,
    annual: pd.DataFrame,
    download_quality: dict,
) -> dict:
    sessions = {}
    for session in SESSION_ORDER:
        session_rows = complete.loc[complete["session"] == session]
        session_curve = curve.loc[curve["session"] == session]
        sessions[session] = summarize_session(session_rows, session_curve)

    missing_by_session = {
        session: int(len(trade_dates) - (raw_rows["session"] == session).sum())
        for session in SESSION_ORDER
    }
    positive_years = (
        annual.assign(is_positive=annual["gross_return"] > 0)
        .groupby("session")["is_positive"]
        .sum()
        .to_dict()
    )
    for session in SESSION_ORDER:
        sessions[session]["positive_calendar_years"] = int(
            positive_years.get(session, 0)
        )
        sessions[session]["calendar_years_observed"] = int(
            (annual["session"] == session).sum()
        )

    return {
        "sample": {
            "start_trade_date": start.isoformat(),
            "end_trade_date": end.isoformat(),
            "candidate_weekdays": int(len(trade_dates)),
            "complete_common_trade_days": int(
                complete["trade_date"].nunique()
            ),
            "timezone_contract": "America/New_York with historical DST",
            "session_contract": {
                "asia": "previous day 18:00 to 03:00 New York",
                "europe": "03:00 to 08:00 New York",
                "us": "08:00 to 17:00 New York",
                "maintenance": "17:00 to 18:00 New York, excluded",
            },
            "price_contract": (
                "gross uses bid/ask midpoint; executable net enters at ask "
                "and exits at bid"
            ),
        },
        "data_quality": {
            **download_quality,
            "valid_session_rows_before_common_filter": int(len(raw_rows)),
            "missing_candidate_sessions": missing_by_session,
            "complete_common_trade_days": int(
                complete["trade_date"].nunique()
            ),
            "median_active_share": float(raw_rows["active_share"].median()),
            "minimum_active_share_required": MIN_ACTIVE_SHARE,
            "wind_daily_crosscheck": _wind_daily_crosscheck(complete),
            "boundary_stitch_check": _boundary_stitch_check(complete),
        },
        "sessions": sessions,
        "paired_differences": _paired_summaries(complete),
        "annual": annual.to_dict(orient="records"),
        "wgc_h1_2026_directional_crosscheck": _wgc_h1_2026_check(complete),
        "sources": {
            "session_definition": WGC_SESSION_SOURCE,
            "market_data": (
                "Dukascopy XAUUSD BID/ASK H1 monthly and M1 daily BI5 files"
            ),
            "dukascopy_trading_hours": DUKASCOPY_HOURS_SOURCE,
        },
    }


def _configure_chinese_font():
    plt.rcParams["font.sans-serif"] = [
        "PingFang SC",
        "Arial Unicode MS",
        "Heiti TC",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def plot_curves(curve: pd.DataFrame, path: Path, start: dt.date, end: dt.date):
    _configure_chinese_font()
    palette = {"asia": "#B8860B", "europe": "#245A7A", "us": "#C65D18"}
    line_styles = {"asia": "-", "europe": "--", "us": ":"}
    actual_start = pd.to_datetime(curve["trade_date"]).min()
    actual_end = pd.to_datetime(curve["trade_date"]).max()
    baseline_date = actual_start - pd.Timedelta(days=1)
    fig, axes = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
    panels = [
        ("gross_nav", "中间价累计净值", "只衡量各时段的价格方向，不扣交易成本"),
        ("net_nav", "按买卖价成交的累计净值", "每日开盘按 ASK 买入、结束按 BID 卖出"),
    ]
    for axis, (column, title, subtitle) in zip(axes, panels):
        for session in SESSION_ORDER:
            data = curve.loc[curve["session"] == session]
            dates = pd.concat(
                [pd.Series([baseline_date]), data["trade_date"]],
                ignore_index=True,
            )
            nav = pd.concat(
                [pd.Series([1.0]), data[column]],
                ignore_index=True,
            )
            axis.plot(
                dates,
                nav,
                label=SESSION_LABELS[session],
                color=palette[session],
                linestyle=line_styles[session],
                linewidth=2.0,
            )
        axis.axhline(1.0, color="#777777", linewidth=0.8)
        axis.text(
            0,
            1.11,
            title,
            transform=axis.transAxes,
            fontsize=14,
            fontweight="bold",
            va="bottom",
        )
        axis.text(
            0,
            1.055,
            subtitle,
            transform=axis.transAxes,
            fontsize=10,
            color="#555555",
            va="bottom",
        )
        axis.set_ylabel("净值（起点为 1）")
        axis.grid(axis="y", color="#E5E5E5", linewidth=0.8)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(frameon=False, ncol=3, loc="upper left")
    axes[-1].set_xlabel("交易日")
    fig.suptitle(
        "伦敦金亚盘、欧盘和美盘共同样本累计净值"
        f"（{actual_start.date()} 至 {actual_end.date()}）",
        x=0.08,
        y=0.995,
        ha="left",
        fontsize=17,
        fontweight="bold",
    )
    fig.subplots_adjust(left=0.08, right=0.985, bottom=0.075, top=0.87, hspace=0.42)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_annual_returns(annual: pd.DataFrame, path: Path):
    _configure_chinese_font()
    palette = {"asia": "#B8860B", "europe": "#245A7A", "us": "#C65D18"}
    years = sorted(annual["year"].unique())
    positions = np.arange(len(years))
    width = 0.24
    fig, axis = plt.subplots(figsize=(11, 5.8))
    for index, session in enumerate(SESSION_ORDER):
        data = (
            annual.loc[annual["session"] == session]
            .set_index("year")
            .reindex(years)
        )
        axis.bar(
            positions + (index - 1) * width,
            data["gross_return"] * 100,
            width,
            label=SESSION_LABELS[session],
            color=palette[session],
            edgecolor="#333333",
            linewidth=0.5,
        )
    axis.axhline(0, color="#444444", linewidth=0.9)
    axis.set_xticks(positions, years)
    axis.set_ylabel("年度累计收益（%）")
    axis.text(
        0,
        1.115,
        "各交易时段年度累计收益",
        transform=axis.transAxes,
        fontsize=15,
        fontweight="bold",
        va="bottom",
    )
    axis.text(
        0,
        1.055,
        "中间价口径；2026 年截至样本结束日",
        transform=axis.transAxes,
        fontsize=10,
        color="#555555",
        va="bottom",
    )
    axis.grid(axis="y", color="#E5E5E5", linewidth=0.8)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(frameon=False, ncol=3, loc="upper left")
    fig.subplots_adjust(left=0.09, right=0.985, bottom=0.12, top=0.84)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return value.isoformat()
    return value


def write_outputs(
    output_dir: Path,
    as_of: dt.date,
    complete: pd.DataFrame,
    curve: pd.DataFrame,
    annual: pd.DataFrame,
    results: dict,
    start: dt.date,
    end: dt.date,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{as_of.isoformat()}_伦敦金三时段强弱"
    paths = {
        "daily": output_dir / f"{prefix}_日度收益.csv",
        "curve": output_dir / f"{prefix}_累计净值.csv",
        "annual": output_dir / f"{prefix}_年度分解.csv",
        "json": output_dir / f"{prefix}_检验结果.json",
        "curve_chart": output_dir / f"{prefix}_累计净值图.png",
        "annual_chart": output_dir / f"{prefix}_年度收益图.png",
    }
    complete.to_csv(paths["daily"], index=False, quoting=csv.QUOTE_MINIMAL)
    curve.to_csv(paths["curve"], index=False, quoting=csv.QUOTE_MINIMAL)
    annual.to_csv(paths["annual"], index=False, quoting=csv.QUOTE_MINIMAL)
    paths["json"].write_text(
        json.dumps(
            _json_safe(results),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    plot_curves(curve, paths["curve_chart"], start, end)
    plot_annual_returns(annual, paths["annual_chart"])
    return paths


def parse_args() -> argparse.Namespace:
    yesterday_new_york = dt.datetime.now(NEW_YORK).date() - dt.timedelta(days=1)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=yesterday_new_york.isoformat())
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "outputs" / "research",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("/private/tmp/gold_sessions_dukascopy_cache"),
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--keep-cache", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    if start > end:
        raise ValueError("start date must be on or before end date")
    trade_dates = candidate_trade_dates(start, end)
    points, download_quality = download_market_points(
        trade_dates,
        args.cache_dir,
        workers=args.workers,
    )
    if not points:
        raise RuntimeError("no valid Dukascopy bid/ask data was downloaded")
    raw_rows = build_session_rows(trade_dates, points)
    complete, curve = build_curves(raw_rows)
    if complete.empty:
        raise RuntimeError("no trade dates have all three complete sessions")
    annual = build_annual_table(complete)
    results = build_results(
        start,
        end,
        trade_dates,
        raw_rows,
        complete,
        curve,
        annual,
        download_quality,
    )
    paths = write_outputs(
        args.output_dir,
        dt.date.today(),
        complete,
        curve,
        annual,
        results,
        start,
        end,
    )
    if not args.keep_cache and args.cache_dir.exists():
        shutil.rmtree(args.cache_dir)
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
