# Step 2 数据管线重构：System Task + 标准化 K 线 + 指标重建

## 背景

当前 K 线下载、指标计算分别使用独立任务表和独立前端管理入口：

- `kline_backfill_tasks`
- `kline_backfill_progress`
- `indicator_backfill_tasks`
- `indicator_backfill_progress`

这两套表结构高度重复，任务状态、进度、前端展示也重复维护。与此同时，K 线质量标记 `CandleQuality` 带来的复杂度高于实际收益。后续需要把任务管理集中到统一的 system task 模型，并把 K 线入库口径改成标准化数据管线。

本次按“全新功能”处理：旧 K 线、旧指标、旧任务历史都可以删除并重建。

## 核心决策

- 删除 `CandleQuality`、`Candle.quality`、`quality_counts` 和 K 线质量审计接口。
- K 线采用标准化模式：
  - `open_time` 必须按 interval 对齐。
  - `close_time` 不使用 Binance 原始值，统一保存为 `open_time + interval - 1ms`。
  - OHLCV 坏形态直接拒绝。
  - `volume == 0` 的零成交占位 K 线允许入库。
  - `1w` 使用 Binance UTC 周一 00:00 作为周线锚点。
- 保留 `KlinePage.next_start_ms` 和 `raw_count`，分页游标仍按 Binance 原始 row 推进。
- 删除旧任务历史，不迁移旧任务表数据。
- 删除旧 `candles` 和 `indicator_snapshots` 数据，后续重新下载和重算。
- 前端新增单独菜单“系统任务”，集中管理 K 线下载和指标计算。
- “系统配置”页不再放 K 线和指标任务管理入口。

## 数据库改造

新增迁移：

1. 清空旧行情和指标数据：
   - `DELETE FROM indicator_snapshots`
   - `DELETE FROM candles`
2. 删除旧任务表：
   - `indicator_backfill_progress`
   - `indicator_backfill_tasks`
   - `kline_backfill_progress`
   - `kline_backfill_tasks`
3. 新增 `system_tasks`：
   - `id`
   - `task_type`
   - `symbol`
   - `status`
   - `message`
   - `error`
   - `total_inserted`
   - `started_at`
   - `finished_at`
   - `metadata`
   - `created_at`
   - `updated_at`
4. 新增 `system_task_steps`：
   - `id`
   - `task_id`
   - `step_key`
   - `interval`
   - `status`
   - `start_ms`
   - `cursor_ms`
   - `end_ms`
   - `inserted_count`
   - `raw_count`
   - `last_error`
   - `started_at`
   - `finished_at`
   - `created_at`
   - `updated_at`

字段约定：

- `task_type`: `kline_backfill` 或 `indicator_backfill`。
- `step_key`: step 唯一键。K 线任务使用 `interval:start_ms`，指标任务使用 interval。
- `interval`: step 对应的 K 线周期。
- `start_ms`: step 原始起点。K 线任务表示缺口起点，指标任务表示本次指标计算起点。
- `cursor_ms`: 原 `next_start_ms`。
- `end_ms`: K 线任务的缺口终点；指标任务可为空。
- `raw_count`: K 线任务记录 Binance 原始返回行数；指标任务默认为 `0`。
- task 级 `metadata`: 放任务级配置，例如 `batch_candles`、`warmup_bars`、并发数、目标结束时间。

## 后端开发步骤

### Step 1：抽出 K 线 interval 工具

- 新增中性模块，例如 `backend/app/services/candle_intervals.py`。
- 放入：
  - `CANDLE_INTERVAL_MS`
  - `align_interval_open_ms()`
  - `standard_close_time()`
  - `validate_aligned_open_time()`
  - `kline_open_ms()`
- 替换 `candle_backfill.py`、`binance_client.py`、`binance_monitor.py`、前端规则对应的 interval 逻辑引用。

### Step 2：删除 CandleQuality

- 从 `Candle` schema 删除 `quality` 字段和 `CandleQuality` 类型。
- 删除 `candle_quality.py`。
- 删除 `quality_counts` 相关后端字段、metadata 写入、序列化和测试。
- 删除 `/api/candles/audit` 与 `list_candle_quality_audit()`。
- 删除前端 `CandleQuality` 类型和质量统计展示。

### Step 3：标准化 Binance K 线解析

- `BinanceClient._parse_kline()` 解析 Binance row 时：
  - 以 row open time 生成 `open_time`。
  - 校验 open time 对齐当前 interval。
  - 用 `standard_close_time(open_time, interval)` 生成 `close_time`。
  - 校验 OHLCV 合法。
  - 允许零成交占位 K 线。
- `KlinePage` 保留：
  - `candles`
  - `next_start_ms`
  - `raw_count`
- `fetch_klines()` 继续兼容返回 `list[Candle]`，内部调用 `fetch_klines_page()`。

### Step 4：新增 System Task 模型与 Store

- 在 ORM 中新增：
  - `SystemTask`
  - `SystemTaskStep`
- 新增 `SystemTaskStore`，统一提供：
  - 查询 latest task。
  - 查询 latest resumable task。
  - 创建 task。
  - 创建或恢复 steps。
  - 更新 step cursor、inserted、raw count。
  - 汇总 task `total_inserted`。
  - 标记 task running、completed、error。
