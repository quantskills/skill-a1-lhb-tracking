# A1 龙虎榜席位追踪因子

A1 是一个用于 A 股龙虎榜数据的席位追踪因子。

因子假设：近期在龙虎榜上表现较好的席位，如果在当前交易日继续买入某只股票，则该股票在下一交易日开盘获得溢价的概率可能更高。

## 因子逻辑

对每个龙虎榜上榜股票：

1. 找到当天买入该股票的席位。
2. 回看每个席位过去 30 个交易日的龙虎榜买入记录。
3. 计算席位历史胜率、盈亏比、平均次日开盘溢价和样本数。
4. 按当前买入金额加权，得到股票级因子值。
5. 在每日龙虎榜股票池内做横截面排名，输出 0 到 100 分。

核心公式见：

- `references/formula_and_lookahead.md`

## 防未来函数

本因子遵守以下规则：

- 龙虎榜数据视为收盘后可用。
- 日期 `T` 的因子分数只使用 `T` 之前的席位历史。
- 日期 `T` 的次日开盘溢价只作为回测标签，不参与 `T` 日因子计算。
- 生产结果不包含未来收益字段。

## 数据来源

正式数据源：

- `panda_data.get_lhb_list`
- `panda_data.get_lhb_detail`
- `panda_data.get_stock_daily`
- `panda_data.get_trade_cal`

开发环境中也提供公开龙虎榜数据的备用适配器。

备用适配器仅用于无法访问 pandadata 时的应急验证。其 `next_open_premium` 来自公开源的“上榜后 1 日”字段，是近似标签；正式交付和验证应优先使用 pandadata 行情计算的“下一交易日开盘价 / 当日收盘价 - 1”。

## 运行方式

生成生产文件：

```bash
python scripts/factor.py --source panda --start-date 20260501 --end-date 20260622 --lookback-days 30 --min-history 3 --latest-only --data-version A1-pandadata-YYYYMMDD --output production/database.parquet
python scripts/validate.py --factor-path production/database.parquet
```

历史验证：

```bash
python scripts/backtest.py --source panda --start-date 20260101 --end-date 20260622 --eval-start-date 20260216 --lookback-days 30 --min-history 3 --group-count 5 --output-dir production/backtest_pandadata_long
```

带交易约束和成本的复核：

```bash
python scripts/backtest.py --source panda --start-date 20260101 --end-date 20260622 --eval-start-date 20260216 --lookback-days 30 --min-history 3 --group-count 5 --exclude-st --exclude-suspended --exclude-limit-open --one-way-cost-bps 10 --one-way-slippage-bps 5 --output-dir production/backtest_pandadata_stress
```

本地检查：

```bash
python scripts/factor.py --sample --lookback-days 6 --min-history 2 --output sample_database.parquet
python scripts/validate.py --factor-path sample_database.parquet
python scripts/backtest.py --sample --lookback-days 6 --min-history 2 --output-dir sample_backtest
python -m pytest scripts/test_a1_core.py -q
```

## 验证结果摘要

当前 pandadata 长窗口验证口径：

- 数据范围：2026-01-01 至 2026-06-22
- 正式评估：2026-02-16 至 2026-06-18
- 有效样本：5177
- 评估交易日：78
- IC 均值：0.2543
- Rank IC 均值：0.2757
- 最高分组平均次日开盘溢价：+1.1629%
- 多空平均收益：+3.0811%
- 多头最大回撤：-1.37%

完整报告见：

- `references/validation_report.md`

## QuantFlow 复核

QuantFlow 平台复核说明见：

- `references/quantflow_validation.md`

本仓库回测脚本已支持交易成本、滑点、涨跌停开盘、停牌和 ST 过滤；平台复核时建议使用同一口径。

## 目录结构

```text
skill-a1-lhb-tracking/
|-- SKILL.md
|-- README.md
|-- LICENSE
|-- agents/
|   `-- openai.yaml
|-- references/
|   |-- data_guide.md
|   |-- formula_and_lookahead.md
|   |-- quantflow_validation.md
|   `-- validation_report.md
|-- scripts/
|   |-- a1_core.py
|   |-- akshare_source.py
|   |-- backtest.py
|   |-- factor.py
|   |-- login_pandadata.py
|   |-- test_a1_core.py
|   |-- validate.py
|   `-- validate_real_backtest.py
`-- requirements.txt
```

## 注意事项

- 不要提交 pandadata token、账号、密码或本地环境文件。
- 回测中的收益为“龙虎榜日收盘到下一交易日开盘”的验证口径，不等同于完整实盘策略收益。
- 上传前建议先运行 `scripts/validate.py` 和单元测试。
