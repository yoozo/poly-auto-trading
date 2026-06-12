# Polymarket Report 需求文档

## 1. 背景

当前项目提供一个本地化 Polymarket 报告工具，用于围绕指定 Polymarket 用户账号或钱包地址，下载公开交易活动，生成账户收益分析报告，并补充市场结果、价格走势、底层资产 K 线和提醒能力。

本文档描述重构后的产品需求和外部对接内容，不限定前端、后端或存储层的具体技术实现。

## 2. 目标

1. 支持用户输入 Polymarket profile、profile URL 或钱包地址，生成账户收益分析报告。
2. 支持复用本地已下载的 activity 数据，减少重复下载。
3. 支持补全市场元数据，识别市场状态、实际结果、市场时间和 token 信息。
4. 支持查看账户整体收益、近期收益、单市场收益和持仓状态。
5. 支持查看单市场价格走势、交易明细和底层资产指标。
6. 支持 BTC 看盘和 Telegram 提醒，辅助短周期市场监控。

## 3. 对接内容

### 3.1 Polymarket Data API

用途：下载指定用户的钱包 activity。

对接接口：

- `https://data-api.polymarket.com/activity`

主要参数：

- `user`：用户 proxy wallet 地址。
- `limit`：单页数量。
- `offset`：分页偏移。
- `sortBy=TIMESTAMP`
- `sortDirection=DESC`
- `end`：用于继续向更早历史翻页。

使用场景：

- 下载用户最近 N 条 activity。
- 支持大数据量分页下载。
- activity 类型包括 `TRADE`、`REDEEM`、`MERGE`、`SPLIT`、`MAKER_REBATE` 等。

产出数据要求：

- 需要保存用户、钱包、profile 信息、activity 明细、时间范围、activity 类型统计等。
- 需要支持后续复用，避免每次查看报告都重新下载。

### 3.2 Polymarket Gamma API

用途：解析用户和补全市场元数据。

对接接口：

- `https://gamma-api.polymarket.com/public-search`
- `https://gamma-api.polymarket.com/markets`
- `https://gamma-api.polymarket.com/markets/slug/<slug>`
- `https://gamma-api.polymarket.com/events`
- `https://gamma-api.polymarket.com/events/slug/<slug>`

使用场景：

- 将 profile name、pseudonym 或 profile URL 解析为 proxy wallet。
- 根据市场 slug 批量查询 market / event 元数据。
- 获取市场标题、开始时间、结束时间、是否关闭、outcomes、clob token IDs、outcome prices 等。
- 根据已关闭市场的 outcome prices 推断官方结果。

缓存规则：

- 已关闭市场不再刷新。
- 未关闭市场默认按 TTL 刷新。
- 支持批量和并发刷新。

### 3.3 Polymarket CLOB API

用途：获取预测市场 token 的价格历史。

对接接口：

- `https://clob.polymarket.com/prices-history`

主要参数：

- `market`：asset / token id。
- `startTs`：开始时间，Unix 秒。
- `endTs`：结束时间，Unix 秒。
- `interval=1m`
- `fidelity=10`

使用场景：

- 单市场详情页绘制 Up / Down 或 Yes / No 的价格走势。
- 如果 CLOB 历史价格不可用，页面应回退显示本地成交价走势。

### 3.4 Binance REST API

用途：获取底层资产 K 线。

对接接口：

- `https://api.binance.com/api/v3/klines`

支持标的：

- `BTCUSDT`
- `ETHUSDT`
- `SOLUSDT`
- `XRPUSDT`

支持周期：

- `1s`
- `1m`
- `5m`
- `15m`
- `30m`
- `1h`
- `4h`

使用场景：

- 单市场详情页加载底层资产 K 线。
- 计算 RSI14、Bollinger20 等指标。
- BTC 看盘页初始化历史 K 线。

### 3.5 Binance WebSocket

用途：获取 BTCUSDT 实时 K 线更新。

对接地址：

