"""Real-data adapter for A1 using public Longhubang data exposed by AkShare.

This adapter is used when PandaAI data credentials are unavailable. It combines
Eastmoney's daily LHB stock table with daily broker-department buy lists, then
maps broker seats back to stock events by event date and stock name.

The fallback label uses Eastmoney's "after 1 day" field as an approximation;
official validation should use the PandaAI next-open-over-close label.
"""

from __future__ import annotations

import bisect
import contextlib
import importlib
import io
import re
from typing import Any

import numpy as np
import pandas as pd

from a1_core import normalize_date


COL_SEQ = "\u5e8f\u53f7"
COL_CODE = "\u4ee3\u7801"
COL_NAME = "\u540d\u79f0"
COL_EVENT_DATE = "\u4e0a\u699c\u65e5"
COL_REASON = "\u4e0a\u699c\u539f\u56e0"
COL_CLOSE = "\u6536\u76d8\u4ef7"
COL_LHB_AMOUNT = "\u9f99\u864e\u699c\u6210\u4ea4\u989d"
COL_TURNOVER = "\u6362\u624b\u7387"
COL_AFTER_1D = "\u4e0a\u699c\u540e1\u65e5"

COL_AGENCY = "\u8425\u4e1a\u90e8\u540d\u79f0"
COL_BUY_COUNT = "\u4e70\u5165\u4e2a\u80a1\u6570"
COL_SELL_COUNT = "\u5356\u51fa\u4e2a\u80a1\u6570"
COL_BUY_AMOUNT = "\u4e70\u5165\u603b\u91d1\u989d"
COL_SELL_AMOUNT = "\u5356\u51fa\u603b\u91d1\u989d"
COL_BUY_STOCKS = "\u4e70\u5165\u80a1\u7968"


def _load_akshare():
    try:
        return importlib.import_module("akshare")
    except ImportError as exc:
        raise RuntimeError("akshare is not installed. Install it or use --source panda with PandaAI credentials.") from exc


def _call_quietly(func: Any, **kwargs: Any) -> pd.DataFrame:
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
        return func(**kwargs)


def _symbol(value: object) -> str:
    text = "" if value is None or pd.isna(value) else str(value).strip()
    return text.zfill(6) if text.isdigit() else text


def _number(value: object) -> float:
    number = pd.to_numeric(value, errors="coerce")
    return float(number) if pd.notna(number) else float("nan")


def _split_stock_names(value: object) -> list[str]:
    if value is None or pd.isna(value):
        return []
    return [item for item in re.split(r"\s+", str(value).strip()) if item]


def _load_trade_calendar(ak: Any, start_date: str, end_date: str) -> pd.DataFrame:
    try:
        raw = _call_quietly(ak.tool_trade_date_hist_sina)
    except Exception:
        raw = pd.DataFrame()
    if raw is None or len(raw) == 0 or "trade_date" not in raw.columns:
        dates = pd.bdate_range(pd.to_datetime(start_date), pd.to_datetime(end_date))
        return pd.DataFrame({"nature_date": [date.strftime("%Y%m%d") for date in dates], "is_trade": 1, "exchange": "SH"})

    dates = raw["trade_date"].map(normalize_date)
    frame = pd.DataFrame({"nature_date": dates, "is_trade": 1, "exchange": "SH"}).dropna()
    return frame[frame["nature_date"].between(start_date, end_date)].sort_values("nature_date").reset_index(drop=True)


def _resolve_window(ak: Any, start_date: str | None, end_date: str | None, lookback_days: int) -> tuple[str, str]:
    resolved_end = normalize_date(end_date) or pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y%m%d")
    resolved_start = normalize_date(start_date)
    if resolved_start:
        return resolved_start, resolved_end

    try:
        raw_calendar = _call_quietly(ak.tool_trade_date_hist_sina)
        dates = sorted(date for date in raw_calendar["trade_date"].map(normalize_date).dropna().tolist() if date <= resolved_end)
    except Exception:
        dates = []
    if dates:
        end_idx = bisect.bisect_right(dates, resolved_end)
        start_idx = max(0, end_idx - lookback_days - 45)
        return dates[start_idx], resolved_end

    fallback_start = (pd.to_datetime(resolved_end) - pd.Timedelta(days=lookback_days + 75)).strftime("%Y%m%d")
    return fallback_start, resolved_end


