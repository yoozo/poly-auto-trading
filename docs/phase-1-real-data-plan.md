# 阶段 1 真实数据接入计划

本文档记录如何把 Dashboard 里的 mock 数据逐步替换为真实 Binance 和 Polymarket 市场数据。

## 目标

阶段 1 的目标是在保持系统只读、安全的前提下，完成真实数据采集与标准化：

- 获取真实 Binance `BTCUSDT` 的 `1m`、`5m`、`15m`、`30m`、`1h`、`4h` K线。
- 基于 Binance 已收盘 K线计算 RSI、Bollinger Bands 和基础趋势状态。
- 发现真实 Polymarket BTC 5m/15m 市场。
- 获取真实 Polymarket market 数据，包括 best bid/ask、spread、liquidity 和 orderbook 状态。
- 让 FastAPI routes 从实时内存状态读取数据，而不是继续依赖 `mock_data.py`。
- 为后续行情信号开发提供可靠输入，例如盘口差价、流动性、价格变化、orderbook imbalance、last trade price 和市场剩余时间。

本阶段不实现 Telegram 通知、Polymarket 用户订单事件和自动交易。

本阶段也不完整实现信号策略，但数据结构和状态仓库要为后续技术指标信号、行情信号预留输入。

预警信号规则：

- 可以提供 `preview_signal`，用于展示未收盘 K线或实时行情形成中的方向预警。
- `preview_signal` 必须明确标记 `actionable=false`，不能用于真实自动交易。
- 正式交易信号仍然必须基于已收盘 K线和完整风控确认。

## 当前完成状态

- 已完成：步骤 1，已添加 `httpx` 和 `websockets` 到运行时依赖，并更新 `uv.lock`。
- 已完成：步骤 2，已新增 `app/schemas/`，包含 `Candle`、`IndicatorSnapshot`、`PolyMarket`、`OrderbookSnapshot`、`RuntimeStatus`、`ServiceHealth` 等共享结构。
- 已完成：步骤 3，已新增内存状态仓库 `app/services/state_store.py`。
- 已完成：步骤 4，已新增 Binance REST client/service、startup backfill 和 `/candles` state store 读取；支持 `1m`、`5m`、`15m`、`30m`、`1h`、`4h`，并过滤未收盘 K线。
- 已完成：步骤 5，已基于真实 closed candles 计算 RSI、Bollinger Bands 和 trend，并由 `/indicators/latest` 返回。
- 已完成：步骤 6，已订阅 Binance WebSocket 的 `1m`、`5m`、`15m`、`30m`、`1h`、`4h` closed candles，写入 state store 并刷新指标。
- 已完成：步骤 7，已实现 Polymarket Gamma 市场发现。默认使用 BTC 5m/15m slug 候选直查，避免高频拉取全量 events；可通过 `polymarket.use_events_fallback` 开启 fallback。
- 已完成：步骤 8，已实现 Polymarket Market WebSocket，按 token ID 订阅 market data，并写入 orderbook state。
- 已完成：步骤 9，已通过 FastAPI lifespan 启动 Binance REST backfill、Binance WS、Polymarket market refresh 和 Polymarket market WS。
- 已完成：步骤 10，已替换阶段 1 相关 mock-backed routes；`/signals` 也已在阶段 1.2 切为真实派生信号。`/orders`、`/notifications`、`/stats/summary` 仍作为阶段 2/3 预留。

## 实施顺序

### 步骤 1：添加运行时依赖（已完成）

添加真实数据服务所需的最小依赖：

- `httpx`：用于 REST API 请求。
- `websockets`：用于 Binance 和 Polymarket WebSocket 连接。

使用 uv：

```bash
uv add httpx websockets
uv lock
uv sync --dev
```

指标计算第一版先使用纯 Python 实现。除非纯 Python 实现明显变得笨重，否则暂时不引入 pandas。

完成记录：

