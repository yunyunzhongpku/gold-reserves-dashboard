#!/usr/bin/env python3
"""Test whether XAU/USD is systematically strong around 08:00 Beijing time."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import lzma
import os
import struct
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from math import log
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from openpyxl import load_workbook
from scipy import stats
import statsmodels.api as sm


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://datafeed.dukascopy.com/datafeed"
INSTRUMENT = "XAUUSD"
DECIMAL_FACTOR = 1000
SIDES = ("BID", "ASK")
HORIZONS = (1, 5, 15, 30, 60)
PRIMARY_HORIZON = 30
BEIJING = ZoneInfo("Asia/Shanghai")
UTC = ZoneInfo("UTC")


def candle_url(day: dt.date, side: str) -> str:
    side = side.upper()
    if side not in SIDES:
        raise ValueError(f"unsupported side: {side}")
    return (
        f"{BASE_URL}/{INSTRUMENT}/{day.year:04d}/{day.month - 1:02d}/"
        f"{day.day:02d}/{side}_candles_min_1.bi5"
    )


def decode_candles(
    payload: bytes,
    day: dt.date,
    decimal_factor: int = DECIMAL_FACTOR,
) -> list[dict]:
    """Decode Dukascopy minute candles.

    The binary record layout is seconds, open, close, low, high, volume.
    """
    raw = lzma.decompress(payload)
    record_size = struct.calcsize(">IIIIIf")
    if len(raw) % record_size:
        raise ValueError(
            f"invalid BI5 payload size: {len(raw)} is not divisible by {record_size}"
        )

    midnight = dt.datetime.combine(day, dt.time())
    rows = []
    for seconds, open_raw, close_raw, low_raw, high_raw, volume in struct.iter_unpack(
        ">IIIIIf", raw
    ):
        open_value = open_raw / decimal_factor
        close_value = close_raw / decimal_factor
        low_value = low_raw / decimal_factor
        high_value = high_raw / decimal_factor
        if (
            seconds >= 24 * 60 * 60
            or low_value > min(open_value, close_value)
            or high_value < max(open_value, close_value)
            or low_value > high_value
        ):
            raise ValueError(
                f"impossible OHLC record for {day}: "
                f"{open_value}, {close_value}, {low_value}, {high_value}"
            )
        rows.append(
            {
                "timestamp_utc": midnight + dt.timedelta(seconds=seconds),
                "open": open_value,
                "close": close_value,
                "low": low_value,
                "high": high_value,
                "volume": float(volume),
            }
        )
    return rows


def _download(
    url: str,
    timeout: int = 20,
    retries: int = 3,
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
            time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"download failed after {retries} attempts: {url}") from last_error


def load_candle_payload(day: dt.date, side: str, cache_dir: Path) -> bytes | None:
    path = (
        cache_dir
        / INSTRUMENT
        / f"{day.year:04d}"
        / f"{day.month:02d}"
        / f"{day.day:02d}_{side}.bi5"
    )
    missing_path = path.with_suffix(".missing")
    if path.exists():
        return path.read_bytes()
    if missing_path.exists():
        return None

    payload = _download(candle_url(day, side))
    path.parent.mkdir(parents=True, exist_ok=True)
    if payload is None:
        missing_path.touch()
        return None
    path.write_bytes(payload)
    return payload


def load_day(day: dt.date, cache_dir: Path) -> tuple[dt.date, list[dict] | None]:
    parsed = {}
    for side in SIDES:
        payload = load_candle_payload(day, side, cache_dir)
        if payload is None:
            return day, None
        parsed[side] = decode_candles(payload, day)

    bid = {row["timestamp_utc"]: row for row in parsed["BID"]}
    ask = {row["timestamp_utc"]: row for row in parsed["ASK"]}
    common = sorted(set(bid) & set(ask))
    rows = []
    for timestamp in common:
        bid_row = bid[timestamp]
        ask_row = ask[timestamp]
        bid_close = bid_row["close"]
        ask_close = ask_row["close"]
        if bid_close <= 0 or ask_close <= 0 or bid_close > ask_close:
            continue
        rows.append(
            {
                "timestamp_utc": timestamp,
                "bid_close": bid_close,
                "ask_close": ask_close,
                "mid_close": (bid_close + ask_close) / 2,
                "spread": ask_close - bid_close,
                "bid_volume": bid_row["volume"],
                "ask_volume": ask_row["volume"],
            }
        )
    return day, rows or None


def _calendar_days(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    current = start
    while current <= end:
        yield current
        current += dt.timedelta(days=1)


def download_history(
    start: dt.date,
    end: dt.date,
    cache_dir: Path,
    workers: int = 12,
) -> tuple[pd.DataFrame, dict]:
    processed_path = cache_dir / f"{INSTRUMENT}_{start}_{end}_mid.csv.gz"
    if processed_path.exists():
        frame = pd.read_csv(processed_path, parse_dates=["timestamp_utc"])
        frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True)
        quality = {
            "calendar_days_requested": (end - start).days + 1,
            "calendar_days_with_data": int(
                frame["timestamp_utc"].dt.date.nunique()
            ),
            "source": "processed cache",
        }
        return frame, quality

    days = list(_calendar_days(start, end))
    rows = []
    days_with_data = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(load_day, day, cache_dir): day for day in days
        }
        for completed, future in enumerate(as_completed(futures), 1):
            _, day_rows = future.result()
            if day_rows:
                rows.extend(day_rows)
                days_with_data += 1
            if completed % 100 == 0 or completed == len(futures):
                print(
                    f"Downloaded {completed}/{len(futures)} calendar days; "
                    f"{days_with_data} have matched bid/ask data",
                    flush=True,
                )

    if not rows:
        raise RuntimeError("no matched Dukascopy bid/ask candles were downloaded")
    frame = pd.DataFrame(rows).sort_values("timestamp_utc")
    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True)
    frame = frame.drop_duplicates("timestamp_utc", keep="last")
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(processed_path, index=False, compression="gzip")
    quality = {
        "calendar_days_requested": len(days),
        "calendar_days_with_data": days_with_data,
        "source": "fresh download",
    }
    return frame, quality


def _price_at(prices: pd.Series, timestamp: pd.Timestamp) -> float | None:
    value = prices.get(timestamp)
    if value is None or pd.isna(value) or float(value) <= 0:
        return None
    return float(value)


def event_returns_for_date(
    prices: pd.Series,
    day: dt.date,
) -> dict | None:
    boundary = pd.Timestamp(dt.datetime.combine(day, dt.time()), tz=UTC) - pd.Timedelta(
        minutes=1
    )
    required = {
        "pre": boundary - pd.Timedelta(minutes=30),
        "boundary": boundary,
    }
    required.update(
        {
            f"horizon_{h}": boundary + pd.Timedelta(minutes=h)
            for h in HORIZONS
        }
    )
    values = {key: _price_at(prices, timestamp) for key, timestamp in required.items()}
    if any(value is None for value in values.values()):
        return None

    base = values["boundary"]
    row = {
        "date": day.isoformat(),
        "boundary_price": base,
        "pre_30m_bps": 10_000 * log(base / values["pre"]),
    }
    for horizon in HORIZONS:
        row[f"return_{horizon}m_bps"] = (
            10_000 * log(values[f"horizon_{horizon}"] / base)
        )
    return row


def build_event_table(
    prices: pd.Series,
    start: dt.date,
    end: dt.date,
) -> pd.DataFrame:
    rows = []
    for day in _calendar_days(start, end):
        if day.weekday() >= 5:
            continue
        row = event_returns_for_date(prices, day)
        if row is not None:
            rows.append(row)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("no complete weekday 08:00 event windows")
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def _beijing_to_utc(day: dt.date, minute_of_day: int) -> pd.Timestamp:
    local = dt.datetime.combine(day, dt.time(), tzinfo=BEIJING) + dt.timedelta(
        minutes=minute_of_day
    )
    return pd.Timestamp(local.astimezone(UTC))


def build_clock_placebos(
    prices: pd.Series,
    event_dates: Iterable[pd.Timestamp],
) -> pd.DataFrame:
    rows = []
    for date_value in event_dates:
        day = pd.Timestamp(date_value).date()
        for minute_of_day in range(0, 24 * 60, 30):
            start = _beijing_to_utc(day, minute_of_day)
            boundary = start - pd.Timedelta(minutes=1)
            end = start + pd.Timedelta(minutes=29)
            start_price = _price_at(prices, boundary)
            end_price = _price_at(prices, end)
            if start_price is None or end_price is None:
                continue
            rows.append(
                {
                    "date": pd.Timestamp(day),
                    "clock_minute": minute_of_day,
                    "clock_label": f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}",
                    "return_30m_bps": 10_000 * log(end_price / start_price),
                }
            )
    return pd.DataFrame(rows)


def build_event_curve(
    prices: pd.Series,
    event_dates: Iterable[pd.Timestamp],
    start_offset: int = -60,
    end_offset: int = 120,
) -> pd.DataFrame:
    rows = []
    for date_value in event_dates:
        day = pd.Timestamp(date_value).date()
        boundary = pd.Timestamp(
            dt.datetime.combine(day, dt.time()), tz=UTC
        ) - pd.Timedelta(minutes=1)
        base = _price_at(prices, boundary)
        if base is None:
            continue
        for offset in range(start_offset, end_offset + 1):
            price = _price_at(prices, boundary + pd.Timedelta(minutes=offset))
            if price is not None:
                rows.append(
                    {
                        "date": pd.Timestamp(day),
                        "minute_offset": offset,
                        "return_bps": 10_000 * log(price / base),
                    }
                )
    curve = pd.DataFrame(rows)
    summary = (
        curve.groupby("minute_offset")["return_bps"]
        .agg(["count", "mean", "median", "std"])
        .reset_index()
    )
    summary["standard_error"] = summary["std"] / np.sqrt(summary["count"])
    summary["ci_low"] = summary["mean"] - 1.96 * summary["standard_error"]
    summary["ci_high"] = summary["mean"] + 1.96 * summary["standard_error"]
    return summary


def load_reserve_changes(workbook: Path, as_of: dt.date) -> dict[str, float]:
    wb = load_workbook(workbook, read_only=True, data_only=True)
    ws = wb["官方黄金储备"]
    values = []
    for row in ws.iter_rows(min_row=4, values_only=True):
        date_value, reserve_value = row[0], row[1]
        if not isinstance(date_value, (dt.datetime, dt.date)):
            continue
        day = date_value.date() if isinstance(date_value, dt.datetime) else date_value
        if day > as_of or reserve_value is None:
            continue
        values.append((pd.Period(day, freq="M"), float(reserve_value)))
    values.sort()
    changes = {}
    previous = None
    for month, value in values:
        if previous is not None:
            changes[str(month)] = value - previous
        previous = value
    return changes


def fetch_sse_calendar(start: dt.date, end: dt.date) -> dict[str, bool]:
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        return {}
    import tushare as ts

    pro = ts.pro_api(token)
    frame = pro.trade_cal(
        exchange="SSE",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        fields="cal_date,is_open",
    )
    return {
        dt.datetime.strptime(str(row.cal_date), "%Y%m%d").date().isoformat(): bool(
            row.is_open
        )
        for row in frame.itertuples()
    }


def block_bootstrap_mean_ci(
    values: pd.Series,
    dates: pd.Series,
    iterations: int = 3000,
    seed: int = 20260723,
) -> tuple[float, float]:
    valid = pd.DataFrame({"value": values, "date": dates}).dropna()
    valid["month"] = pd.to_datetime(valid["date"]).dt.to_period("M")
    blocks = [group["value"].to_numpy() for _, group in valid.groupby("month")]
    if len(blocks) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = np.empty(iterations)
    for i in range(iterations):
        selected = rng.integers(0, len(blocks), size=len(blocks))
        sample = np.concatenate([blocks[j] for j in selected])
        means[i] = sample.mean()
    low, high = np.quantile(means, [0.025, 0.975])
    return float(low), float(high)


def summarize_returns(values: pd.Series, dates: pd.Series) -> dict:
    valid = pd.DataFrame({"value": values, "date": dates}).dropna()
    sample = valid["value"].astype(float).to_numpy()
    if len(sample) < 3:
        return {
            "n": int(len(sample)),
            "mean_bps": float(np.mean(sample)) if len(sample) else None,
        }

    model = sm.OLS(sample, np.ones(len(sample))).fit(
        cov_type="HAC", cov_kwds={"maxlags": 5}
    )
    nonzero = sample[sample != 0]
    positive_count = int((nonzero > 0).sum())
    sign_p = (
        float(stats.binomtest(positive_count, len(nonzero), 0.5).pvalue)
        if len(nonzero)
        else float("nan")
    )
    ci_low, ci_high = block_bootstrap_mean_ci(
        valid["value"], valid["date"]
    )
    return {
        "n": int(len(sample)),
        "mean_bps": float(sample.mean()),
        "median_bps": float(np.median(sample)),
        "positive_share": float((sample > 0).mean()),
        "standard_deviation_bps": float(sample.std(ddof=1)),
        "hac_t": float(model.tvalues[0]),
        "hac_p": float(model.pvalues[0]),
        "sign_test_p": sign_p,
        "month_block_ci_low": ci_low,
        "month_block_ci_high": ci_high,
    }


def analyze(
    minute_frame: pd.DataFrame,
    start: dt.date,
    end: dt.date,
    reserve_changes: dict[str, float],
    sse_calendar: dict[str, bool],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    prices = minute_frame.set_index("timestamp_utc")["mid_close"].sort_index()
    event = build_event_table(prices, start, end)
    event["month"] = event["date"].dt.to_period("M").astype(str)
    event["reserve_delta_ton"] = event["month"].map(reserve_changes)
    event["reserve_regime"] = np.select(
        [
            event["reserve_delta_ton"] > 0.5,
            event["reserve_delta_ton"].abs() <= 0.5,
            event["reserve_delta_ton"] < -0.5,
        ],
        ["buy", "flat", "sell"],
        default=None,
    )
    event["sse_open"] = event["date"].dt.date.astype(str).map(sse_calendar)

    clock = build_clock_placebos(prices, event["date"])
    clock_summary = (
        clock.groupby(["clock_minute", "clock_label"])["return_30m_bps"]
        .agg(["count", "mean", "median", "std"])
        .reset_index()
    )
    clock_summary["standard_error"] = clock_summary["std"] / np.sqrt(
        clock_summary["count"]
    )
    clock_summary["ci_low"] = (
        clock_summary["mean"] - 1.96 * clock_summary["standard_error"]
    )
    clock_summary["ci_high"] = (
        clock_summary["mean"] + 1.96 * clock_summary["standard_error"]
    )

    asian_peer = (
        clock.loc[
            clock["clock_minute"].between(6 * 60, 12 * 60)
            & (clock["clock_minute"] != 8 * 60)
        ]
        .groupby("date")["return_30m_bps"]
        .mean()
        .rename("asian_peer_30m_bps")
    )
    event = event.merge(asian_peer, left_on="date", right_index=True, how="left")
    event["post_minus_pre_bps"] = (
        event["return_30m_bps"] - event["pre_30m_bps"]
    )
    event["post_minus_asian_peer_bps"] = (
        event["return_30m_bps"] - event["asian_peer_30m_bps"]
    )

    curve = build_event_curve(prices, event["date"])
    results = {
        "sample": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "event_days": int(len(event)),
            "timezone": "Asia/Shanghai (UTC+8, no DST)",
            "price": "Dukascopy XAU/USD one-minute bid/ask midpoint",
            "primary_window": "07:59 close to 08:29 close Beijing time",
        },
        "horizons": {
            f"{horizon}m": summarize_returns(
                event[f"return_{horizon}m_bps"], event["date"]
            )
            for horizon in HORIZONS
        },
        "paired_controls": {
            "post_minus_pre_30m": summarize_returns(
                event["post_minus_pre_bps"], event["date"]
            ),
            "post_minus_asian_peer_30m": summarize_returns(
                event["post_minus_asian_peer_bps"], event["date"]
            ),
        },
        "subsamples": {},
    }

    for name, mask in {
        "sse_open": event["sse_open"] == True,  # noqa: E712
        "sse_closed_weekday": event["sse_open"] == False,  # noqa: E712
        "reserve_buy_month": event["reserve_regime"] == "buy",
        "reserve_flat_month": event["reserve_regime"] == "flat",
        "since_2024_11": event["date"] >= pd.Timestamp("2024-11-01"),
    }.items():
        subset = event.loc[mask]
        results["subsamples"][name] = summarize_returns(
            subset["return_30m_bps"], subset["date"]
        )

    target = clock_summary.loc[clock_summary["clock_minute"] == 8 * 60].iloc[0]
    absolute_means = clock_summary["mean"].abs()
    results["clock_placebo"] = {
        "target_mean_bps": float(target["mean"]),
        "rank_descending": int(
            clock_summary["mean"].rank(ascending=False, method="min")[
                clock_summary["clock_minute"] == 8 * 60
            ].iloc[0]
        ),
        "clock_count": int(len(clock_summary)),
        "two_sided_empirical_p": float(
            (absolute_means >= abs(float(target["mean"]))).mean()
        ),
    }

    monthly = event.dropna(subset=["reserve_regime"]).copy()
    monthly["month_key"] = monthly["date"].dt.to_period("M")
    monthly = (
        monthly.groupby(["month_key", "reserve_regime"])["return_30m_bps"]
        .mean()
        .reset_index()
    )
    buy = monthly.loc[monthly["reserve_regime"] == "buy", "return_30m_bps"]
    flat = monthly.loc[monthly["reserve_regime"] == "flat", "return_30m_bps"]
    if len(buy) >= 2 and len(flat) >= 2:
        comparison = stats.ttest_ind(buy, flat, equal_var=False)
        results["reserve_regime_difference"] = {
            "buy_months": int(len(buy)),
            "flat_months": int(len(flat)),
            "buy_mean_bps": float(buy.mean()),
            "flat_mean_bps": float(flat.mean()),
            "difference_bps": float(buy.mean() - flat.mean()),
            "welch_p": float(comparison.pvalue),
        }

    open_returns = event.loc[event["sse_open"] == True, "return_30m_bps"]  # noqa: E712
    closed_returns = event.loc[event["sse_open"] == False, "return_30m_bps"]  # noqa: E712
    if len(open_returns) >= 2 and len(closed_returns) >= 2:
        daily_test = stats.ttest_ind(open_returns, closed_returns, equal_var=False)
        monthly_open_closed = (
            event.dropna(subset=["sse_open"])
            .groupby(["month", "sse_open"])["return_30m_bps"]
            .mean()
            .unstack()
            .dropna()
        )
        monthly_differences = (
            monthly_open_closed[True] - monthly_open_closed[False]
        )
        monthly_test = stats.ttest_1samp(monthly_differences, 0)
        results["sse_open_closed_difference"] = {
            "open_days": int(len(open_returns)),
            "closed_weekdays": int(len(closed_returns)),
            "daily_difference_bps": float(
                open_returns.mean() - closed_returns.mean()
            ),
            "daily_welch_p": float(daily_test.pvalue),
            "paired_months": int(len(monthly_differences)),
            "paired_month_mean_difference_bps": float(
                monthly_differences.mean()
            ),
            "paired_month_median_difference_bps": float(
                monthly_differences.median()
            ),
            "paired_month_p": float(monthly_test.pvalue),
        }

    primary_values = event["return_30m_bps"]
    lower, upper = primary_values.quantile([0.01, 0.99])
    iso = event["date"].dt.isocalendar()
    event_weeks = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    leave_week_out = []
    for week in sorted(event_weeks.unique()):
        remaining = primary_values[event_weeks != week]
        leave_week_out.append(
            {
                "week": week,
                "mean_without_week_bps": float(remaining.mean()),
                "influence_bps": float(primary_values.mean() - remaining.mean()),
            }
        )
    influential = max(leave_week_out, key=lambda row: abs(row["influence_bps"]))
    results["sensitivity"] = {
        "winsorized_1pct_mean_bps": float(primary_values.clip(lower, upper).mean()),
        "most_influential_week": influential["week"],
        "mean_without_most_influential_week_bps": influential[
            "mean_without_week_bps"
        ],
        "most_influential_week_contribution_bps": influential["influence_bps"],
    }

    return event, clock_summary, curve, results


def data_quality_summary(
    minute_frame: pd.DataFrame,
    download_quality: dict,
    event: pd.DataFrame,
) -> dict:
    timestamps = minute_frame["timestamp_utc"]
    spread = minute_frame["spread"]
    duplicated = int(timestamps.duplicated().sum())
    quality = {
        **download_quality,
        "minute_rows": int(len(minute_frame)),
        "first_timestamp_utc": str(timestamps.min()),
        "last_timestamp_utc": str(timestamps.max()),
        "duplicate_timestamps": duplicated,
        "nonpositive_or_crossed_spreads": int((spread <= 0).sum()),
        "median_spread_usd_per_oz": float(spread.median()),
        "p99_spread_usd_per_oz": float(spread.quantile(0.99)),
        "complete_weekday_event_windows": int(len(event)),
    }
    wind_path = ROOT / "data" / "market" / "wind_daily.csv"
    if wind_path.exists():
        daily_mid = minute_frame[["timestamp_utc", "mid_close"]].copy()
        daily_mid["date"] = daily_mid["timestamp_utc"].dt.strftime("%Y-%m-%d")
        daily_mid = daily_mid.groupby("date").tail(1)[["date", "mid_close"]]
        wind = pd.read_csv(wind_path, usecols=["date", "gold_price"]).dropna()
        overlap = daily_mid.merge(wind, on="date")
        if len(overlap) >= 2:
            quality["wind_daily_crosscheck"] = {
                "overlap_days": int(len(overlap)),
                "level_correlation": float(
                    overlap[["mid_close", "gold_price"]].corr().iloc[0, 1]
                ),
                "note": (
                    "Only validates instrument identity and price scale; "
                    "daily cutoff differences are not an intraday reconciliation."
                ),
            }
    return quality


def _fmt_stat(stat: dict) -> str:
    if not stat or stat.get("mean_bps") is None:
        return "不可用"
    return (
        f"{stat['mean_bps']:+.2f} 个基点；月度分块 95% 区间 "
        f"[{stat.get('month_block_ci_low', float('nan')):+.2f}, "
        f"{stat.get('month_block_ci_high', float('nan')):+.2f}]；"
        f"上涨占比 {stat.get('positive_share', float('nan')):.1%}；"
        f"样本 {stat.get('n', 0)} 天"
    )


def conclusion_label(results: dict) -> str:
    primary = results["horizons"]["30m"]
    paired = results["paired_controls"]["post_minus_asian_peer_30m"]
    placebo = results["clock_placebo"]
    if (
        primary.get("month_block_ci_low", -1) > 0
        and paired.get("month_block_ci_low", -1) > 0
        and placebo.get("two_sided_empirical_p", 1) <= 0.05
    ):
        return "存在较稳健的北京时间 8 点后偏强效应，但不能据此识别人民银行交易"
    return "现有分钟数据未能给出稳健证据，支持“北京时间 8 点后通常偏强”"


def write_outputs(
    output_dir: Path,
    as_of: dt.date,
    event: pd.DataFrame,
    clock: pd.DataFrame,
    curve: pd.DataFrame,
    results: dict,
    quality: dict,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = as_of.isoformat()
    paths = {
        "event": output_dir / f"{prefix}_伦敦金8点效应_事件样本.csv",
        "clock": output_dir / f"{prefix}_伦敦金8点效应_时点安慰剂.csv",
        "curve": output_dir / f"{prefix}_伦敦金8点效应_事件曲线.csv",
        "json": output_dir / f"{prefix}_伦敦金8点效应_检验结果.json",
        "report": output_dir / f"{prefix}_伦敦金8点效应_研究结论.md",
    }
    event.to_csv(paths["event"], index=False, quoting=csv.QUOTE_MINIMAL)
    clock.to_csv(paths["clock"], index=False, quoting=csv.QUOTE_MINIMAL)
    curve.to_csv(paths["curve"], index=False, quoting=csv.QUOTE_MINIMAL)
    payload = {"data_quality": quality, **results}
    paths["json"].write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    primary = results["horizons"]["30m"]
    paired_pre = results["paired_controls"]["post_minus_pre_30m"]
    paired_peer = results["paired_controls"]["post_minus_asian_peer_30m"]
    open_days = results["subsamples"].get("sse_open", {})
    closed_days = results["subsamples"].get("sse_closed_weekday", {})
    buy_months = results["subsamples"].get("reserve_buy_month", {})
    flat_months = results["subsamples"].get("reserve_flat_month", {})
    recent = results["subsamples"].get("since_2024_11", {})
    placebo = results["clock_placebo"]
    one_minute = results["horizons"]["1m"]
    open_closed = results.get("sse_open_closed_difference", {})
    sensitivity = results.get("sensitivity", {})
    report = f"""# 伦敦金北京时间 8 点效应检验

