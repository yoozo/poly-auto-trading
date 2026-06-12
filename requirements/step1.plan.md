# Step 1 Plan: Polymarket Report 与 BTC 看盘开发计划

## 背景

当前项目已重建为前后端同仓库结构：

- 后端：Python 3.13 + uv + FastAPI + PostgreSQL。
- 前端：Vite + React + TypeScript + Ant Design + ProComponents + lightweight-charts。

当前只完成了基础骨架、健康检查、Binance REST 最近 K 线接口、BTC 看盘基础蜡烛图。`requirements/report-requirements.md` 中的大部分业务需求仍待实现。

## Phase 1: 基础设施和数据库

状态：已完成（2026-06-12）

备注：`docker compose up -d postgres` 因本机 `5432` 已被占用未启动 compose 容器；已使用当前本地 PostgreSQL 创建 `poly_auto_trading` 数据库，并完成 Alembic migration 验证。

目标：把项目从页面骨架变成可持续采集数据的系统。

后端：

- 初始化 Alembic。
- 建 PostgreSQL 表：
  - `candles`
  - `indicator_snapshots`
  - `service_events`
  - `analysis_tasks`
  - `accounts`
  - `activities`
  - `market_metadata`
- `/api/health` 增加 DB 连接检查。
- 增加统一 logging：
  - 服务启动
  - 外部 API 请求失败
  - WebSocket 重连
  - 任务失败堆栈
- 增加基础 service health API。

前端：

- 增加系统状态页。
- 显示 API、DB、Binance、Polymarket、Telegram 状态。
- 保留当前 Ant Design 后台布局。

验收：

- `docker compose up -d postgres` 后，后端能连接 PG。
- Alembic migration 能执行。
- `/api/health` 返回 API + DB 状态。

## Phase 2: BTC 看盘 v1

状态：已完成（2026-06-12）

备注：已参考 `polymarket-tool/tools/polymarket-trade-analysis/templates/btc_watch.html` 的周期、RSI、RSI EMA、RSI-EMA diff、BOLL 参数与配色实现。已验证后端测试、ruff、前端 build 和本地 BTC 看盘页面；页面可显示历史 K 线、BOLL、RSI 子图、最新价、收盘倒计时、实时流状态和 K 线 tooltip。

目标：先把 BTC 看盘做成真正可用的核心页面。

后端：

- Binance REST 拉历史 K 线。
- Binance WebSocket 常驻连接。
- 支持周期：
  - `1m`
  - `5m`
  - `15m`
  - `30m`
  - `1h`
  - `4h`
- candles upsert 入 PG。
- 保留未收盘 K 线状态。
- 计算指标：
  - RSI14
  - RSI EMA14
  - RSI-EMA diff
  - Bollinger20
- 提供 API：
  - `GET /api/candles`
  - `GET /api/indicators`
  - `GET /api/events/stream`，SSE 推送实时 K 线和指标。

前端：

- BTC Watch 页面完善：
  - 蜡烛图
  - 周期切换
  - BOLL 开关
  - RSI 开关
  - RSI 子图
  - K 线倒计时
  - tooltip
  - 最新价
  - WS/SSE 状态
- 配置保存到 `localStorage`：
  - 周期
  - BOLL 开关
  - RSI 开关

验收：

- 页面打开后能显示历史 K 线。
- 不刷新页面也能实时更新最新 K 线。
- 切换周期正常。
- BOLL 和 RSI 可开关。
- 断网/断 WS 后有状态提示和重连日志。

## Phase 3: BTC 提醒和 Telegram

目标：把看盘页面变成可提醒的监控工具。

后端：

- 增加 Telegram notifier。
- 支持测试发送。
- 增加提醒规则：
  - RSI <= 30 或 >= 70
  - RSI <= 20 或 >= 80
  - RSI-EMA diff 绝对值超过阈值
  - 收盘前预警
  - 收盘信号
- 实现冷却和去重。
- 记录 notification event。

前端：

- BTC Watch 增加 Telegram 配置弹窗。
- 支持：
  - 启用/关闭
  - bot token
  - chat id
  - cooldown
  - 测试发送
- 显示最近提醒记录。
- 图表上高亮触发信号点。

验收：

- 配置 Telegram 后可发送测试消息。
- 满足阈值时能触发提醒。
- 同一周期不会重复刷屏。
- 关闭提醒后不发送。

## Phase 4: Polymarket Activity 下载

目标：实现账号数据获取和本地缓存。

后端：

- Polymarket Gamma client：
  - `public-search`
  - profile URL 解析
  - wallet 地址识别