- 已将 `httpx` 和 `websockets` 加入 `pyproject.toml` 的 runtime dependencies。
- 已更新 `uv.lock`。
- 已移除 dev dependency 中重复的 `httpx`。

### 步骤 2：新增共享 Schemas（已完成）

新增 `app/schemas/`，用于存放标准化后的内部/API 数据结构：

- `Candle`
- `IndicatorSnapshot`
- `PolyMarket`
- `OrderbookSnapshot`
- `RuntimeStatus`
- `ServiceHealth`

规则：

- API routes 返回标准化结构。
- 外部 API 的原始响应结构只能在 clients/services 里解析，不直接暴露给前端。
- Polymarket 的 `condition_id`、YES token ID、NO token ID 必须明确分开保存。

完成记录：

- 已新增 `app/schemas/market.py`。
- 已新增 `app/schemas/__init__.py`。
- 已实现 `Candle`、`IndicatorSnapshot`、`IndicatorInterval`、`PolyMarket`、`OrderbookSnapshot`、`OrderbookLevel`、`ServiceHealth`、`RuntimeStatus`。
- 已通过 Python 编译/导入检查。

### 步骤 3：新增内存状态仓库

新增 `app/services/state_store.py`。

状态仓库保存：

- 按 symbol 和 interval 分组的最近 K线。
- 按 symbol 和 interval 分组的最新指标快照。
- 当前追踪的 Polymarket BTC 5m/15m 市场。
- 按 token ID 分组的最新 orderbook / best bid / ask。
- 后续行情信号所需的市场状态输入，例如 spread、liquidity、price change、last trade price、orderbook depth 和 market end time。
- Binance REST、Binance WS、Polymarket market refresh、Polymarket market WS 的服务健康状态。

规则：

- 第一版只使用内存状态。
- 状态仓库保持简单、可替换，方便后续切到 SQLite/PostgreSQL repository。
- 每个服务都记录 last update 和 last error。

### 步骤 4：实现 Binance REST 历史 K线补齐

新增：

- `app/clients/binance_client.py`
- `app/services/binance_data.py`

使用 Binance REST endpoint：

```text
GET https://api.binance.com/api/v3/klines
```

默认参数：

- `symbol=BTCUSDT`
- `interval=1m,5m,15m,30m,1h,4h`
- `limit=300`

规则：

- 将 Binance kline array 转换为标准化 `Candle`。
- REST 获取到的历史 K线标记为已收盘。
- App 启动时先补齐所有配置 interval 的历史 K线，再依赖指标。
- 成功/失败都要更新 service health。

验收标准：

- `/candles?symbol=BTCUSDT&interval=1m&limit=200` 返回真实 Binance K线。
- `/candles` 支持 `1m`、`5m`、`15m`、`30m`、`1h`、`4h`。

### 步骤 5：实现指标计算

新增 `app/services/indicators.py`。

实现：

- RSI，默认周期 `14`。
- Bollinger Bands，默认周期 `20`，标准差倍数 `2`。
- 趋势状态：`up`、`down`、`flat`。

规则：

- 只使用已收盘 K线。
- 历史补齐后计算一次指标。
- 每收到一根 WebSocket 已收盘 K线后重新计算指标。
- 如果 K线数量不足，返回 `null` 值或明确的 `insufficient_data` 状态。

验收标准：

- `/indicators/latest?symbol=BTCUSDT` 返回 `1m`、`5m`、`15m`、`30m`、`1h`、`4h` 的真实指标。

### 步骤 6：实现 Binance WebSocket

扩展 `app/services/binance_data.py`。

订阅：

```text
wss://stream.binance.com:9443/stream?streams=btcusdt@kline_1m/btcusdt@kline_5m/btcusdt@kline_15m/btcusdt@kline_30m/btcusdt@kline_1h/btcusdt@kline_4h
```

规则：

