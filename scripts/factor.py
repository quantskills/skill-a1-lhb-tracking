"""Production entry for A1 Longhubang capital tracking factor."""

from __future__ import annotations

import argparse
import importlib
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd

from a1_core import DEFAULT_LOOKBACK_DAYS, DEFAULT_MIN_HISTORY, compute_a1_factor, make_sample_data
from akshare_source import fetch_akshare_inputs


def _first_value(frame: Any, column: str) -> str | None:
    if isinstance(frame, pd.DataFrame) and column in frame.columns and len(frame) > 0:
        value = frame.iloc[0][column]
        return None if pd.isna(value) else str(value)
    if isinstance(frame, pd.Series) and len(frame) > 0:
        value = frame.iloc[0]
        return None if pd.isna(value) else str(value)
    if isinstance(frame, (list, tuple)) and frame:
        value = frame[0]
        return None if pd.isna(value) else str(value)
    if isinstance(frame, (str, int)):
        return str(frame)
    return None


def _load_panda_data():
    try:
        return importlib.import_module("panda_data")
    except ImportError as exc:
        raise RuntimeError(
            "panda_data is not installed in this Python environment. "
            "Install PandaAI data or run with --sample for a local smoke test."
        ) from exc


def _env_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return ""


def _init_panda_data_if_configured(
    panda_data: Any,
    username: str | None = None,
    password: str | None = None,
    base_url: str | None = None,
) -> None:
    resolved_username = username or _env_value("PANDADATA_USERNAME", "DEFAULT_USERNAME")
    resolved_password = password or _env_value("PANDADATA_PASSWORD", "DEFAULT_PASSWORD")
    resolved_base_url = base_url or _env_value("PANDADATA_BASE_URL", "JAVA_SERVICE_BASE_URL")
    if not resolved_base_url:
        resolved_base_url = "http://pandadata.pandaaiquant.com"

    if not resolved_username or not resolved_password:
        return

    panda_data.init_token(
        username=resolved_username,
        password=resolved_password,
        base_url=resolved_base_url,
    )


def _concat_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    usable = [frame for frame in frames if isinstance(frame, pd.DataFrame) and len(frame) > 0]
    if not usable:
        return pd.DataFrame()
    return pd.concat(usable, ignore_index=True)


def _trade_dates_from_frame(trade_cal: pd.DataFrame, start_date: str, end_date: str) -> list[str]:
    if not isinstance(trade_cal, pd.DataFrame) or len(trade_cal) == 0:
        return [start_date]
    date_col = "nature_date" if "nature_date" in trade_cal.columns else "date"
    dates = trade_cal[date_col].dropna().astype(str).str.replace("-", "", regex=False).str[:8]
    return sorted(date for date in dates.tolist() if start_date <= date <= end_date)


def _call_with_retries(label: str, func: Any, attempts: int = 3, delay_seconds: float = 2.0, **kwargs: Any) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func(**kwargs)
        except Exception as exc:
            last_error = exc
            if attempt == attempts:
                break
            wait = delay_seconds * attempt
            print(f"Retrying {label}: attempt {attempt + 1}/{attempts} after {type(exc).__name__}: {str(exc)[:160]}")
            time.sleep(wait)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}") from last_error


