# 开发阶段计划

本文档记录 Polymarket BTC 5m/15m 量化交易系统的分阶段开发计划。项目需要按阶段推进，先验证数据质量和可观测性，再接入通知和用户订单事件，最后才开启自动交易。

## 当前完成状态

- 已完成：项目基础骨架，包括 FastAPI 后端、React/Vite 前端、`.env.example`、`.gitignore`、README。
- 已完成：Python 3.13 + uv 包管理，包含 `pyproject.toml`、`.python-version`、`uv.lock`。
- 已完成：阶段 1.1 的只读 Dashboard 初始版本，并已接入真实 Binance / Polymarket 数据。
- 已完成：项目启动 skill 已移动到当前项目目录，仅对本项目生效。
- 已完成：阶段 1 的真实 Binance / Polymarket 数据接入，包括 Binance REST/WS、Polymarket Gamma discovery、Polymarket market WS、orderbook、market result。
- 已完成：阶段 1.2 信号开发框架第一版，已用真实指标和盘口生成 technical / market signals。
- 未完成：阶段 2 Telegram 通知与 Polymarket 用户事件监听。
- 未完成：阶段 3 自动交易执行。

## 阶段 1：数据采集与标准化

目标：建立 Binance BTC K线和 Polymarket BTC 预测市场的数据基础。

交付内容：

- 通过 Binance REST 拉取 `BTCUSDT` 的 `1m`、`5m`、`15m`、`30m`、`1h`、`4h` 历史 K线，用于补足技术指标计算窗口。
- 通过 Binance WebSocket 订阅同样周期的实时 K线。
- 发现 Polymarket 当前可交易的 BTC 5m/15m 市场，包括 event、market、`condition_id`、YES token ID、NO token ID、结束时间和市场状态。
- 订阅 Polymarket market WebSocket，获取 orderbook、best bid/ask、last trade price、price change 等数据。
- 计算 RSI、Bollinger Bands 和基础趋势状态。
- 本地持久化 candles、markets、orderbook snapshots 和信号输入快照。

重要规则：

- 正式指标计算只使用 Binance 已收盘 K线。
- 同时追踪当前期和下一期 Polymarket BTC 5m/15m 市场。
- 明确区分 Polymarket 的 `condition_id` 和 token ID。
- 数据服务必须处理重连、重复消息和过期市场清理。

验收标准：

- `/status` 可以返回 Binance 和 Polymarket 市场数据服务的健康状态。
- `/markets` 可以返回当前追踪的 BTC 5m/15m 市场。
- `/candles` 可以返回 `1m`、`5m`、`15m`、`30m`、`1h`、`4h` 的最近 K线。
- `/indicators/latest` 可以返回 RSI、Bollinger 和趋势状态。
- 本阶段不要求真实交易，也不要求 Telegram 通知。

## 阶段 1.1：FastAPI + React/Vite 前端监控页面

目标：基于 FastAPI API 构建只读 React/Vite Dashboard，在接入通知和自动交易前，方便检查系统状态和数据质量。

交付内容：

- 在 `frontend/` 下实现 React/Vite 前端项目。
- FastAPI 提供只读 API，用于查询 status、markets、candles、indicators、orderbook、signals、orders、notifications 和 summary stats。
- Dashboard 页面展示 WS 状态、scheduler 状态、dry-run 状态、追踪市场数量、最近错误、K线图、指标、盘口和最新信号。
- Markets 页面展示 BTC 5m/15m 市场元数据、best bid/ask、spread、liquidity、状态、YES token 和 NO token。
- Signals 页面展示交易信号和被风控拦截的信号。
- Orders 页面预留给阶段 2/3 的订单和通知事件。
- Stats 页面预留给后续信号、成交、spread、延迟和 PnL 统计。

重要规则：