> 样本截至 {results['sample']['end']}；一分钟数据；收益单位为基点（万分之一）。

## 结论

**{conclusion_label(results)}。**

主检验是北京时间 07:59 收盘到 08:29 收盘的 30 分钟收益。全样本结果为
{_fmt_stat(primary)}。8 点在全天 48 个半小时时点中按平均收益从高到低排第
{placebo['rank_descending']}，基于 48 个时点的双侧经验概率为
{placebo['two_sided_empirical_p']:.3f}。

最贴近“8 点一开工就买”的第一个分钟，平均收益反而是
{_fmt_stat(one_minute)}。另外，最有影响力的单周是
{sensitivity.get('most_influential_week', '—')}；剔除该周后，30 分钟均值降为
{sensitivity.get('mean_without_most_influential_week_bps', float('nan')):+.2f}
个基点。这说明全样本轻微正均值并不稳定。

这项检验最多能判断“8 点附近是否有可重复的价格效应”。它不能看到交易对手，
因此不能把任何价格变化直接归因于人民银行。

## 三项反证

- 与同一日 07:30—08:00 相比：08:00 后的超额为 {_fmt_stat(paired_pre)}。
- 与同一日 06:00—12:00 其他半小时窗口相比：8 点窗口的超额为
  {_fmt_stat(paired_peer)}。
