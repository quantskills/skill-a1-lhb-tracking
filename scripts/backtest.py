"""Backtest A1 factor with next-open premium labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from a1_core import (
    add_group_neutral_columns,
    attach_full_universe_next_open_premium,
    attach_next_open_premium,
    compute_a1_factor,
    compute_backtest_metrics,
    make_sample_data,
)
from akshare_source import attach_akshare_next_open_premium, fetch_akshare_inputs
from factor import _dedupe_and_rerank, fetch_panda_inputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest A1 Longhubang capital tracking factor")
    parser.add_argument("--source", choices=["panda", "akshare", "sample"], default="panda")
    parser.add_argument("--start-date", help="YYYYMMDD")
    parser.add_argument("--end-date", help="YYYYMMDD")
    parser.add_argument("--eval-start-date", help="Only evaluate factor rows on or after this date.")
    parser.add_argument("--exchange", default="SH")
    parser.add_argument("--lhb-type", default="")
    parser.add_argument("--panda-username", help="PandaAI data username. Prefer PANDADATA_USERNAME env var.")
    parser.add_argument("--panda-password", help="PandaAI data password. Prefer PANDADATA_PASSWORD env var.")
    parser.add_argument("--panda-base-url", help="PandaAI data service URL. Defaults to the package default.")
    parser.add_argument("--evaluation-scope", choices=["event", "all-stock", "lhb-type-neutral"], default="event")
    parser.add_argument("--missing-factor-value", type=float, default=0.0)
    parser.add_argument("--stock-indicator", default="", help="Optional stock universe indicator for PandaAI stock_daily.")
    parser.add_argument("--exclude-st", action="store_true", help="Exclude ST stocks when fetching PandaAI all-stock daily data.")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--min-history", type=int, default=3)
    parser.add_argument("--group-count", type=int, default=5)
    parser.add_argument("--output-dir", default="backtest_output")
    parser.add_argument("--sample", action="store_true", help="Run built-in sample data for smoke testing only.")
    return parser.parse_args()


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    source = "sample" if args.sample else args.source

    if source == "sample":
        lhb_list, lhb_detail, stock_daily, trade_cal = make_sample_data()
    elif source == "akshare":
        if not args.start_date or not args.end_date:
            print("Backtest requires --start-date and --end-date unless --sample is used.")
            return 1
        lhb_list, lhb_detail, stock_daily, trade_cal, _, _ = fetch_akshare_inputs(
            args.start_date,
            args.end_date,
            args.lookback_days,
        )
    else:
        if not args.start_date or not args.end_date:
            print("Backtest requires --start-date and --end-date unless --sample is used.")
            return 1
        lhb_list, lhb_detail, stock_daily, trade_cal, _, _ = fetch_panda_inputs(
            args.start_date,
            args.end_date,
            args.exchange,
            args.lookback_days,
            args.lhb_type,
            args.panda_username,
            args.panda_password,
            args.panda_base_url,
            full_stock_daily=args.evaluation_scope == "all-stock",
            stock_indicator=args.stock_indicator,
            include_st=not args.exclude_st,
        )

    factors = compute_a1_factor(
        lhb_list,
        lhb_detail,
        stock_daily,
        trade_cal,
        lookback_days=args.lookback_days,
        min_history=args.min_history,
        data_version="backtest",
    )
    factors = _dedupe_and_rerank(factors)
    if args.eval_start_date:
        factors = factors[factors["trade_date"].astype(str).ge(args.eval_start_date)].copy()
    if source == "akshare":
        event_labeled = attach_akshare_next_open_premium(factors, lhb_list)
    else:
        event_labeled = attach_next_open_premium(factors, stock_daily)

    if args.evaluation_scope == "all-stock":
        labeled = attach_full_universe_next_open_premium(
            factors,
            stock_daily,
            missing_factor_value=args.missing_factor_value,
        )
        score_col = "score"
        label_col = "next_open_premium"
    elif args.evaluation_scope == "lhb-type-neutral":
        labeled = add_group_neutral_columns(event_labeled, ["lhb_type"])
        score_col = "score_neutral"
        label_col = "next_open_premium_neutral"
    else:
        labeled = event_labeled
        score_col = "score"
        label_col = "next_open_premium"

    summary, ic_frame, group_frame = compute_backtest_metrics(
        labeled,
        group_count=args.group_count,
        score_col=score_col,
        label_col=label_col,
    )
    summary["evaluation_scope"] = args.evaluation_scope
    summary["stock_daily_symbol_count"] = int(stock_daily["symbol"].nunique()) if "symbol" in stock_daily.columns else 0

    factors.to_parquet(output_dir / "a1_factors.parquet", index=False)
    labeled.to_parquet(output_dir / "a1_factors_with_labels.parquet", index=False)
    ic_frame.to_csv(output_dir / "ic_by_date.csv", index=False, encoding="utf-8-sig")
    group_frame.to_csv(output_dir / "group_returns.csv", index=False, encoding="utf-8-sig")
    _write_json(output_dir / "summary.json", summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