def _fetch_panda_lhb_by_day(panda_data: Any, dates: list[str], lhb_type: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    list_frames: list[pd.DataFrame] = []
    detail_frames: list[pd.DataFrame] = []
    for date in dates:
        common = {"start_date": date, "end_date": date, "fields": []}
        if lhb_type:
            common["type"] = lhb_type
        list_frame = _call_with_retries(
            f"get_lhb_list {date}",
            panda_data.get_lhb_list,
            symbol="",
            **common,
        )
        detail_frame = _call_with_retries(
            f"get_lhb_detail {date}",
            panda_data.get_lhb_detail,
            symbol="",
            side="buy",
            **common,
        )
        if isinstance(list_frame, pd.DataFrame) and len(list_frame) > 0:
            list_frames.append(list_frame)
        if isinstance(detail_frame, pd.DataFrame) and len(detail_frame) > 0:
            detail_frames.append(detail_frame)
    return _concat_frames(list_frames), _concat_frames(detail_frames)


def _fetch_panda_stock_daily(
    panda_data: Any,
    symbols: list[str],
    start_date: str,
    end_date: str,
    chunk_size: int = 100,
    indicator: str = "",
    include_st: bool = True,
) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for idx in range(0, len(symbols), chunk_size):
        chunk = symbols[idx : idx + chunk_size]
        frame = _call_with_retries(
            f"get_stock_daily chunk {idx // chunk_size + 1}",
            panda_data.get_stock_daily,
            symbol=chunk,
            start_date=start_date,
            end_date=end_date,
            fields=[],
            indicator=indicator,
            st=include_st,
        )
        if isinstance(frame, pd.DataFrame) and len(frame) > 0:
            frames.append(frame)
    return _concat_frames(frames)


def _fetch_panda_stock_daily_all(
    panda_data: Any,
    start_date: str,
    end_date: str,
    indicator: str = "",
    include_st: bool = True,
) -> pd.DataFrame:
    frame = _call_with_retries(
        "get_stock_daily all-stock",
        panda_data.get_stock_daily,
        symbol="",
        start_date=start_date,
        end_date=end_date,
        fields=[],
        indicator=indicator,
        st=include_st,
    )
    return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()


def _dedupe_and_rerank(factors: pd.DataFrame) -> pd.DataFrame:
    if len(factors) == 0:
        return factors
    factors = factors.sort_values(["trade_date", "ts_code", "factor_value"], ascending=[True, True, False])
    factors = factors.drop_duplicates(["trade_date", "ts_code", "factor_id"], keep="first").copy()
    factors["score"] = factors.groupby("trade_date")["factor_value"].rank(pct=True).mul(100).round(4)
    factors["rank"] = factors.groupby("trade_date")["factor_value"].rank(method="first", ascending=False).astype(int)
    factors["signal"] = factors.apply(
        lambda row: "buy" if float(row["score"]) >= 80.0 and float(row["confidence"]) >= 0.35 else "hold",
        axis=1,
    )
    return factors.sort_values(["trade_date", "rank", "ts_code"]).reset_index(drop=True)


def fetch_panda_inputs(
    start_date: str | None,
    end_date: str | None,
    exchange: str,
    lookback_days: int,
    lhb_type: str,
    panda_username: str | None = None,
    panda_password: str | None = None,
    panda_base_url: str | None = None,
    full_stock_daily: bool = False,
    stock_indicator: str = "",
    include_st: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, str, str]:
    panda_data = _load_panda_data()
    _init_panda_data_if_configured(panda_data, panda_username, panda_password, panda_base_url)

    if not end_date:
        last_trade = panda_data.get_last_trade_date(exchange=exchange)
        end_date = _first_value(last_trade, "date")
        if not end_date:
            raise RuntimeError("Could not resolve latest trade date from panda_data.get_last_trade_date")

    if not start_date:
        prev = _call_with_retries(
            "get_prev_trade_date",
            panda_data.get_prev_trade_date,
            date=end_date,
            exchange=exchange,
            n=lookback_days + 10,
        )
        start_date = _first_value(prev, "date")
        if not start_date:
            raise RuntimeError("Could not resolve start date from panda_data.get_prev_trade_date")

    trade_cal = _call_with_retries(
        "get_trade_cal",
        panda_data.get_trade_cal,
        start_date=start_date,
        end_date=end_date,
        exchange=exchange,
        is_trading_day=1,
        fields=[],
    )
    trade_dates = _trade_dates_from_frame(trade_cal, start_date, end_date)

    lhb_list, lhb_detail = _fetch_panda_lhb_by_day(panda_data, trade_dates, lhb_type)

    symbols = sorted(
        set(lhb_list.get("symbol", pd.Series(dtype=str)).dropna().astype(str))
        | set(lhb_detail.get("symbol", pd.Series(dtype=str)).dropna().astype(str))
    )
    if full_stock_daily:
        stock_daily = _fetch_panda_stock_daily_all(
            panda_data,
            start_date,
            end_date,
            indicator=stock_indicator,
            include_st=include_st,
        )
    else:
        stock_daily = _fetch_panda_stock_daily(
            panda_data,
            symbols,
            start_date,
            end_date,
            indicator=stock_indicator,
            include_st=include_st,
        )
    return lhb_list, lhb_detail, stock_daily, trade_cal, start_date, end_date


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute A1 Longhubang capital tracking factor")
    parser.add_argument("--source", choices=["panda", "akshare", "sample"], default="panda", help="Data source.")
    parser.add_argument("--start-date", help="YYYYMMDD. Defaults to lookback window before end date.")
    parser.add_argument("--end-date", help="YYYYMMDD. Defaults to latest trade date from PandaAI data.")
    parser.add_argument("--target-date", help="If set, keep only this LHB date in the output.")
    parser.add_argument("--latest-only", action="store_true", help="Keep only the latest LHB date found in the input window.")
    parser.add_argument("--exchange", default="SH", help="Trade calendar exchange. Default: SH.")
    parser.add_argument("--lhb-type", default="", help="Optional LHB type filter, e.g. G0007.")
    parser.add_argument("--panda-username", help="PandaAI data username. Prefer PANDADATA_USERNAME env var.")
    parser.add_argument("--panda-password", help="PandaAI data password. Prefer PANDADATA_PASSWORD env var.")
    parser.add_argument("--panda-base-url", help="PandaAI data service URL. Defaults to the package default.")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--min-history", type=int, default=DEFAULT_MIN_HISTORY)
    parser.add_argument("--data-version", default="A1-dev")
    parser.add_argument("--output", default="database.parquet", help="Output parquet path.")
    parser.add_argument("--sample", action="store_true", help="Run built-in sample data for smoke testing only.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    source = "sample" if args.sample else args.source

    if source == "sample":
        lhb_list, lhb_detail, stock_daily, trade_cal = make_sample_data()
        start_date = str(lhb_list["date"].min())
        end_date = str(lhb_list["date"].max())
    elif source == "akshare":
        lhb_list, lhb_detail, stock_daily, trade_cal, start_date, end_date = fetch_akshare_inputs(
            args.start_date,
            args.end_date,
            args.lookback_days,
        )
    else:
        lhb_list, lhb_detail, stock_daily, trade_cal, start_date, end_date = fetch_panda_inputs(
            args.start_date,
            args.end_date,
            args.exchange,
            args.lookback_days,
            args.lhb_type,
            args.panda_username,
            args.panda_password,
            args.panda_base_url,
        )

    factors = compute_a1_factor(
        lhb_list=lhb_list,
        lhb_detail=lhb_detail,
        stock_daily=stock_daily,
        trade_cal=trade_cal,
        lookback_days=args.lookback_days,
        min_history=args.min_history,
        data_version=args.data_version,
    )

    if args.target_date:
        factors = factors[factors["trade_date"].eq(args.target_date)].copy()
    elif args.latest_only and len(factors):
        latest_date = factors["trade_date"].max()
        factors = factors[factors["trade_date"].eq(latest_date)].copy()

    factors = _dedupe_and_rerank(factors)
    factors.to_parquet(output, index=False)
    print(
        f"A1 factor saved: {output} | source={source} | rows={len(factors)} | "
        f"range={start_date}-{end_date} | buy_signals={(factors['signal'] == 'buy').sum() if len(factors) else 0}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