- 中国交易日：{_fmt_stat(open_days)}；中国休市但伦敦金仍交易的工作日：
  {_fmt_stat(closed_days)}。

按日直接比较，中国交易日比休市工作日高
{open_closed.get('daily_difference_bps', float('nan')):+.2f} 个基点，
但休市日集中在节假日。改为只在同一个月内比较后，只有
{open_closed.get('paired_months', 0)} 个月同时包含两类日期，月度配对差异的
检验概率为 {open_closed.get('paired_month_p', float('nan')):.3f}，只能视为
方向性线索，不能当作稳健证据。

如果效应确由中国官方机构规律性开工带来，它应在中国交易日更明显，也应优于
同一亚洲时段的其他窗口；否则更可能是普通的日内季节性、美元或贵金属共同波动。

## 与官方储备月份的关系

- 中国黄金储备净增加月份：{_fmt_stat(buy_months)}。
- 储备基本不变月份：{_fmt_stat(flat_months)}。
- 2024 年 11 月以后：{_fmt_stat(recent)}。

月末储备变化只适合做弱分组：官方月度数据不披露具体交易日、交易时点或执行账户。
“净增加月份更强”仍不能证明 8 点的买单来自人民银行；“没有更强”则会削弱该解释。

## 方法

1. 数据使用 Dukascopy 的 XAU/USD 一分钟 BID 与 ASK K 线，取买卖中间价；
   原始时间戳为 UTC，固定转换为北京时间 UTC+8。