- 只有 Binance kline payload 中 `k.x == true` 时，才写入正式 K线。
- 按 symbol、interval、open time 去重。
- 每收到 closed candle 后重新计算指标。
- 断线后使用 backoff 重连。
- service health 需要体现 `connected`、`reconnecting`、`error` 等状态。

验收标准：

- `/status` 返回 Binance WS 健康状态。
- 不重启服务时，K线可以持续更新。

### 步骤 7：实现 Polymarket 市场发现

新增：

- `app/clients/polymarket_client.py`
- `app/services/polymarket_market.py`

使用 Polymarket Gamma API。第一优先级使用 slug 直查：

```text
GET https://gamma-api.polymarket.com/events/slug/btc-up-or-down-15m-<timestamp>
GET https://gamma-api.polymarket.com/events/slug/btc-up-or-down-5m-<timestamp>
GET https://gamma-api.polymarket.com/events/slug/btc-updown-15m-<timestamp>
GET https://gamma-api.polymarket.com/events/slug/btc-updown-5m-<timestamp>
```

可选 fallback：

```text
GET https://gamma-api.polymarket.com/events?active=true&closed=false&limit=100
```

第一版过滤规则：

- event/market title 或 slug 包含 `bitcoin` 或 `btc`。
- market 看起来是短周期 BTC 市场，尤其是 `5m`、`15m`、`5 minute`、`15 minute`。
- 默认不启用全量 events fallback，避免 response 过大和刷新过慢。

规则：

- 尽量同时追踪当前期和下一期 BTC 5m/15m 市场。
- 提取 event title、market title、event slug、market id、`condition_id`、YES token ID、NO token ID、end time 和 status。
- 默认每 `30` 秒刷新一次。
- 如果某个 market 结构和预期不同，不要让整个 app 失败；把 parser warning 写入 service health。

验收标准：

- `/markets` 在市场可用时返回真实 Polymarket BTC 5m/15m 市场。
- `/status` 返回 Polymarket market refresh 健康状态。

### 步骤 8：实现 Polymarket Market WebSocket（已完成）

新增 `app/services/polymarket_ws.py`。

使用配置的 market WS URL：

```text
wss://ws-subscriptions-clob.polymarket.com/ws/market
```

使用步骤 7 发现到的 YES/NO token IDs 订阅。

处理消息：

- orderbook / book snapshot。
- price change。
- last trade price。
- 可用时处理 best bid/ask。
- 后续行情信号所需的盘口变化和市场状态变化。

规则：

- Market WS 按 token ID 订阅。
- 如果发现的 markets 变化，需要刷新订阅。
- 按 token ID 保存最新 orderbook 状态。
- 保存足够的快照字段，方便后续计算 spread、流动性、orderbook imbalance、价格跳变和成交活跃度。
- 断线后使用 backoff 重连。
- 更新 service health。

验收标准：

- `/orderbook/latest?token_id=...` 返回真实 orderbook / best bid / ask 数据。
- `/orderbook/latest` 不传 token ID 时，返回第一个有可用数据的追踪 token。
- 数据结构可以被后续行情信号模块直接消费。

完成记录：

- 已新增 `app/services/polymarket_ws.py`。
- 已使用 `wss://ws-subscriptions-clob.polymarket.com/ws/market` 订阅 Polymarket market channel。
- 已按 YES/NO token ID 订阅 `book`、`price_change`、`best_bid_ask` 等 market data。
- 已处理 PING、断线重连和新 token 订阅刷新。
- 已将 orderbook snapshot、best bid/ask、spread、liquidity 写入 `state_store`。
- 已由 `/orderbook/latest` 返回真实 Polymarket market data。

### 步骤 9：接入 FastAPI Lifespan（已完成）

更新 `app/main.py`，或新增 `app/core/lifecycle.py`。

启动任务：

- Binance REST backfill。
- Binance WS loop。
- Polymarket market refresh loop。
- Polymarket market WS loop。

关闭任务：

- 取消后台 tasks。
- 优雅关闭 clients 和 WebSocket 连接。

