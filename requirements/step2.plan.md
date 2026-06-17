# Step 2 统计需求：4h RSI-EMA Diff 后续方向概率

## 背景

项目已经支持下载 Binance K 线，并计划/已经补充指标快照计算能力。用户希望基于 `BTCUSDT` 的 `4h` K 线与 `rsi_ema_diff` 指标，统计强动量出现后，下一根 K 线是否延续同方向。

## 统计需求

- 数据范围：`candles` 与 `indicator_snapshots` 中的 `BTCUSDT`、`4h` 数据。
- 指标字段：`indicator_snapshots.rsi_ema_diff`。
- 阈值：`12`、`15`、`18`、`22`。
- 正向场景：
  - 条件：`rsi_ema_diff > threshold`。
  - 业务含义：当前处于上涨动量。
  - 顺方向：下一根 4h K 线也是涨，即 `next_close > next_open`。
  - 反方向：下一根 4h K 线是跌，即 `next_close < next_open`。
  - 十字星：`next_close = next_open`，单独计数，不纳入顺/反概率分母。
- 需要输出：
  - `threshold`
  - 总样本数
  - 顺方向次数与概率
  - 反方向次数与概率
  - 十字星次数

## 后续扩展

- 增加负向场景：
  - 条件：`rsi_ema_diff < -threshold`。
  - 业务含义：当前处于下跌动量。
  - 顺方向：下一根 4h K 线也是跌。
  - 反方向：下一根 4h K 线是涨。
- 增加按年份、牛熊区间、波动率区间拆分统计。
- 增加“连续多根后续方向”统计，例如下一根、后两根、后三根的延续概率。

## 验收标准

- 能用 SQL 或后端报表接口稳定复现统计结果。
- 统计结果明确说明十字星是否进入概率分母。
- 当 `indicator_snapshots` 缺少 4h 指标时，应先运行指标计算任务再统计。