- `wss://stream.binance.com:9443/ws`
- `wss://stream.binance.com:443/ws`

使用场景：

- BTC 看盘页实时更新 K 线。
- 更新 RSI、EMA、Bollinger。
- 支持断线重连和备用 endpoint。

### 3.6 Telegram Bot API

用途：发送看盘提醒和测试消息。

对接接口：

- `https://api.telegram.org/bot<botToken>/sendMessage`

主要参数：

- `botToken`
- `chatId`
- `text`
- `parseMode`

使用场景：

- BTC 看盘页保存 Telegram 配置后发送测试消息。
- 满足监控条件时发送提醒。

安全要求：

- bot token 和 chat id 需要校验格式。
- 消息长度需要限制。
- 不应在服务端持久化敏感 token，除非后续增加明确的加密存储方案。

## 4. 功能需求

### 4.1 账户选择与数据下载

用户可以输入：

- Polymarket 用户名。
- `@profile`。
- Polymarket profile URL。
- `0x` 钱包地址。

系统需要：

- 规范化用户输入。
- 对非钱包输入调用 Gamma API 解析 proxy wallet。
- 如果远程解析失败，可尝试读取本地 YAML 中缓存的钱包地址。
- 支持配置下载 activity 数量。
- 支持手动刷新 activity。
- 下载过程需要展示进度。

### 4.2 账户数据管理

系统需要维护已分析账号的数据列表。

用户可以：

- 选择已分析账号打开报告。
- 收藏账号。
- 为收藏账号添加备注。
- 自动记住上次打开的账号。
- 自动记住 activity 下载数量。

### 4.3 账户收益汇总

账户报告需要展示：

- 数据统计口径。
- activity 数量。
- 市场数量。
- 数据时间范围。
- 报告生成时间。
- 全部市场收益。
- 全部市场加 maker rebate 收益。
- 已结算市场收益。
- ROI。
- 平均成本、中位成本、最大成本。
- 胜率、盈利市场数、亏损市场数、打平市场数。
- 平均盈利和平均亏损。
- 未结算市场数量和买入成本。
- 数据不完整市场数量。
- maker rebate 条数和金额。

### 4.4 近期收益分析

报告需要展示最近：

- 1 天。
- 3 天。
- 7 天。
- 14 天。
- 30 天。

每个周期需要统计：

- 市场数。
- 已结算市场数。
- 未结算市场数。
- 成本。
- 回收。
- 收益。
- ROI。
- 胜率。
- 未结算敞口。

另外需要展示最近 7 天按日期拆分的收益。

### 4.5 市场排行榜

报告需要展示：

- 最大盈利市场。
- 最大亏损市场。
- 最好日期。
- 最差日期。

市场字段至少包含：

- 市场标题。
- 实际结果。
- 持仓状态。
- 成本。
- 收益。
- 收益率。
- 市场链接。

### 4.6 市场明细

市场明细需要按市场维度横向展示。

字段包括：

- 市场标题。
- 实际结果。
- 持仓状态。
- Redeem time。
- 市场日期。
- 交易数。
- Redeem count。
- Merge count。
- 上涨成本 / 份额 / 平均成本。
- 下跌成本 / 份额 / 平均成本。
- 回收 / 成本。
- Merge return。
- 收益 / 收益率。
- 若上涨收益 / 收益率。
- 若下跌收益 / 收益率。

交互要求：

- 支持市场关键词搜索。
- 支持按市场日期过滤。
- 支持只显示同时持有 Up 和 Down 份额的市场。
- 支持横向滚动。
- 支持滚动到右侧时继续加载更多市场。
- 点击市场标题可进入单市场详情页。

### 4.7 单市场详情

单市场详情页需要展示：

- 市场概览。
- 市场 activity 明细。
- 买入、卖出、redeem、merge、split 时间线。
- 成本、回收、收益、收益率。
- 当前持仓状态。
- Polymarket token 价格历史图。
- 本地成交价 fallback 曲线。
- 底层资产 K 线和指标图。

图表能力：

