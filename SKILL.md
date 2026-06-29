---
name: skill-a1-lhb-tracking
description: "A股龙虎榜席位追踪因子开发、校验和回测 skill，基于 pandadata 龙虎榜、行情和交易日历数据生成事件驱动排序分数。Use when an agent needs to build, validate, explain, or backtest an A-share LHB event-driven alpha factor with IC, Rank IC, look-ahead checks, and QuantFlow review materials."
quantSkills:
  organization: https://github.com/quantskills
  repository: quantskills/skill-a1-lhb-tracking
  repository_url: https://github.com/quantskills/skill-a1-lhb-tracking
  project_type: skill
  collection: factor-skills
  license: GPL-3.0
  category: factor
  tags: [a-share, lhb, alpha-factor, event-driven, pandadata]
  platforms: [claude-code, codex, openclaw, cursor]
  language: zh-en
  status: active
  validation_level: verified
  maintainer_type: community
  requires: [skill-pandadata-api]
  summary_zh: 用 pandadata 龙虎榜数据追踪席位胜率、盈亏比和次日溢价，生成事件驱动排序因子。
  summary_en: A-share LHB event-ranking factor using seat win rate, payoff, premium, and buy-size evidence from Pandadata.
---

# A1 龙虎榜席位追踪因子

本 skill 用于处理 A1「龙虎榜资金追踪」因子。

因子的核心思想是：如果某只股票当天被近期胜率较高、盈亏比较好的龙虎榜席位买入，并且买入金额较大，那么它在下一交易日开盘获得溢价的概率可能更高。

定位说明：本因子是龙虎榜事件驱动排序因子，适合在“当天上过龙虎榜的股票”内部做横截面排序；不建议直接包装为全市场通用日频 Alpha。

## 主要流程

1. 使用 `scripts/login_pandadata.py` 或环境变量初始化 pandadata 登录状态。
2. 使用 `scripts/factor.py` 生成生产因子文件。
3. 使用 `scripts/validate.py` 校验生产文件。
4. 使用 `scripts/backtest.py` 进行历史验证。
5. 按需要读取 `references/` 下的公式、数据、验证和 QuantFlow 说明。

## 数据来源

正式数据源为 PandaAI `panda_data`：

- `get_lhb_list`
- `get_lhb_detail`
- `get_stock_daily`
- `get_trade_cal`

开发或应急验证时，可使用 `scripts/akshare_source.py` 中的公开龙虎榜数据适配器。

正式交付应优先使用 pandadata。

注意：akshare 备用适配器中的 `next_open_premium` 使用公开源“上榜后 1 日”字段，只作为应急近似标签；正式验证口径以 pandadata 行情计算的“下一交易日开盘价 / 龙虎榜当日收盘价 - 1”为准。

## 公式摘要

历史标签：

```text
次日开盘溢价 = 下一交易日开盘价 / 龙虎榜当日收盘价 - 1
```

席位分数：

```text
seat_score =
100 * (
  0.40 * win_rate
  + 0.25 * payoff_component
  + 0.25 * premium_component
  + 0.10 * sample_component
)
```

股票因子值：

```text
raw_seat_score = sum(seat_score * buy_value) / sum(buy_value)
factor_value = raw_seat_score * (0.75 + 0.25 * 当日买入金额分位排名)
score = 当日 factor_value 分位排名 * 100
```

完整公式见：

- `references/formula_and_lookahead.md`

## 防未来函数规则

- 龙虎榜数据视为收盘后可用。
- 日期 `T` 的评分只使用 `date < T` 的席位历史。
- 日期 `T` 的次日开盘溢价只作为回测标签，不能参与日期 `T` 的因子计算。
- 生产输出不能包含 `next_open_premium`、`next_open`、`future_return` 等未来字段。
- 生产输出按 `trade_date`、`ts_code`、`factor_id` 去重。

## 生成生产文件

```bash
python scripts/factor.py --source panda --start-date 20260501 --end-date 20260622 --lookback-days 30 --min-history 3 --latest-only --data-version A1-pandadata-YYYYMMDD --output production/database.parquet
python scripts/validate.py --factor-path production/database.parquet
```

## 历史验证

```bash
python scripts/backtest.py --source panda --start-date 20260101 --end-date 20260622 --eval-start-date 20260216 --lookback-days 30 --min-history 3 --group-count 5 --output-dir production/backtest_pandadata_long
```

输出指标包括：

- IC
- Rank IC
- ICIR
- Rank ICIR
- 分组收益
- 多空收益
- 胜率
- 夏普
- 最大回撤
- 换手率
- 样本数量

## 本地检查

```bash
python scripts/factor.py --sample --lookback-days 6 --min-history 2 --output sample_database.parquet
python scripts/validate.py --factor-path sample_database.parquet
python scripts/backtest.py --sample --lookback-days 6 --min-history 2 --output-dir sample_backtest
python -m pytest scripts/test_a1_core.py -q
```

如需复核已落盘真实样本：

```bash
python scripts/validate_real_backtest.py --event-dir production/backtest_pandadata_event_corrected_202602 --neutral-dir production/backtest_pandadata_lhb_type_neutral_corrected_202602 --output-dir production/final_local_validation
```

## 参考文档

- 公式和防未来函数：`references/formula_and_lookahead.md`
- pandadata 字段说明：`references/data_guide.md`
- 历史验证报告：`references/validation_report.md`
- QuantFlow 验证说明：`references/quantflow_validation.md`

## 交付检查

交付前必须确认：

- `validate.py` 校验通过。
- 生产文件没有未来标签字段。
- 重复主键数量为 0。
- 回测结果包含 IC、Rank IC、ICIR、Rank ICIR、分组收益、胜率、夏普、最大回撤、换手率。
- 文档明确区分“因子有效性验证”和“完整实盘策略收益”。
