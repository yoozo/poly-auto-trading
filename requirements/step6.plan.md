# Chainlink TWAP 结算价格源改造计划

## 背景

BTC Up/Down market 的结算依据是 Chainlink BTC/USD TWAP。现有页面以 Binance BTCUSDT K 线价格计算参考价和价差，可能产生错误交易信号。

## 决策

- Binance 继续提供 OHLCV K 线和技术指标，不用 TWAP 伪造 K 线。
- 5m market 使用 Polymarket 免费 RTDS 的 Chainlink 30s TWAP。
- 15m market 使用 Polymarket免费 RTDS 的 Chainlink 60s TWAP。
- Price to Beat 优先使用 Polymarket 页面同源的 openPrice，Current Price 使用 Chainlink spot；30s/60s TWAP 独立用于结算方向估计。
- RTDS 不提供历史回放，因此实时 observation 本地持久化；首次启动缺少起始 observation 时，允许使用窗口开始前一根 Binance 1m 收盘价补齐。
- 使用 Binance 补齐时，前端和 Telegram 必须显示“可能有误差”，不得标记为精确 Chainlink 数据。
- Chainlink 当前数据缺失或过期时，不生成方向信号。

## 实施任务

1. 接入 `crypto_prices_twap_thirty` 与 `crypto_prices_twap_sixty`，使用 `full_accuracy_value` 的 E18 精度计算。
2. 接入 `crypto_prices_chainlink` spot tick，以 1m OHLC 作为权威重放数据持久化；高周期实时聚合并仅物化闭合结果。
3. 新增 observation、K 线来源与完整性字段的增量数据库迁移。
4. 构造独立的 market price context：基准价、spot 当前价、TWAP 结算估计、方向、质量和警告。
5. 通过现有 Polymarket market WebSocket 快照下发 price context。
6. 前端主图与技术指标切换到 Chainlink 聚合 K 线；启用前历史才使用 Binance 补齐并标源。
7. Telegram 推送复用同一 price context，并按 market、方向和数据质量去重。

## 验收标准

- 5m/15m 页面不再用 Binance 最新 K 线计算 market 价差。
- 主图实时 K 线及其 BOLL/RSI 来自 Chainlink spot 本地聚合，Volume 为 null。
- Price to Beat 来自 Polymarket openPrice，Current Price 来自 Chainlink spot，结算估计来自相应 TWAP。
- Binance 只补齐 Chainlink 首次采集时间之前的历史，不覆盖采集开始后的断线缺口。
- 冷启动补偿模式有清晰误差提示；Chainlink 基准到达后自动切回精确模式。
- stale/waiting/unavailable 状态不输出交易方向。
- 后端测试、静态检查和前端生产构建通过。