- 支持价格曲线。
- 支持 K 线。
- 支持 RSI14。
- 支持 Bollinger20。
- 支持订单 / 成交标注。
- 支持 tooltip。
- 支持图表联动和十字光标同步。

### 4.8 BTC 看盘

BTC 看盘页需要：

- 展示 BTCUSDT K 线。
- 支持多周期切换。
- 支持实时 WebSocket 更新。
- 支持 RSI 开关。
- 支持 Bollinger 开关。
- 支持 K 线倒计时。
- 支持图表 tooltip。
- 支持本地保存看盘配置。
- 支持 Telegram 配置、测试和提醒。

### 4.9 任务进度

分析任务需要异步执行，并提供进度反馈。

任务状态：

- `running`
- `done`
- `error`

进度内容：

- 任务 ID。
- 状态。
- 当前消息。
- 百分比。
- 结果。
- 错误信息。

## 5. 数据处理规则

### 5.1 activity 聚合

系统按市场聚合 activity，市场 key 优先级：

1. `title`
2. `slug`
3. `conditionId`
4. `(unknown)`

### 5.2 成本和回收

买入成本：

- `TRADE` 且 `side=BUY` 的 `usdcSize`。
- `SPLIT` 的 `usdcSize` 计入成本。

回收：

- `TRADE` 且 `side=SELL` 的 `usdcSize`。
- `REDEEM` 的 `usdcSize`。
- `MERGE` 的 `usdcSize`。

收益：

```text
收益 = 回收 - 买入成本
```

ROI：

```text
ROI = 收益 / 买入成本
```

### 5.3 持仓状态

系统需要按 outcome 计算当前剩余份额：

- BUY 增加份额。
- SELL 减少份额。
- SPLIT 对 Up / Down 市场增加双边份额。
- MERGE 减少双边份额。
- REDEEM 根据官方结果或推断结果减少对应 outcome 份额。

小于 dust 阈值的份额不展示。

### 5.4 市场结果

市场实际结果优先级：

1. Gamma API 元数据中的已关闭市场结果。
2. 根据 redeem 份额推断。
3. 未能识别时标记为 `未结算`。

Up / Down / Yes / No 需要映射为中文展示：

- `Up`：上涨。
- `Down`：下跌。
- `Yes`：是。
- `No`：否。

### 5.5 数据不完整识别

如果 redeem 份额无法和当前数据窗口内的买入份额匹配，则该市场标记为数据不完整。

数据不完整市场仍可展示，但已结算统计应避免将其作为可靠结算样本。

## 6. 非功能需求

### 6.1 性能

- activity 下载需要分页并支持并发窗口。
- 市场元数据刷新需要批量化。
- 大账户报告需要支持市场明细分页加载，避免一次性渲染过多列。

### 6.2 容错

- Profile 解析失败时，应支持使用本地 YAML 缓存的钱包地址。
- CLOB 价格历史不可用时，单市场详情页应回退到本地成交价曲线。
- Binance WebSocket 断线时应支持重连和备用 endpoint。
- 外部 API 返回错误时，需要展示明确错误信息。

### 6.3 安全

- 用户输入需要校验。
- Telegram token、chat id、消息内容需要校验。
- 敏感配置不应明文持久化，除非提供明确的加密或权限隔离方案。

### 6.4 可维护性

- 数据下载、报告分析、外部对接、展示层应保持职责分离。
- 对接外部 API 的字段解析需要集中管理。
- 收益计算规则需要保持可测试、可复核。

## 7. 当前边界

1. 报告基于公开 activity，不包含无法从公开接口拿到的私有订单状态。
2. 未结算市场默认按当前回收为准，剩余持仓敞口单独展示。
3. 如果 activity 拉取窗口不完整，部分 redeem 市场可能无法准确匹配历史买入份额。
4. Telegram 配置当前以页面配置为主，不作为服务端账户系统管理。
5. 技术实现方式不做限定，只需满足本需求定义的数据对接、分析规则和交互能力。
