# A1 龙虎榜资金追踪因子公式与防未来函数说明

## 因子定义

A1 因子用于衡量龙虎榜买入席位的近期质量。

如果某只股票当天被近期表现较好的席位买入，且买入金额较大，则该股票获得更高的 A1 分数。

## 基础变量

对股票 `i`、交易日 `t`、席位 `s`：

- `close(i, t)`：股票 `i` 在龙虎榜日期 `t` 的收盘价
- `open(i, t+1)`：股票 `i` 下一交易日开盘价
- `premium(i, t)`：龙虎榜后次日开盘溢价
- `buy_value(i, s, t)`：席位 `s` 在日期 `t` 买入股票 `i` 的金额
- `sell_value(i, s, t)`：席位 `s` 在日期 `t` 卖出股票 `i` 的金额
- `lookback_days`：历史观察窗口，默认 30 个交易日
- `min_history`：席位最小有效历史样本数，默认 3

## 历史收益标签

每条历史龙虎榜记录的次日开盘溢价为：

```text
premium(i, t) = open(i, t+1) / close(i, t) - 1
```

这个值只用于评价历史席位表现。

对评分日 `T` 来说，只允许使用 `t < T` 的历史记录。

## 席位历史样本

对席位 `s`，在评分日 `T` 的历史样本集合为：

```text
H(s, T) = { premium(i, t) | 席位 s 在 t 日买入股票 i，且 t < T，且 t 位于 T 前 30 个交易日内 }
```

如果 `H(s, T)` 的样本数小于 `min_history`，该席位在当天不贡献正向席位分数。

## 席位指标

样本数：

```text
n(s, T) = count(H(s, T))
```

胜率：

```text
win_rate(s, T) = count(premium > 0) / n(s, T)
```

平均盈利：

```text
avg_win(s, T) = mean(premium | premium > 0)
```

平均亏损绝对值：

```text
avg_loss_abs(s, T) = abs(mean(premium | premium < 0))
```

盈亏比：

```text
payoff_ratio(s, T) = avg_win(s, T) / avg_loss_abs(s, T)
```

如果没有亏损且存在盈利，则盈亏比按 3 处理，避免无限放大。

平均次日溢价：

```text
mean_premium(s, T) = mean(H(s, T))
```

## 席位分数

先把各指标压缩到 0 到 1：

```text
payoff_component = min(max(payoff_ratio, 0), 3) / 3
```

```text
premium_component = clamp((mean_premium + 0.03) / 0.08, 0, 1)
```

```text
sample_component = min(n / 10, 1)
```

席位分数：

```text
seat_score(s, T) =
100 * (
  0.40 * win_rate
  + 0.25 * payoff_component
  + 0.25 * premium_component
  + 0.10 * sample_component
)
```

含义：

- 胜率权重最高，占 40%
- 盈亏比和平均溢价各占 25%
- 样本数量占 10%，用于奖励更稳定的席位历史

## 股票席位原始分

某股票 `i` 在日期 `T` 可能被多个席位买入。

对所有当天买入该股票的席位，用买入金额加权：

```text
raw_seat_score(i, T) =
sum(seat_score(s, T) * buy_value(i, s, T)) / sum(buy_value(i, s, T))
```

如果席位历史样本不足，则该席位分数按 0 参与加权。

## 当日资金强度修正

先计算股票当天买入金额：

```text
current_buy_value(i, T) = sum(buy_value(i, s, T))
```

再计算它在当日所有龙虎榜股票中的买入金额分位：

```text
amount_rank(i, T) = percentile_rank(current_buy_value(i, T))
```

最终因子值：

```text
factor_value(i, T) =
raw_seat_score(i, T) * (0.75 + 0.25 * amount_rank(i, T))
```

这一步让资金强度更高的龙虎榜股票获得适度加分，但不会覆盖席位质量本身。

## 横截面分数

每天在所有龙虎榜股票内部做横截面排名：

```text
score(i, T) = percentile_rank(factor_value(i, T)) * 100
```

分数范围为 0 到 100。

## 交易信号

```text
signal = buy,  if score >= 80 and confidence >= 0.35
signal = hold, otherwise
```

其中 `confidence` 来自席位历史覆盖度：

```text
coverage = 有足够历史样本的席位数 / 当天买入席位数
```

```text
history_ratio = min(总历史样本数 / (min_history * 当天买入席位数), 1)
```

```text
confidence = 0.60 * coverage + 0.40 * history_ratio
```

## 防未来函数规则

本因子严格遵守以下规则：

1. 龙虎榜数据视为收盘后可用。
2. 对评分日 `T`，席位历史只使用 `t < T` 的记录。
3. `premium(i, T)` 不参与日期 `T` 的因子计算，只能在日期 `T` 之后作为回测标签使用。
4. 生产结果不输出 `next_open_premium`、`next_open`、`future_return` 等未来字段。
5. 同一股票同一天多次上榜时，生产文件只保留分数最高的一条记录，避免重复计入。
6. 生成生产文件后必须运行结构校验，确认无重复主键、分数范围正确、无未来字段泄露。

## 当前默认参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| lookback_days | 30 | 席位历史观察窗口 |
| min_history | 3 | 席位最小有效历史样本 |
| buy threshold | score >= 80 | 高分买入信号阈值 |
| confidence threshold | confidence >= 0.35 | 最低置信度 |
| group_count | 5 | 回测分组数量 |

## 可调参数建议

平台复核时建议测试：

- `lookback_days`: 20 / 30 / 60
- `min_history`: 3 / 5 / 10
- `buy threshold`: 75 / 80 / 85
- 是否过滤 ST、停牌、一字板、涨停无法买入
- 加入不同手续费和滑点假设
