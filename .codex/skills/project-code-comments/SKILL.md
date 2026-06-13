---
name: project-code-comments
description: Use when editing code in this poly-auto-trading project and the change introduces or touches non-obvious business flow, data flow, service boundaries, stateful caches, async/background processing, external integrations, error handling, or project-specific conventions. Add concise Chinese comments where they improve future maintainability.
---

# Project Code Comments

## Principle

在这个项目中，适当加入中文注释，帮助后续维护者理解“为什么这样设计”和“这段代码在数据流里承担什么职责”。注释应解释意图、边界和扩展点，不要逐行翻译代码。

## When To Add Comments

添加中文注释的优先场景：

- 新增或修改跨服务数据流，例如数据源事件、信号上下文、通知、WebSocket、持久化之间的编排。
- 新增状态缓存、内存窗口、去重、合并、节流、重试、回填、并发控制等逻辑。
- 代码依赖项目业务约定，例如 Polymarket、Binance、Telegram、指标计算、账户分析、报表快照等领域语义。
- 某个函数或类的职责边界容易和其他层混淆。
- 保留向后兼容字段、旧接口或过渡逻辑时，需要说明原因。

## What To Avoid

避免这些注释：

- 不要解释 Python/TypeScript 语法或显而易见的赋值、循环、返回值。
- 不要写和代码重复的注释，例如“遍历列表”“调用函数”“设置变量”。
- 不要写长段落；优先一行或两行说明。
- 不要给所有函数机械加注释，只给有理解成本的地方加。

## Style

- 使用中文注释，技术名词和代码概念可保留英文，例如 `SignalInput`、`backfill`、`WebSocket`。
- 类或模块级注释说明职责；复杂方法内部注释说明关键步骤。
- 注释应靠近对应代码，避免文件顶部堆积无关背景。
- 如果注释描述架构边界，明确上游、下游和当前层不负责什么。

## Example

```python
class MarketSignalPipeline:
    """市场信号流水线：把各数据源事件转成信号上下文，再分发给下游。"""

    def replace_live_candles(self, symbol: str, interval: str, candles: list[Candle]) -> None:
        # REST backfill 后用数据库中的最近窗口重置内存态，保证 WS 增量计算有历史。
        ...
```