规则：

- API request handlers 只读 state store。
- API request handlers 不直接请求 Binance 或 Polymarket。
- 如果某个服务挂了，API 仍然返回最新已知状态和 service health。

验收标准：

- App 启动时自动启动阶段 1 的后台数据服务。
- App 关闭时不会留下孤儿任务。

完成记录：

- 已在 `app/core/lifecycle.py` 中启动 Binance REST backfill。
- 已启动 Binance WebSocket loop。
- 已启动 Polymarket Gamma market refresh loop。
- 已启动 Polymarket market WebSocket loop。
- App shutdown 时会取消后台 tasks，并处理 `CancelledError`。

### 步骤 10：替换 Mock-backed Routes（已完成）

替换这些接口的数据源：

- `/status`
- `/markets`
- `/candles`
- `/indicators/latest`
- `/orderbook/latest`
- `/signals`
- `/signals/latest`
- `/signals/preview`

暂时保留 mock 或空数据：

- `/orders`
- `/notifications`
- `/stats/summary`

规则：

- 阶段 1 替换真实数据时，前端不需要改结构。
- Polymarket 市场暂时不可用时，返回清晰的空状态，而不是让接口失败。

验收标准：

- Dashboard 展示真实 Binance K线和指标。
- Markets 页面在发现市场后展示真实 Polymarket BTC markets。
- Orderbook 面板在 token 订阅成功后展示真实 market data。

完成记录：

- `/status` 已读取 `state_store` 的真实 service health。
- `/markets` 已返回 Polymarket Gamma 发现到的 BTC 5m/15m live markets。
- `/candles` 已返回 Binance REST/WS 写入的真实 closed candles。
- `/indicators/latest` 已返回真实 closed candles 计算得到的 RSI、Bollinger Bands 和 trend。
- `/orderbook/latest` 已返回 Polymarket market WS 写入的真实 orderbook。
- `/signals`、`/signals/latest`、`/signals/preview` 已在阶段 1.2 切换为真实指标和盘口派生信号。
- `/orders`、`/notifications`、`/stats/summary` 仍保留为阶段 2/3 的预留 mock 数据。

## 配置项

配置主入口改为 `config.yaml`。仓库提交 `config.example.yaml`，本地复制为 `config.yaml` 后按需修改。

```yaml
binance:
  symbol: BTCUSDT
  rest_base_urls:
    - https://api.binance.com
    - https://api1.binance.com
    - https://api2.binance.com
    - https://api3.binance.com
  ws_base_urls:
    - wss://stream.binance.com:9443
  candle_history_limit: 300
```

规则：

- `config.yaml` 不提交 git。
- `.env` 只保留 `CONFIG_FILE=config.yaml` 这类入口配置。
- Binance REST client 按 `rest_base_urls` 顺序尝试，失败后切换下一个 endpoint。
- Binance WS 在步骤 6 实现时复用 `ws_base_urls` 做重连和 endpoint failover。

## 阶段 1 最终验收清单

- `uv sync --dev` 成功。
- `npm run build` 成功。
- `uv run uvicorn app.main:app --reload --port 8000` 可以启动后端。
- `/health` 返回 ok。
- `/status` 返回实时 service health。
- `/candles` 返回真实 Binance K线。
- `/indicators/latest` 返回基于真实 Binance K线计算的指标。
- `/markets` 在市场可用时返回真实 Polymarket BTC 5m/15m 市场。
- `/orderbook/latest` 在订阅成功后返回真实 Polymarket market data。
- 前端 Dashboard 不再依赖 `mock_data.py` 展示阶段 1 数据。
- `/signals` 不再依赖 `mock_data.py`，已返回真实技术指标信号和行情盘口信号。
- 前端 Markets 中的市场标题可以进入只读市场详情页；详情页直接订阅 Polymarket market WebSocket 展示 YES/NO 实时盘口变化，不使用定时轮询模拟实时价格。