- 所有任务状态写入都通过该 store，避免 runner 直接操作表细节。

### Step 5：改造 K 线回填 Runner

- `candle_backfill_runner` 不再依赖 `KlineBackfillTask` 和 `KlineBackfillProgress`。
- 使用 `SystemTaskStore` 管理 `task_type=kline_backfill`。
- 每次触发任务时按 interval 计算目标覆盖范围，再统一探测缺口。
- 每个 `system_task_steps` row 表示一个待下载缺口，不再按业务场景拆分模式。
- 保留能力：
  - 断点续跑。
  - 全新库只初始化最近窗口，避免从 1970 拉全量。
  - 本地已有数据时补历史边界缺口、中间断档和最新缺口。
  - `raw_count` 统计。
  - `candle_ranges` 展示。
- 删除质量统计相关逻辑。

### Step 6：改造指标回填 Runner

- `indicator_backfill_runner` 不再依赖 `IndicatorBackfillTask` 和 `IndicatorBackfillProgress`。
- 使用 `SystemTaskStore` 管理 `task_type=indicator_backfill`。
- 每个 interval 对应一个 `system_task_steps` row。
- 计算前按 `open_time` 连续性切 segment：
  - 相邻 K 线差值必须等于 interval ms。
  - 不跨缺口延续 RSI/EMA/BOLL。
  - segment 太短时不写误导性指标。
- 因为 `indicator_snapshots` 会清空，首次执行是完整重建。

### Step 7：新增统一 System Task API

新增接口：

- `GET /api/system-tasks?symbol=BTCUSDT`
- `GET /api/system-tasks/latest?task_type=kline_backfill&symbol=BTCUSDT`
- `GET /api/system-tasks/latest?task_type=indicator_backfill&symbol=BTCUSDT`
- `POST /api/system-tasks/kline_backfill/start?symbol=BTCUSDT`
- `POST /api/system-tasks/indicator_backfill/start?symbol=BTCUSDT`

统一响应模型 `SystemTaskStatus`：

- task 基础字段。
- `steps[]`。
- K 线任务附加 `candle_ranges`。
- step 中包含：
  - `step_key`
  - `interval`
  - `status`
  - `start_ms`
  - `cursor_ms`
  - `end_ms`
  - `inserted_count`
  - `raw_count`
  - `last_error`
  - `started_at`
  - `finished_at`

旧 `/api/candles/backfill` 和 `/api/indicators/backfill` 不再给前端使用。

## 前端开发步骤

### Step 1：新增菜单与路由

- 新增菜单：
  - path: `/system-tasks`
  - name: `系统任务`
- 新增页面：
  - `frontend/src/pages/SystemTasksPage.tsx`
- `App.tsx` 中增加路由类型、lazy import、菜单项和页面渲染。

### Step 2：新增统一 API 类型

- 在 `frontend/src/api/client.ts` 新增：
  - `SystemTaskType`
  - `SystemTaskStatus`
  - `SystemTaskStepStatus`
  - `api.systemTasks()`
  - `api.latestSystemTask()`
  - `api.startSystemTask()`
- 删除旧 backfill status 类型里不再使用的质量字段。

### Step 3：实现 SystemTasksPage

- 页面集中展示所有 system task。
- 顶部操作：
  - `启动 K 线下载`
  - `启动指标计算`
  - `刷新`
- 主表展示：
  - 任务类型
  - 状态
  - symbol
  - 当前 step
  - 已写入数量
  - raw count
  - 开始/结束时间
  - 错误
- 展开行展示 step 明细：
  - interval
  - 状态
  - 缺口起点
  - cursor
  - 缺口终点
  - inserted
  - raw_count
  - 错误

### Step 4：瘦身系统配置页

- `SystemStatusPage` 移除 K 线数据和指标数据两块任务管理。
- 保留：
  - API / Database 健康状态。
  - 服务状态。
  - 服务事件。
  - 权限 / 配置预留。
- 服务状态详情仍可展示 `kline_backfill` 和 `indicator_backfill` 的 health metadata。

## 验收标准

- 迁移后旧任务表不存在，`system_tasks` 和 `system_task_steps` 存在。
- 迁移后 `candles` 和 `indicator_snapshots` 为空。
- 后端不再引用 `CandleQuality`、`quality_counts`、旧 task/progress ORM。
- K 线下载能重新生成标准化 candles。
- 指标计算能基于新 candles 重新生成 `indicator_snapshots`。
- 缺口不会跨 segment 计算指标。
- 前端侧边栏有“系统任务”菜单。
- K 线下载和指标计算只在“系统任务”页管理。
- “系统配置”页不再有 K 线/指标任务启动入口。
- TypeScript 不再引用旧 backfill status 或 `CandleQuality`。

## 风险与注意事项

- 这是破坏性数据重建，部署后必须重新执行 K 线下载和指标计算。
- 如果 candles 数据量很大，清空和重建期间看盘页可能暂时没有历史图表。
- 指标结果会因为 K 线标准化和缺口切段策略发生变化。
- 前后端需要同批发布，否则前端会找不到新的 system task API。