- Polymarket Data API client：
  - `/activity`
  - 分页下载
  - 去重
  - limit 控制
  - retry
- activity 入库。
- 账号表维护：
  - input
  - normalized user
  - proxy wallet
  - profile info
  - last download time
- 异步任务系统：
  - `running`
  - `done`
  - `error`
  - percent
  - message
  - result
- API：
  - `POST /api/reports/accounts/analyze`
  - `GET /api/reports/tasks/{task_id}`
  - `GET /api/reports/accounts`

前端：

- Reports 页面 v1：
  - 输入账号/profile URL/wallet
  - activity count
  - 开始分析
  - 进度条
  - 本地账号列表
  - 错误展示

验收：

- 输入 Polymarket profile 能解析 wallet。
- 能下载最近 N 条 activity。
- 下载过程有进度。
- 数据写入 PG。
- 已下载账号能在列表里看到。

## Phase 5: 账户收益分析

目标：实现报告核心价值。

后端：

- activity 聚合规则：
  - `TRADE`
  - `REDEEM`
  - `MERGE`
  - `SPLIT`
  - `MAKER_REBATE`
- market metadata cache：
  - Gamma markets/events
  - closed 市场不刷新
  - open 市场 TTL 刷新
- 收益计算：
  - 成本
  - 回收
  - PnL
  - ROI
  - maker rebate
  - 未结算敞口
  - 胜率
  - 平均盈利/亏损
  - 数据不完整识别
- 近期收益：
  - 1 天
  - 3 天
  - 7 天
  - 14 天
  - 30 天
- 排行榜：
  - 最大盈利市场
  - 最大亏损市场
  - 最好日期
  - 最差日期
- API：
  - `GET /api/reports/accounts/{account_id}/summary`
  - `GET /api/reports/accounts/{account_id}/markets`

前端：

- Reports 页面 v2：
  - 汇总 KPI
  - 近期收益卡片
  - 最近 7 天日期收益
  - 最大盈利/亏损
  - 市场明细表
- 市场明细支持：
  - 关键词搜索
  - 日期过滤
  - 只看双向持仓
  - 分页加载

验收：

- 对同一份 activity 数据，关键收益字段可复核。
- 大账户不会一次性渲染卡死。
- 市场明细筛选正确。

## Phase 6: 单市场详情

目标：补齐报告分析深度。

后端：

- CLOB price history client。
- 本地成交价 fallback。
- 单市场 activity 时间线。
- 底层资产 K 线匹配。
- API：
  - `GET /api/reports/markets/{market_id}`
  - `GET /api/reports/markets/{market_id}/prices`
  - `GET /api/reports/markets/{market_id}/activities`
  - `GET /api/reports/markets/{market_id}/underlying`

前端：

- Market Detail 页面：
  - 市场概览
  - activity 明细
  - BUY/SELL/REDEEM/MERGE/SPLIT 时间线
  - Up/Down 或 Yes/No token 价格图
  - 本地成交 fallback 曲线
  - 底层资产 K 线
  - RSI/BOLL
  - 订单/成交标注
  - 十字光标同步

验收：

- 点击市场明细能进入详情页。
- 有 CLOB 历史时显示 token 价格曲线。
- CLOB 不可用时显示本地成交价 fallback。
- 图表和成交标注时间对齐。

## Phase 7: 体验、性能和稳定性

目标：把系统打磨成长期可用的后台工具。

后端：

- 外部 API rate limit 和 retry 策略。
- 后台任务持久化。
- 服务重启后任务状态可恢复或明确失败。
- 日志查询 API。
- 数据清理策略。
- 指标计算性能优化。

前端：

- 路由级懒加载，解决当前 AntD bundle 过大。
- 表格虚拟滚动。
- 空状态、错误状态、重试按钮。
- 深色看盘模式。
- 权限/配置页预留。

验收：

- 前端构建无大 chunk 警告或明显下降。
- 大账户报告可流畅筛选。
- 服务异常时页面有明确提示。
- 日志能定位 Binance、Polymarket、Telegram、分析任务问题。

## 推荐优先级

建议按以下顺序推进：

1. Phase 1：数据库和基础设施
2. Phase 2：BTC 看盘 v1
3. Phase 4：Activity 下载
4. Phase 5：账户收益分析
5. Phase 3：Telegram
6. Phase 6：单市场详情
7. Phase 7：优化

原因：BTC 看盘和 activity 下载是两个数据入口，先把数据入口打通，后面的分析、提醒和报表才有基础。
