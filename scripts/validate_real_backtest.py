"""Run local validation checks for the real A1 LHB backtest artifacts."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from a1_core import compute_backtest_metrics, validate_factor_frame


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.ndarray,)):
        return value.tolist()
    if pd.isna(value):
        return None
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def _annualized_return(series: pd.Series) -> float | None:
    series = pd.to_numeric(series, errors="coerce").dropna()
    if len(series) == 0:
        return None
    return float((1.0 + series.mean()) ** 252 - 1.0)


def _sharpe(series: pd.Series) -> float | None:
    series = pd.to_numeric(series, errors="coerce").dropna()
    if len(series) < 2:
        return None
    std = float(series.std(ddof=1))
    if std <= 0:
        return None
    return float(series.mean() / std * math.sqrt(252))


def _max_drawdown(series: pd.Series) -> float | None:
    series = pd.to_numeric(series, errors="coerce").dropna()
    if len(series) == 0:
        return None
    curve = (1.0 + series).cumprod()
    dd = curve / curve.cummax() - 1.0
    return float(dd.min())


def _cum_return(series: pd.Series) -> float | None:
    series = pd.to_numeric(series, errors="coerce").dropna()
    if len(series) == 0:
        return None
    return float((1.0 + series).prod() - 1.0)


def _p_value(values: pd.Series) -> float | None:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if len(values) < 2:
        return None
    std = float(values.std(ddof=1))
    if std <= 0:
        return None
    t_stat = float(values.mean() / (std / math.sqrt(len(values))))
    try:
        from scipy import stats

        return float(stats.t.sf(abs(t_stat), len(values) - 1) * 2.0)
    except Exception:
        return None


def _bootstrap_ci(values: pd.Series, n_bootstrap: int = 5000, seed: int = 20260623) -> dict[str, float | None]:
    values = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if len(values) == 0:
        return {"low": None, "high": None}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(values), size=(n_bootstrap, len(values)))
    means = values[idx].mean(axis=1)
    return {"low": float(np.percentile(means, 2.5)), "high": float(np.percentile(means, 97.5))}


def _enrich_summary(
    name: str,
    frame: pd.DataFrame,
    group_count: int = 5,
    score_col: str = "score",
    label_col: str = "next_open_premium",
) -> dict[str, Any]:
    summary, ic_frame, group_frame = compute_backtest_metrics(
        frame,
        group_count=group_count,
        score_col=score_col,
        label_col=label_col,
    )
    ic = pd.to_numeric(ic_frame.get("ic", pd.Series(dtype=float)), errors="coerce").dropna()
    rank_ic = pd.to_numeric(ic_frame.get("rank_ic", pd.Series(dtype=float)), errors="coerce").dropna()

    row: dict[str, Any] = {"segment": name, **summary}
    row["ic_std"] = float(ic.std(ddof=1)) if len(ic) > 1 else None
    row["rank_ic_std"] = float(rank_ic.std(ddof=1)) if len(rank_ic) > 1 else None
    row["ic_ir_daily"] = float(ic.mean() / ic.std(ddof=1)) if len(ic) > 1 and ic.std(ddof=1) > 0 else None
    row["rank_ic_ir_daily"] = (
        float(rank_ic.mean() / rank_ic.std(ddof=1)) if len(rank_ic) > 1 and rank_ic.std(ddof=1) > 0 else None
    )
    row["ic_p_value"] = _p_value(ic)
    row["rank_ic_p_value"] = _p_value(rank_ic)
    row["ic_ci_95_low"] = _bootstrap_ci(ic)["low"]
    row["ic_ci_95_high"] = _bootstrap_ci(ic)["high"]
    row["rank_ic_ci_95_low"] = _bootstrap_ci(rank_ic)["low"]
    row["rank_ic_ci_95_high"] = _bootstrap_ci(rank_ic)["high"]

    if len(group_frame):
        grouped_rows = []
        for group, part in group_frame.groupby("group"):
            grouped_rows.append(
                {
                    "group": group,
                    "weighted_mean_return": float(
                        (part["mean_next_open_premium"] * part["count"]).sum() / part["count"].sum()
                    ),
                }
            )
        grouped = pd.DataFrame(grouped_rows)
        if len(grouped) >= 2:
            row["group_return_spearman"] = float(grouped["group"].corr(grouped["weighted_mean_return"], method="spearman"))
        else:
            row["group_return_spearman"] = None
    else:
        row["group_return_spearman"] = None
    return row


def _daily_group_returns(frame: pd.DataFrame, group_count: int = 5, score_col: str = "score") -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for date, day in frame.dropna(subset=[score_col, "next_open_premium"]).groupby("trade_date"):
        day = day.sort_values(score_col, ascending=False).copy()
        ranks = day[score_col].rank(method="first", ascending=False)
        day["group"] = np.ceil(ranks / max(len(day), 1) * group_count).astype(int).clip(1, group_count)
        for group, g in day.groupby("group"):
            rows.append(
                {
                    "trade_date": date,
                    "group": int(group),
                    "ret": float(g["next_open_premium"].mean()),
                    "symbols": "|".join(g["ts_code"].astype(str).tolist()),
                    "count": int(len(g)),
                }
            )
    return pd.DataFrame(rows)


def _turnover(prev: set[str], cur: set[str]) -> float:
    if not prev:
        return 1.0
    return 1.0 - len(prev.intersection(cur)) / len(prev)


def _cost_stress(frame: pd.DataFrame, output_dir: Path, group_count: int = 5) -> pd.DataFrame:
    daily = _daily_group_returns(frame, group_count=group_count)
    pivot = daily.pivot(index="trade_date", columns="group", values="ret").sort_index()
    sets = daily.pivot(index="trade_date", columns="group", values="symbols").sort_index()
    top_sets = sets.get(1, pd.Series(dtype=str)).fillna("").map(lambda x: set(filter(None, str(x).split("|"))))
    bottom_sets = sets.get(group_count, pd.Series(dtype=str)).fillna("").map(lambda x: set(filter(None, str(x).split("|"))))

    top_turnover = []
    bottom_turnover = []
    for idx, _date in enumerate(pivot.index):
        if idx == 0:
            top_turnover.append(1.0)
            bottom_turnover.append(1.0)
        else:
            top_turnover.append(_turnover(top_sets.iloc[idx - 1], top_sets.iloc[idx]))
            bottom_turnover.append(_turnover(bottom_sets.iloc[idx - 1], bottom_sets.iloc[idx]))

    top = pivot.get(1, pd.Series(dtype=float)).astype(float)
    bottom = pivot.get(group_count, pd.Series(dtype=float)).astype(float)
    gross_ls = (top - bottom).dropna()
    top_turnover_s = pd.Series(top_turnover, index=pivot.index).reindex(gross_ls.index).fillna(1.0)
    bottom_turnover_s = pd.Series(bottom_turnover, index=pivot.index).reindex(gross_ls.index).fillna(1.0)

    rows = []
    for cost_bps in [0, 5, 10, 20, 50, 100]:
        one_way_cost = cost_bps / 10000.0
        top_net = top.reindex(gross_ls.index) - top_turnover_s * one_way_cost
        ls_net = gross_ls - (top_turnover_s + bottom_turnover_s) * one_way_cost
        rows.append(
            {
                "one_way_cost_bps": cost_bps,
                "top_mean": float(top_net.mean()),
                "top_cumulative": _cum_return(top_net),
                "top_annualized_return": _annualized_return(top_net),
                "top_sharpe": _sharpe(top_net),
                "top_max_drawdown": _max_drawdown(top_net),
                "long_short_mean": float(ls_net.mean()),
                "long_short_cumulative": _cum_return(ls_net),
                "long_short_annualized_return": _annualized_return(ls_net),
                "long_short_sharpe": _sharpe(ls_net),
                "long_short_max_drawdown": _max_drawdown(ls_net),
            }
        )
    out = pd.DataFrame(rows)
    out.to_csv(output_dir / "cost_stress.csv", index=False, encoding="utf-8-sig")
    return out


def _variant_summaries(frame: pd.DataFrame) -> pd.DataFrame:
    variants = frame.copy()
    variants["score_times_confidence"] = variants["score"] * variants["confidence"]
    variants["factor_times_confidence"] = variants["factor_value"] * variants["confidence"]
    variants["rank_bad_direction"] = variants["rank"].astype(float)
    variants["rank_corrected"] = -variants["rank"].astype(float)

    rows = []
    for name, col, subset in [
        ("score", "score", variants),
        ("factor_value", "factor_value", variants),
        ("raw_seat_score", "raw_seat_score", variants),
        ("score_times_confidence", "score_times_confidence", variants),
        ("factor_times_confidence", "factor_times_confidence", variants),
        ("rank_bad_direction", "rank_bad_direction", variants),
        ("rank_corrected", "rank_corrected", variants),
        ("confidence_ge_035", "score", variants[variants["confidence"] >= 0.35]),
        ("confidence_ge_050", "score", variants[variants["confidence"] >= 0.50]),
        ("history_count_ge_10", "score", variants[variants["history_count"] >= 10]),
    ]:
        rows.append(_enrich_summary(name, subset, score_col=col))
    return pd.DataFrame(rows)


def _segment_summaries(frame: pd.DataFrame, neutral_frame: pd.DataFrame | None) -> pd.DataFrame:
    valid = frame.dropna(subset=["next_open_premium"]).copy()
    dates = sorted(valid["trade_date"].unique().tolist())
    midpoint = len(dates) // 2
    first_dates = set(dates[:midpoint])
    second_dates = set(dates[midpoint:])

    rows = [
        _enrich_summary("full_event_pool", valid),
        _enrich_summary("first_half_in_sample_proxy", valid[valid["trade_date"].isin(first_dates)]),
        _enrich_summary("second_half_out_of_sample_proxy", valid[valid["trade_date"].isin(second_dates)]),
    ]

    valid["month"] = valid["trade_date"].astype(str).str[:6]
    for month, month_frame in valid.groupby("month"):
        rows.append(_enrich_summary(f"month_{month}", month_frame))

    if neutral_frame is not None and len(neutral_frame):
        neutral = neutral_frame.dropna(subset=["next_open_premium_neutral", "score_neutral"]).copy()
        rows.append(
            _enrich_summary(
                "lhb_type_neutral_full",
                neutral,
                score_col="score_neutral",
                label_col="next_open_premium_neutral",
            )
        )
        neutral_dates = sorted(neutral["trade_date"].unique().tolist())
        neutral_second_dates = set(neutral_dates[len(neutral_dates) // 2 :])
        rows.append(
            _enrich_summary(
                "lhb_type_neutral_second_half_oos_proxy",
                neutral[neutral["trade_date"].isin(neutral_second_dates)],
                score_col="score_neutral",
                label_col="next_open_premium_neutral",
            )
        )
    return pd.DataFrame(rows)


def _group_summary(frame: pd.DataFrame, output_dir: Path) -> pd.DataFrame:
    daily = _daily_group_returns(frame)
    rows = []
    for group, part in daily.groupby("group"):
        rows.append(
            {
                "group": group,
                "weighted_mean_return": float((part["ret"] * part["count"]).sum() / part["count"].sum()),
                "mean_daily_return": float(part["ret"].mean()),
                "sample_count": int(part["count"].sum()),
                "day_count": int(part["trade_date"].nunique()),
            }
        )
    group_summary = pd.DataFrame(rows)
    group_summary.to_csv(output_dir / "group_summary.csv", index=False, encoding="utf-8-sig")
    return group_summary


def _lhb_type_summary(frame: pd.DataFrame, output_dir: Path) -> pd.DataFrame:
    rows = []
    for lhb_type, part in frame.dropna(subset=["next_open_premium"]).groupby("lhb_type", dropna=False):
        if len(part) < 80 or part["trade_date"].nunique() < 5:
            continue
        summary = _enrich_summary(str(lhb_type), part, group_count=3)
        rows.append(summary)
    out = pd.DataFrame(rows).sort_values("sample_count", ascending=False) if rows else pd.DataFrame()
    out.to_csv(output_dir / "lhb_type_summary.csv", index=False, encoding="utf-8-sig")
    return out


def _monthly_summary(frame: pd.DataFrame, output_dir: Path) -> pd.DataFrame:
    rows = []
    valid = frame.dropna(subset=["next_open_premium"]).copy()
    valid["month"] = valid["trade_date"].astype(str).str[:6]
    for month, part in valid.groupby("month"):
        rows.append(_enrich_summary(month, part))
    out = pd.DataFrame(rows)
    out.to_csv(output_dir / "monthly_summary.csv", index=False, encoding="utf-8-sig")
    return out


def _top_agencies_have_duplicates(text: Any) -> bool:
    if not isinstance(text, str) or not text:
        return False
    names = []
    for item in text.split(";"):
        item = item.strip()
        if not item:
            continue
        names.append(re.sub(r"\([^)]*\)\s*$", "", item).strip())
    return len(names) != len(set(names))


def _lookahead_checks(factors: pd.DataFrame, labeled: pd.DataFrame, source_path: Path) -> dict[str, Any]:
    source_text = source_path.read_text(encoding="utf-8")
    checks: dict[str, Any] = {}
    forward_columns = {"next_open", "next_date", "next_open_premium", "future_return"}
    checks["production_has_no_forward_columns"] = sorted(forward_columns.intersection(factors.columns)) == []
    checks["production_forward_columns_found"] = sorted(forward_columns.intersection(factors.columns))
    checks["production_validation_errors"] = validate_factor_frame(factors, require_no_forward_label=True)
    checks["labeled_primary_key_duplicates"] = int(labeled.duplicated(["trade_date", "ts_code", "factor_id"]).sum())
    checks["production_primary_key_duplicates"] = int(factors.duplicated(["trade_date", "ts_code", "factor_id"]).sum())
    valid_dates = labeled.dropna(subset=["next_date", "next_open_premium"]).copy()
    checks["label_next_date_after_trade_date"] = bool((valid_dates["next_date"].astype(str) > valid_dates["trade_date"].astype(str)).all())
    checks["valid_label_rows"] = int(valid_dates.shape[0])
    checks["total_labeled_rows"] = int(labeled.shape[0])
    checks["top_agencies_duplicate_rows"] = int(labeled["top_agencies"].map(_top_agencies_have_duplicates).sum())
    checks["source_contains_strict_history_date_filter"] = 'outcomes["date"].lt(event.date)' in source_text
    checks["source_contains_lookback_window_excluding_current_date"] = "trade_dates[start_idx:idx]" in source_text
    return checks


def _write_report(
    path: Path,
    segment_summary: pd.DataFrame,
    group_summary: pd.DataFrame,
    cost_stress: pd.DataFrame,
    variant_summary: pd.DataFrame,
    lhb_type_summary: pd.DataFrame,
    lookahead: dict[str, Any],
) -> None:
    def fmt(value: Any, pct: bool = False) -> str:
        value = _safe_float(value)
        if value is None:
            return "NA"
        return f"{value * 100:.2f}%" if pct else f"{value:.4f}"

    def row(segment: str) -> pd.Series:
        found = segment_summary[segment_summary["segment"].eq(segment)]
        return found.iloc[0] if len(found) else pd.Series(dtype=object)

    full = row("full_event_pool")
    oos = row("second_half_out_of_sample_proxy")
    neutral = row("lhb_type_neutral_full")
    neutral_oos = row("lhb_type_neutral_second_half_oos_proxy")

    lines = [
        "# A1 龙虎榜资金追踪因子本地复核报告",
        "",
        "## 结论",
        "",
        "本次复核基于已经落盘的 pandadata 真实样本，验证口径为动态龙虎榜事件池。",
        "该因子在事件池内表现为正；去除龙虎榜类型影响后仍为正，但强度明显下降。",
        "因此更适合定位为龙虎榜事件驱动排序因子，不建议包装成全市场通用 Alpha。",
        "",
        "## 核心结果",
        "",
        "| 口径 | 样本数 | 交易日 | IC | Rank IC | 多头均值 | 多空均值 | 多头夏普 | 最大回撤 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| 事件池全样本 | {int(full.get('sample_count', 0))} | {int(full.get('date_count', 0))} | {fmt(full.get('ic_mean'))} | {fmt(full.get('rank_ic_mean'))} | {fmt(full.get('top_group_mean_next_open_premium'), True)} | {fmt(full.get('long_short_mean_next_open_premium'), True)} | {fmt(full.get('top_group_sharpe'))} | {fmt(full.get('max_drawdown'), True)} |",
        f"| 后半段样本外代理 | {int(oos.get('sample_count', 0))} | {int(oos.get('date_count', 0))} | {fmt(oos.get('ic_mean'))} | {fmt(oos.get('rank_ic_mean'))} | {fmt(oos.get('top_group_mean_next_open_premium'), True)} | {fmt(oos.get('long_short_mean_next_open_premium'), True)} | {fmt(oos.get('top_group_sharpe'))} | {fmt(oos.get('max_drawdown'), True)} |",
        f"| 类型中性全样本 | {int(neutral.get('sample_count', 0))} | {int(neutral.get('date_count', 0))} | {fmt(neutral.get('ic_mean'))} | {fmt(neutral.get('rank_ic_mean'))} | {fmt(neutral.get('top_group_mean_next_open_premium'), True)} | {fmt(neutral.get('long_short_mean_next_open_premium'), True)} | {fmt(neutral.get('top_group_sharpe'))} | {fmt(neutral.get('max_drawdown'), True)} |",
        f"| 类型中性后半段 | {int(neutral_oos.get('sample_count', 0))} | {int(neutral_oos.get('date_count', 0))} | {fmt(neutral_oos.get('ic_mean'))} | {fmt(neutral_oos.get('rank_ic_mean'))} | {fmt(neutral_oos.get('top_group_mean_next_open_premium'), True)} | {fmt(neutral_oos.get('long_short_mean_next_open_premium'), True)} | {fmt(neutral_oos.get('top_group_sharpe'))} | {fmt(neutral_oos.get('max_drawdown'), True)} |",
        "",
        "## 分组收益",
        "",
        "| 分组 | 加权平均次日开盘收益 | 样本数 |",
        "| ---: | ---: | ---: |",
    ]
    for item in group_summary.itertuples(index=False):
        lines.append(f"| {int(item.group)} | {fmt(item.weighted_mean_return, True)} | {int(item.sample_count)} |")

    lines.extend(
        [
            "",
            "## 交易成本压力",
            "",
            "| 单边成本 | 多头累计 | 多头夏普 | 多空累计 | 多空夏普 |",
            "| ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in cost_stress.itertuples(index=False):
        lines.append(
            f"| {int(item.one_way_cost_bps)} bp | {fmt(item.top_cumulative, True)} | {fmt(item.top_sharpe)} | {fmt(item.long_short_cumulative, True)} | {fmt(item.long_short_sharpe)} |"
        )

    lines.extend(
        [
            "",
            "## 稳健性检查",
            "",
            "| 变体 | 样本数 | IC | Rank IC | 多头均值 | 多空均值 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in variant_summary.itertuples(index=False):
        lines.append(
            f"| {item.segment} | {int(item.sample_count)} | {fmt(item.ic_mean)} | {fmt(item.rank_ic_mean)} | {fmt(item.top_group_mean_next_open_premium, True)} | {fmt(item.long_short_mean_next_open_premium, True)} |"
        )

    lines.extend(
        [
            "",
            "## 防未来函数检查",
            "",
            f"- 生产因子文件不含未来收益字段：{'通过' if lookahead['production_has_no_forward_columns'] else '未通过'}",
            f"- 生产主键重复数：{lookahead['production_primary_key_duplicates']}",
            f"- 标签 next_date 晚于 trade_date：{'通过' if lookahead['label_next_date_after_trade_date'] else '未通过'}",
            f"- 有效标签行数：{lookahead['valid_label_rows']} / {lookahead['total_labeled_rows']}",
            f"- top_agencies 重复席位行数：{lookahead['top_agencies_duplicate_rows']}",
            f"- 源码中存在严格历史日期过滤：{'通过' if lookahead['source_contains_strict_history_date_filter'] else '未通过'}",
            "",
            "## 限制说明",
            "",
            "当前环境没有 pandadata 登录环境变量，因此本次没有重新抓取更长历史原始明细，也没有重新计算 20/30/60 日历史窗口参数组。",
            "本报告覆盖的是已落盘真实样本上的样本外代理、类型中性、成本压力、分组单调性和字段级防未来函数检查。",
            "GitHub 交付时建议明确写成事件驱动因子，不写成全市场通用因子。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate real A1 backtest artifacts.")
    parser.add_argument(
        "--event-dir",
        default="outputs/alpha-A1/production/backtest_pandadata_event_corrected_202602",
    )
    parser.add_argument(
        "--neutral-dir",
        default="outputs/alpha-A1/production/backtest_pandadata_lhb_type_neutral_corrected_202602",
    )
    parser.add_argument("--output-dir", default="outputs/alpha-A1/production/final_local_validation")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    event_dir = Path(args.event_dir)
    neutral_dir = Path(args.neutral_dir)
    factors = pd.read_parquet(event_dir / "a1_factors.parquet")
    labeled = pd.read_parquet(event_dir / "a1_factors_with_labels.parquet")
    neutral = None
    neutral_path = neutral_dir / "a1_factors_type_neutral_with_labels.parquet"
    if neutral_path.exists():
        neutral = pd.read_parquet(neutral_path)

    valid = labeled.dropna(subset=["next_open_premium"]).copy()
    segment_summary = _segment_summaries(valid, neutral)
    segment_summary.to_csv(output_dir / "segment_summary.csv", index=False, encoding="utf-8-sig")
    variant_summary = _variant_summaries(valid)
    variant_summary.to_csv(output_dir / "factor_variant_summary.csv", index=False, encoding="utf-8-sig")
    group_summary = _group_summary(valid, output_dir)
    monthly_summary = _monthly_summary(valid, output_dir)
    cost_stress = _cost_stress(valid, output_dir)
    lhb_type_summary = _lhb_type_summary(valid, output_dir)
    lookahead = _lookahead_checks(
        factors,
        labeled,
        Path("outputs/alpha-A1/development/scripts/a1_core.py"),
    )
    _write_json(output_dir / "lookahead_checks.json", lookahead)

    payload = {
        "source_event_dir": str(event_dir),
        "source_neutral_dir": str(neutral_dir),
        "segment_summary": segment_summary.to_dict(orient="records"),
        "factor_variant_summary": variant_summary.to_dict(orient="records"),
        "group_summary": group_summary.to_dict(orient="records"),
        "monthly_summary": monthly_summary.to_dict(orient="records"),
        "cost_stress": cost_stress.to_dict(orient="records"),
        "lhb_type_summary": lhb_type_summary.to_dict(orient="records"),
        "lookahead_checks": lookahead,
    }
    _write_json(output_dir / "validation_summary.json", payload)
    _write_report(
        output_dir / "validation_report.md",
        segment_summary,
        group_summary,
        cost_stress,
        variant_summary,
        lhb_type_summary,
        lookahead,
    )

    key = segment_summary[segment_summary["segment"].isin(["full_event_pool", "second_half_out_of_sample_proxy", "lhb_type_neutral_full"])]
    print(key[["segment", "sample_count", "date_count", "ic_mean", "rank_ic_mean", "top_group_mean_next_open_premium", "long_short_mean_next_open_premium", "top_group_sharpe", "max_drawdown"]].to_string(index=False))
    print(f"Validation artifacts written to: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