def _build_lhb_list(raw_lhb: pd.DataFrame) -> pd.DataFrame:
    if raw_lhb is None or len(raw_lhb) == 0:
        return pd.DataFrame()

    frame = pd.DataFrame()
    frame["symbol"] = raw_lhb[COL_CODE].map(_symbol)
    frame["date"] = raw_lhb[COL_EVENT_DATE].map(normalize_date)
    frame["type"] = ""
    frame["reason"] = raw_lhb.get(COL_REASON, pd.Series("", index=raw_lhb.index)).fillna("").astype(str)
    frame["amount"] = pd.to_numeric(raw_lhb.get(COL_LHB_AMOUNT), errors="coerce")
    frame["volume"] = np.nan
    frame["turnover"] = pd.to_numeric(raw_lhb.get(COL_TURNOVER), errors="coerce")
    frame["name"] = raw_lhb[COL_NAME].fillna("").astype(str)
    frame["close"] = pd.to_numeric(raw_lhb.get(COL_CLOSE), errors="coerce")
    frame["next_open_premium"] = pd.to_numeric(raw_lhb.get(COL_AFTER_1D), errors="coerce") / 100.0
    return frame.dropna(subset=["symbol", "date"]).reset_index(drop=True)


def _build_lhb_detail(raw_seats: pd.DataFrame, lhb_list: pd.DataFrame) -> pd.DataFrame:
    if raw_seats is None or len(raw_seats) == 0 or len(lhb_list) == 0:
        return pd.DataFrame()

    events: dict[tuple[str, str], list[dict[str, object]]] = {}
    for event in lhb_list.itertuples(index=False):
        key = (str(event.date), str(getattr(event, "name", "")))
        events.setdefault(key, []).append(
            {
                "symbol": event.symbol,
                "date": event.date,
                "type": getattr(event, "type", ""),
                "reason": getattr(event, "reason", ""),
                "next_open_premium": getattr(event, "next_open_premium", np.nan),
            }
        )

    rows: list[dict[str, object]] = []
    for _, row in raw_seats.iterrows():
        date = normalize_date(row.get(COL_EVENT_DATE))
        agency = str(row.get(COL_AGENCY, "") or "").strip()
        if not date or not agency:
            continue

        names = _split_stock_names(row.get(COL_BUY_STOCKS))
        raw_buy_count = _number(row.get(COL_BUY_COUNT))
        raw_sell_count = _number(row.get(COL_SELL_COUNT))
        buy_count = int(raw_buy_count) if pd.notna(raw_buy_count) else len(names)
        sell_count = int(raw_sell_count) if pd.notna(raw_sell_count) else len(names)
        buy_denom = max(buy_count, len(names), 1)
        sell_denom = max(sell_count, len(names), 1)
        buy_each = _number(row.get(COL_BUY_AMOUNT)) / buy_denom
        sell_each = _number(row.get(COL_SELL_AMOUNT)) / sell_denom

        for name in names:
            for event in events.get((date, name), []):
                rows.append(
                    {
                        "symbol": event["symbol"],
                        "date": event["date"],
                        "type": event["type"],
                        "side": "buy",
                        "rank": row.get(COL_SEQ, np.nan),
                        "agency": agency,
                        "b_value": buy_each,
                        "s_value": sell_each,
                        "reason": event["reason"],
                        "next_open_premium": event["next_open_premium"],
                    }
                )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates(["symbol", "date", "agency"]).reset_index(drop=True)


def fetch_akshare_inputs(
    start_date: str | None,
    end_date: str | None,
    lookback_days: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, str, str]:
    """Fetch real public LHB inputs and convert them to A1's internal schema."""
    ak = _load_akshare()
    start_date, end_date = _resolve_window(ak, start_date, end_date, lookback_days)

    raw_lhb = _call_quietly(ak.stock_lhb_detail_em, start_date=start_date, end_date=end_date)
    raw_seats = _call_quietly(ak.stock_lhb_hyyyb_em, start_date=start_date, end_date=end_date)

    lhb_list = _build_lhb_list(raw_lhb)
    lhb_detail = _build_lhb_detail(raw_seats, lhb_list)
    trade_cal = _load_trade_calendar(ak, start_date, end_date)
    stock_daily = pd.DataFrame()
    return lhb_list, lhb_detail, stock_daily, trade_cal, start_date, end_date


def attach_akshare_next_open_premium(factors: pd.DataFrame, lhb_list: pd.DataFrame) -> pd.DataFrame:
    if len(factors) == 0 or len(lhb_list) == 0 or "next_open_premium" not in lhb_list.columns:
        out = factors.copy()
        out["next_open_premium"] = np.nan
        return out

    labels = lhb_list[["symbol", "date", "next_open_premium"]].copy()
    labels["next_open_premium"] = pd.to_numeric(labels["next_open_premium"], errors="coerce")
    labels = labels.dropna(subset=["symbol", "date", "next_open_premium"]).drop_duplicates(["symbol", "date"])
    out = factors.merge(labels, left_on=["ts_code", "trade_date"], right_on=["symbol", "date"], how="left")
    return out.drop(columns=[column for column in ["symbol", "date"] if column in out.columns])