2. 事前固定 30 分钟为主窗口；1、5、15、60 分钟为辅助窗口。
3. 同时报均值、中位数、上涨占比、Newey-West 检验和按月份分块的自助法区间。
4. 全天每 30 分钟扫描仅作安慰剂并显式计入多重比较，不据最强时点倒推故事。
5. 中国工作日代理采用上交所交易日历；官方储备月份来自项目工作簿
   `官方黄金储备` sheet，并过滤样本截至日后的未来月份。

## 数据质量

- 分钟记录 {quality['minute_rows']:,} 条，覆盖
  {quality['first_timestamp_utc']} 至 {quality['last_timestamp_utc']}。
- 完整的工作日 8 点事件样本 {quality['complete_weekday_event_windows']:,} 天；
  重复时间戳 {quality['duplicate_timestamps']} 条。
- 买卖价倒挂或非正点差 {quality['nonpositive_or_crossed_spreads']} 条；
  中位点差 {quality['median_spread_usd_per_oz']:.3f} 美元/盎司，
  99 分位点差 {quality['p99_spread_usd_per_oz']:.3f} 美元/盎司。
- 与项目 Wind 日频金价重叠
  {quality.get('wind_daily_crosscheck', {}).get('overlap_days', 0)} 天，
  价格水平相关系数
  {quality.get('wind_daily_crosscheck', {}).get('level_correlation', float('nan')):.6f}；
  该核对只确认品种与价格量级，不把不同日切口当作分钟线一致性证明。

