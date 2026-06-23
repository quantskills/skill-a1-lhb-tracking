"""Smoke tests for A1 core logic."""

from __future__ import annotations

import pandas as pd

from a1_core import (
    attach_full_universe_next_open_premium,
    attach_next_open_premium,
    compute_a1_factor,
    compute_backtest_metrics,
    make_sample_data,
    validate_factor_frame,
)


def test_sample_factor_output_is_valid() -> None:
    lhb_list, lhb_detail, stock_daily, trade_cal = make_sample_data()
    factors = compute_a1_factor(lhb_list, lhb_detail, stock_daily, trade_cal, lookback_days=6, min_history=2)
    errors = validate_factor_frame(factors, require_no_forward_label=True)
    assert errors == []
    assert len(factors) > 0
    assert factors["score"].between(0, 100).all()
    assert set(factors["signal"]).issubset({"buy", "hold"})
    assert "next_open_premium" not in factors.columns


def test_alpha_hot_seat_gets_history() -> None:
    lhb_list, lhb_detail, stock_daily, trade_cal = make_sample_data()
    factors = compute_a1_factor(lhb_list, lhb_detail, stock_daily, trade_cal, lookback_days=6, min_history=2)
    latest_alpha = factors[(factors["trade_date"].eq("20250114")) & (factors["ts_code"].eq("000001.SZ"))]
    assert len(latest_alpha) == 1
    row = latest_alpha.iloc[0]
    assert row["history_count"] >= 2
    assert row["confidence"] >= 0.35
    assert row["factor_value"] > 0


def test_duplicate_buy_side_agency_is_counted_once_per_event() -> None:
    lhb_list, lhb_detail, stock_daily, trade_cal = make_sample_data()
    duplicate = lhb_detail[
        (lhb_detail["date"].eq("20250114"))
        & (lhb_detail["symbol"].eq("000001.SZ"))
        & (lhb_detail["agency"].eq("Alpha Hot Seat"))
    ].iloc[0].copy()
    duplicate["b_value"] = 10_000_000
    lhb_detail = pd.concat([lhb_detail, pd.DataFrame([duplicate])], ignore_index=True)

    factors = compute_a1_factor(lhb_list, lhb_detail, stock_daily, trade_cal, lookback_days=6, min_history=2)
    row = factors[(factors["trade_date"].eq("20250114")) & (factors["ts_code"].eq("000001.SZ"))].iloc[0]

    assert row["agency_count"] == 1
    assert str(row["top_agencies"]).count("Alpha Hot Seat") == 1


def test_backtest_metrics_are_created() -> None:
    lhb_list, lhb_detail, stock_daily, trade_cal = make_sample_data()
    factors = compute_a1_factor(lhb_list, lhb_detail, stock_daily, trade_cal, lookback_days=6, min_history=2)
    labeled = attach_next_open_premium(factors, stock_daily)
    summary, ic_frame, group_frame = compute_backtest_metrics(labeled, group_count=3)
    assert summary["sample_count"] > 0
    assert len(group_frame) > 0
    assert "next_open_premium" in labeled.columns


def test_full_universe_labels_include_non_lhb_stocks_with_zero_score() -> None:
    lhb_list, lhb_detail, stock_daily, trade_cal = make_sample_data()
    extra_stock = stock_daily[stock_daily["symbol"].eq("000003.SZ")].copy()
    extra_stock["symbol"] = "000004.SZ"
    stock_daily = pd.concat([stock_daily, extra_stock], ignore_index=True)
    factors = compute_a1_factor(lhb_list, lhb_detail, stock_daily, trade_cal, lookback_days=6, min_history=2)

    labeled = attach_full_universe_next_open_premium(factors, stock_daily, missing_factor_value=0.0)
    extra_rows = labeled[labeled["ts_code"].eq("000004.SZ")]

    assert len(extra_rows) > 0
    assert extra_rows["score"].eq(0.0).all()
    assert extra_rows["signal"].eq("hold").all()
