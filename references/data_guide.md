# A1 Data Guide

## Required PandaAI Data Interfaces

### get_lhb_list

Purpose: find stock-date events that entered the Longhubang list.

Required fields:

- `symbol`
- `date`
- `type`
- `reason`
- `amount`
- `volume`
- `turnover`

### get_lhb_detail

Purpose: identify buy-side seats and their buy values.

Required fields:

- `symbol`
- `date`
- `type`
- `side`
- `rank`
- `agency`
- `b_value`
- `s_value`
- `reason`

Use `side="buy"` for formal factor calculation.

### get_stock_daily

Purpose: calculate historical next-open premium for seat performance.

Required fields:

- `symbol`
- `date`
- `open`
- `close`
- `high`
- `low`
- `volume`
- `amount`
- `trade_status`

Next-open premium is:

```text
next trading day's open / LHB date close - 1
```

### get_trade_cal

Purpose: define the previous 30 trading-day window.

Required fields:

- `nature_date`
- `is_trade`
- `exchange`

## Real Public Fallback

When PandaAI credentials are not available, `scripts/akshare_source.py` can use
real public LHB data exposed by AkShare/Eastmoney.

Required AkShare interfaces:

- `stock_lhb_detail_em`: stock-date LHB events, event reason, turnover, and public post-event outcome fields
- `stock_lhb_hyyyb_em`: broker-department daily buy stock lists and daily total buy/sell amount
- `tool_trade_date_hist_sina`: trading calendar

Mapping notes:

- Stock events come from `stock_lhb_detail_em`.
- Broker seats are linked to stock events by the same LHB date and stock name in `stock_lhb_hyyyb_em`.
- Broker daily buy amount is allocated across that broker's bought stocks for weighting.
- Public `post LHB 1-day` outcome is used only as a historical label for dates before the scored date.
- Production factor output never writes label fields such as `next_open_premium`.

## Data Alignment Rules

- LHB records are after-close information.
- A stock's score on date D can use seat outcomes from dates before D only.
- A seat outcome for date D-1 can use date D open because date D open is known after date D close.
- The current date D next-open result is never included in the factor output.

## Default Parameters

| Parameter | Default | Meaning |
|---|---:|---|
| lookback_days | 30 | Seat history window |
| min_history | 3 | Minimum records before a seat score is trusted |
| signal threshold | score >= 80 and confidence >= 0.35 | Buy signal rule |

## Missing Data Rules

- Missing current buy-side detail: skip the stock-date event.
- Missing historical next open: exclude that historical sample.
- Seat history below `min_history`: seat score is treated as unavailable for the current event.
- Empty factor output is considered validation failure.

## Fields Written to Production Parquet

Primary fields:

- `trade_date`
- `asset_type`
- `ts_code`
- `factor_id`
- `factor_name`
- `factor_value`
- `score`
- `rank`
- `signal`
- `confidence`
- `data_version`
- `update_time`

Diagnostic fields:

- `raw_seat_score`
- `current_buy_value`
- `amount_rank`
- `agency_count`
- `agencies_with_history`
- `history_count`
- `lhb_type`
- `lhb_reason`
- `top_agencies`

Diagnostic fields help audit the result but should not be used as future labels.