## 证据边界

- Dukascopy 是可复现的场外报价源，不是整个伦敦现货市场的完整成交记录。
- 月度储备变化不是日内成交标签；不存在可公开验证的人民银行逐笔订单。
- 这是一项观察性事件研究。统计显著也不等于因果，更不等于可交易收益。
"""
    paths["report"].write_text(report, encoding="utf-8")
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument(
        "--end",
        default=(dt.date.today() - dt.timedelta(days=1)).isoformat(),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("/private/tmp/gold_8am_dukascopy_cache"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "outputs" / "research",
    )
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument(
        "--workbook",
        type=Path,
        default=ROOT / "data" / "招商证券：黄金图表整理2607.xlsx",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    if end < start:
        raise SystemExit("--end must be on or after --start")

    minute_frame, download_quality = download_history(
        start - dt.timedelta(days=1),
        end,
        args.cache_dir,
        workers=args.workers,
    )
    reserve_changes = load_reserve_changes(args.workbook, end)
    sse_calendar = fetch_sse_calendar(start, end)
    event, clock, curve, results = analyze(
        minute_frame,
        start,
        end,
        reserve_changes,
        sse_calendar,
    )
    quality = data_quality_summary(minute_frame, download_quality, event)
    paths = write_outputs(
        args.output_dir,
        dt.date.today(),
        event,
        clock,
        curve,
        results,
        quality,
    )
    print(json.dumps({key: str(path) for key, path in paths.items()}, ensure_ascii=False))
    print(conclusion_label(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
