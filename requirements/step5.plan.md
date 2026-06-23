# Polymarket API 更新监控 GitHub Actions 方案

## 背景

项目依赖 Polymarket 的 CLOB、市场数据、WebSocket、SDK 等接口。为了避免接口变更、限流调整、迁移窗口或 SDK 发布后程序才被动发现问题，需要建立一个自动监控机制：定时检查官方变更来源，发现更新后推送消息。

本方案优先使用 GitHub Actions 实现，不引入常驻服务。

## 目标

- 定时监控 Polymarket 官方 API 相关变更。
- 识别 changelog、文档索引、状态页、SDK release 的新增内容。
- 对变化进行风险分级，并推送到配置的通知渠道。
- 避免重复推送同一条变更。
- 不在仓库主分支产生周期性状态提交。

## 官方监控来源

- Polymarket Changelog: `https://docs.polymarket.com/changelog`
- Polymarket 文档索引: `https://docs.polymarket.com/llms.txt`
- Polymarket 状态页 API: `https://status.polymarket.com/v3/summary.json`
- 官方 SDK Releases:
  - `Polymarket/clob-client-v2`
  - `Polymarket/py-clob-client-v2`
  - `Polymarket/rs-clob-client-v2`

## 建议文件结构

```text
.github/workflows/polymarket-api-watch.yml
scripts/polymarket_api_watch.py
config/polymarket_watch_sources.yml
```

可选状态文件存放在独立分支：

```text
state/polymarket-api-watch.json
```

## GitHub Actions 设计

- 使用 `schedule` 每小时运行一次。
- 支持 `workflow_dispatch` 手动触发。
- 每次运行流程：
  1. Checkout 主分支代码。
  2. 拉取或读取 `polymarket-watch-state` 分支中的上次监控状态。
  3. 抓取所有官方来源。
  4. 生成结构化摘要和 hash。
  5. 与上次状态对比。
  6. 有新增或变化时推送通知。
  7. 将最新状态写回 `polymarket-watch-state` 分支。

建议 cron：

```yaml
on:
  schedule:
    - cron: "17 * * * *"
  workflow_dispatch:
```

## 状态保存策略

- 不建议把监控状态提交到主分支，避免定时任务污染业务提交历史。
- 建议创建独立分支 `polymarket-watch-state`。
- 状态文件只保存已处理内容的 hash、标题、来源、检测时间。
- 同一 hash 已推送过则跳过，保证 Action 重跑不会重复报警。
- 如果抓取失败，不覆盖旧状态，避免错误状态导致漏报。

## 风险分级规则

`critical`:

- `no backward compatibility`
- `migration`
- `signed order`
- `signature`
- `auth`
- `CLOB V2`
- `CLOB V3`
- `order struct`
- `removed`

`high`:

- `rate limit`
- `limit`
- `WebSocket`
- `endpoint`
- `deprecated`
- `batch size`
- `pagination`

`medium`:

- `added`
- `new field`
- `SDK`
- `release`

`low`:

- `docs update`
- `UI`
- 文案调整

## 通知内容

通知消息至少包含：

- 风险等级
- 来源
- 标题或摘要
- 可能影响的接口或模块
- 建议动作
- 原文链接
- 检测时间

示例：

```text
[HIGH] Polymarket API 变更检测

来源: Official Changelog
标题: GET /markets/keyset maximum limit reduced to 100
影响: markets keyset pagination
建议: 检查代码中 limit 是否超过 100，并确认分页使用 after_cursor / next_cursor
链接: https://docs.polymarket.com/changelog
```

## 你需要配置的内容

### 1. GitHub Actions 权限

仓库需要允许 workflow 写入状态分支。建议在 workflow 中配置：

```yaml
permissions:
  contents: write
```

如果组织或仓库默认禁用了 Actions 写权限，需要在 GitHub 仓库设置中打开：

- Settings
- Actions
- General
- Workflow permissions
- 选择 `Read and write permissions`

### 2. 状态分支

建议提前创建：

```text
polymarket-watch-state
```

该分支只用于保存 watcher 状态，不放业务代码。

### 3. 通知渠道 Secrets

第一版建议选择一个渠道即可。

Telegram:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

企业微信机器人:

```text
WECHAT_WORK_WEBHOOK_URL
```

飞书机器人:

```text
FEISHU_WEBHOOK_URL
```

如果后续需要邮件通知，可补充：

```text
SMTP_HOST
SMTP_PORT
SMTP_USERNAME
SMTP_PASSWORD
ALERT_EMAIL_TO
```

### 4. 可选 GitHub Token

监控公开 GitHub release 可以使用默认 `GITHUB_TOKEN`。如果遇到 GitHub API rate limit，可配置：

```text
POLYMARKET_WATCH_GITHUB_TOKEN
```

### 5. 推送策略配置

需要决定：

- 每小时推送所有变化，还是只推送 `medium` 及以上。
- `critical` 是否需要额外渠道，例如同时推 Telegram 和企业微信。
- 是否在工作时间外静默低等级通知。

## 实施阶段

### 第一阶段：最小可用版本

- 新增 GitHub Actions workflow。
- 新增 Python watcher 脚本。
- 监控 `changelog`、`llms.txt`、状态页 API。
- 支持 Telegram 或企业微信推送。
- 使用状态分支去重。

### 第二阶段：SDK 监控

- 增加 GitHub releases/tags 监控。
- 检测 `clob-client-v2`、`py-clob-client-v2`、`rs-clob-client-v2` 新版本。
- 将 SDK release 和 changelog 变更关联到同一条通知。

### 第三阶段：项目影响映射

- 建立关键词到项目模块的映射。
- 告警中提示可能影响的本地文件或功能，例如市场同步、订单取消、WebSocket、认证签名。
- 后续可扩展为自动创建 GitHub Issue。

## 验收标准

- 手动触发 GitHub Action 可以正常运行。
- 首次运行会初始化状态，不重复发送历史大量通知，或只发送一条初始化摘要。
- 官方来源新增内容后，下一次 Action 能检测并推送。
- 同一变更重复运行不会重复推送。
- 抓取失败时 Action 明确失败或发送 watcher 异常通知，且不会覆盖旧状态。
- 主分支不会出现周期性状态文件提交。

## 安全要求

- watcher 只访问公开官方文档、公开状态页和公开 GitHub release。
- 不需要配置 Polymarket 私钥、钱包助记词、CLOB API secret。
- 不允许在 GitHub Secrets、日志或通知中暴露任何交易密钥。
- 通知内容只包含公开变更摘要和链接。

## 待确认问题

- 第一版使用哪个通知渠道：Telegram、企业微信、飞书或邮件。
- 是否需要在 `critical` 级别时同时创建 GitHub Issue。
- 定时频率是否使用每小时一次，还是提高到每 30 分钟一次。
- 首次初始化时是否推送最近一条 changelog 摘要，还是只保存基线不推送。
