"""Validation checks for A1 factor outputs."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from a1_core import compute_a1_factor, make_sample_data, validate_factor_frame


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate A1 factor parquet output")
    parser.add_argument("--factor-path", help="Parquet file created with factor.py")
    parser.add_argument("--sample", action="store_true", help="Validate built-in sample output")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--min-history", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.sample:
        lhb_list, lhb_detail, stock_daily, trade_cal = make_sample_data()
        factors = compute_a1_factor(
            lhb_list,
            lhb_detail,
            stock_daily,
            trade_cal,
            lookback_days=args.lookback_days,
            min_history=args.min_history,
            data_version="sample-validation",
        )
    elif args.factor_path:
        factor_path = Path(args.factor_path)
        if not factor_path.exists():
            print(f"Validation failed: factor file does not exist: {factor_path}")
            return 1
        factors = pd.read_parquet(factor_path)
    else:
        print("Validation failed: provide --factor-path or --sample")
        return 1

    errors = validate_factor_frame(factors, require_no_forward_label=True)
    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(
        "Validation passed: "
        f"rows={len(factors)}, dates={factors['trade_date'].nunique()}, "
        f"buy_signals={(factors['signal'] == 'buy').sum()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