- 阶段 1.1 的前端只读。
- 前端展示的运行状态、K线、指标、市场、信号、订单、通知和统计数据都必须自动刷新；默认刷新间隔为 15 秒，并在页面上显示最近刷新时间。
- 不在 UI 中暴露真实下单按钮或危险交易控制。
- FastAPI 作为 API/control plane；实时 WebSocket 服务作为后台服务运行，不放在请求处理逻辑里。
- 前端默认 API 地址为 `http://localhost:8000`，可以通过 `VITE_API_BASE_URL` 覆盖。

验收标准：

- `npm run build` 成功。
- 前端可以使用 mock API 数据正常渲染。
- Dashboard 能帮助识别市场缺失、K线异常、spread 过大、数据过期和信号被拦截等问题。

当前状态：

- 已完成：初始 FastAPI routes 和 React/Vite Dashboard scaffold。
- 当前 status、markets、candles、indicators、orderbook 和 signals 已接入真实 Binance / Polymarket 数据服务；orders、notifications 和 stats 仍保留为阶段 2/3 的预留数据。
- 已完成：Python 包管理确定为 Python 3.13 + uv + `pyproject.toml` + `uv.lock`。
- 阶段 1 真实数据接入的详细执行清单见 [阶段 1 真实数据接入计划](phase-1-real-data-plan.md)。

## 项目启动 Skill

已完成：创建 Codex skill：`poly-auto-trading-start`，并移动到当前项目目录，仅对本项目生效。

用途：

- 启动本项目的 FastAPI 后端和 React/Vite 前端。
- 检查 `uv`、`python3.13`、`npm`、`node` 是否可用。
- 执行 `uv sync --dev`。
- 在缺少 `frontend/node_modules` 时执行 `npm install`。
- 启动后端 `http://localhost:8000`。
- 启动前端 `http://localhost:5173`。
- 检查后端 `/health`。
- 将日志写入 `/private/tmp/poly-auto-trading/`。

Skill 路径：

```text
/Users/yoozo/Documents/poly-auto-trading/.codex/skills/poly-auto-trading-start
```

启动脚本：

```bash
python3.13 /Users/yoozo/Documents/poly-auto-trading/.codex/skills/poly-auto-trading-start/scripts/start_project.py
```

使用方式：

- 对 Codex 说“帮我启动项目”时，应使用这个 skill。
- 启动完成后，需要报告 backend URL、frontend URL、health check 结果和日志路径。
- 使用这个 skill 时不得修改 `.env`，也不得开启真实交易。

## 阶段 1.2：信号开发框架

目标：在真实数据采集和只读 Dashboard 稳定后，建立统一的信号开发框架，支持技术指标信号和行情信号两类输入。

信号类型：

- 技术指标信号：基于 Binance K线和指标计算，例如 RSI、Bollinger Bands、趋势、动量、均线斜率等。
- 行情信号：基于 Polymarket 市场状态和盘口变化，例如 spread、best bid/ask、orderbook imbalance、liquidity、price change、last trade price、成交活跃度、市场剩余时间、YES/NO 价格偏移等。

交付内容：

- 已实现统一的 `SignalService`，把不同来源的信号标准化为同一种信号结构。
- 已生成技术指标信号和行情信号第一版，后续可继续拆分为独立策略模块。
- 已在信号结果中记录触发来源、触发原因、置信度、相关 market、指标快照和盘口快照。
- Dashboard 的 Signals 页面已展示技术指标信号、行情信号和被过滤/拦截的信号。
- Stats 页面后续可以按信号类型统计命中率、平均 spread、成交延迟和模拟 PnL。

重要规则：

- 阶段 1.2 只生成和展示信号，不发送 Telegram 通知，不执行真实交易。
- 技术指标信号和行情信号都必须能被 dry-run / paper 模式消费。
- 行情信号必须依赖 Polymarket market data 的新鲜度；如果 orderbook 或 market data 过期，信号应标记为不可用或被拦截。
- 信号需要保留足够上下文，方便后续复盘为什么产生、为什么被拦截、是否适合交易。

验收标准：

- 已完成：`/signals` 可以返回至少一种技术指标信号和一种行情信号。
- 已完成：`/signals/latest` 可以返回最新信号，并明确 `signal_type`。
- 已完成：Dashboard 可以区分展示技术指标信号和行情信号。
- 已完成：信号不会触发真实下单。

## 阶段 2：Telegram 通知与 Polymarket 用户事件监听

目标：在开启自动交易前，先建立可靠的外部通知能力，并接入经过认证的 Polymarket 用户订单事件。

交付内容：

- 实现 Telegram bot client，用于发送系统、信号、订单和错误通知。
- 使用 Polymarket CLOB 凭证连接 user WebSocket。
- 处理用户事件：订单提交、订单更新、撤单、部分成交、完全成交、拒单和 trade 生命周期更新。
- 使用稳定 event key 做通知去重。
- 持久化 notifications 和用户订单事件。
- Dashboard 的 Orders 和 Notifications 区域切换为真实持久化事件。

重要规则：

- Polymarket user WS 按 `condition_id` 订阅，market WS 按 token ID 订阅。
- WebSocket 重连不能导致 Telegram 重复通知。
- 订单通知事件必须包含足够上下文，方便关联 market ID、token ID、side、price、size、filled quantity 和 status。
- 真实交易仍然默认关闭。

验收标准：

- Telegram 可以收到信号、系统健康、WS 断开/重连和订单生命周期通知。
- `/notifications` 可以返回已发送通知历史。
- `/orders` 在配置凭证后可以返回 Polymarket 用户订单状态。
- dry-run 模式可以模拟与真实用户事件一致的通知结构。

## 阶段 3：自动交易执行

目标：基于交易信号、用户订单事件和风控检查，开启受控的自动交易。

交付内容：

- 实现执行状态机：
  - `IDLE`
  - `BUY_LIMIT_PLACED`
  - `BUY_PARTIALLY_FILLED`
  - `BUY_FILLED`
  - `SELL_PLACED`
  - `SELL_PARTIALLY_FILLED`
  - `SELL_FILLED`
  - `CANCELLED`
  - `EXPIRED`
  - `ERROR`
- 实现 Risk Service，检查 max order USDC、max daily loss、max spread、minimum liquidity、市场剩余时间、数据过期、重复信号和已有敞口。
- 向 Polymarket CLOB 提交 limit buy order。
- buy 成交后，基于实际成交数量挂 sell order。
- 对未成交 buy/sell order 做超时处理和市场临近结束处理。
- dry-run 执行链路与真实执行事件保持一致。
- 记录所有 signal、risk、order、fill、cancel、notification 决策的审计日志。

重要规则：

- 真实交易默认关闭。
- `DRY_RUN=true` 必须是默认值。
- 不能假设 buy order 一定完全成交。
- sell order 必须基于实际成交数量下单。
- 市场接近结束时禁止新开仓。
- 数据过期或 WS 健康状态异常时禁止执行交易。

验收标准：

- dry-run 可以跑通完整链路：signal -> risk check -> buy intent -> fill simulation -> sell intent -> notification。
- 真实下单必须同时满足 `TRADING_ENABLED=true` 和有效 Polymarket 凭证。
- 重复信号不能创建重复 active order。
- Dashboard 和 Telegram 展示的执行状态一致。

## 后续扩展

- 将 mock data 替换为真实数据库 repository。
- 长期运行部署时切换到 PostgreSQL。
- 增加历史 replay/backtesting，用于验证信号质量。
- 增加更丰富统计：信号命中率、平均 spread、成交延迟、胜率、PnL 和按市场维度的表现。
- 只读 Dashboard 稳定后，再加入带认证的 pause/resume 和 dry-run 状态控制。
- Celery 只作为后续重型离线任务选项，例如回测、参数扫描和报告生成。
