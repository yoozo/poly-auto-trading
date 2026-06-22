# Step 3 Polymarket 安全下单方案：MetaMask 签订单 + 多钱包 CLOB Credentials

## 背景

当前服务器没有启用 HTTPS。为了避免私钥或 CLOB API secret 在 HTTP 传输中泄露，下单链路需要区分两类授权：

- 钱包订单签名：由 MetaMask 对具体 BUY/SELL 订单做 EIP-712 签名，表示用户同意这笔买卖。
- CLOB API HMAC：由后端使用 CLOB API credentials 生成 `POLY_SIGNATURE`，用于提交 signed order、查询挂单、撤单和 User WS 认证。

系统还需要支持多个 Polymarket 钱包 profile，并能在前端切换当前 active wallet。切换后，仓位、挂单、User WS、撤单、signed order 提交都必须使用当前 active wallet。

## 安全决策

- 服务器不保存 `POLYMARKET_PRIVATE_KEY`。
- 前端不要求、不读取、不上传、不保存 private key 或 seed phrase。
- 在无 HTTPS 条件下，浏览器不通过 HTTP 向服务器传输 CLOB secret、passphrase。
- 前端可以用 MetaMask 生成 CLOB credentials，但只生成“用户手动执行”的服务器导入命令。
- 前端不把 CLOB credentials 写入 `localStorage`、`IndexedDB`、console、日志或错误上报。
- 后端不提供 HTTP 写 `.env` 或 HTTP 导入 CLOB credentials 的接口。
- 后端把多个 CLOB credentials 加密保存到数据库，不再依赖单一 `.env` 钱包配置。
- `.env` 只保存服务器级主密钥：
  - `POLYMARKET_CREDENTIALS_ENCRYPTION_KEY`
- 前端只把 signed order 发给后端，不发送 API secret。
- 后端只负责提交 signed order，不负责签 BUY/SELL 订单。
- signed order 不落库、不缓存、不打印日志，提交后即丢弃。

## 多钱包 Credentials 存储

新增数据库表 `polymarket_credentials`，每条记录代表一个钱包 profile：

- `id`
- `label`
- `signer_address`
- `funder_address`
- `signature_type`
- encrypted `api_key`
- encrypted `api_secret`
- encrypted `api_passphrase`
- `created_at`
- `updated_at`

使用 `POLYMARKET_CREDENTIALS_ENCRYPTION_KEY` 加密保存 credentials。API 只返回 masked 信息，不返回明文 `api_key`、`api_secret`、`api_passphrase`。

使用 `app_settings` 保存全局 active profile：

- key: `polymarket.active_credential_id`
- value: `{ "credential_id": "..." }`

active profile 是全局的，不按登录用户区分。当前项目本地单用户使用时，这个行为最简单；以后如果接多用户权限，再把 active profile 迁移到用户级设置。

## Funder / Deposit Wallet 获取说明

`funder_address` 应填写 Polymarket Deposit Wallet 地址，通常不是 MetaMask EOA 地址。

前端配置页需要提示用户：

- 连接 MetaMask 后显示的是 signer address，也就是签名钱包地址。
- funder address 是 Polymarket Deposit Wallet / proxy wallet，用于持有资金和 shares。
- active profile 的仓位查询也使用 funder address：`/positions?user=<funder_address>`。
- 获取方式：
  - 打开 Polymarket 网站并连接同一个 MetaMask 钱包。
  - 进入账户、充值或钱包相关页面，找到 Deposit Wallet / Proxy Wallet 地址。
  - 或在 Polymarket 账户资料/API 返回中确认 `proxyWallet`。
- `signature_type` 默认 `3`，对应 Polymarket Deposit Wallet / `POLY_1271` 场景。

## Credentials 生成与导入流程

1. 用户打开系统配置页的“生成/导入 Polymarket 钱包”模块。
2. 前端连接 MetaMask，并要求切换到 Polygon `chainId=137`。
3. 前端调用 Polymarket TS SDK `createOrDeriveApiKey()` 生成 CLOB credentials。
4. 用户确认 profile 信息：
   - `label`
   - `signature_type`，默认 `3`
   - `funder_address`
5. 前端生成 base64 JSON payload 和服务器导入命令：

   ```bash
   cd backend && POLY_CREDENTIAL_PAYLOAD='<base64-json>' uv run python -m app.scripts.import_polymarket_credentials
   ```

6. 用户手动 SSH 到服务器执行命令。
7. 导入脚本校验 `POLYMARKET_CREDENTIALS_ENCRYPTION_KEY`，新增或更新 wallet profile。
8. 导入脚本只输出 profile id、label、masked address、是否 active，不输出明文 secret/passphrase。
9. 前端提供“清空生成结果”按钮；页面刷新后不保留 generated secret。

## Profile 管理接口

新增后端接口：

- `GET /api/polymarket/credentials`
  - 返回 wallet profiles、active id、masked api key、signer、funder、signature type。
- `POST /api/polymarket/credentials/{id}/activate`
  - 设置 active profile。
  - 刷新 account-state。
  - 触发 User WS 用新 credentials 重连。
- `DELETE /api/polymarket/credentials/{id}`
  - 删除非 active profile。
  - active profile 不允许直接删除。

接口不得返回 `api_secret`、`api_passphrase` 明文。错误信息不得包含 secret、passphrase、signed order 原文。

## Runtime Credential Resolution

后端所有 Polymarket 私有账户操作统一通过 runtime credential resolver 获取凭证：

1. 优先读取 DB active profile。
2. 如果没有 active profile，则视为未配置私有账户 credentials。
3. 如果 DB active profile 存在但解密失败，操作失败并给出明确错误，不静默 fallback。
4. 如果没有 active profile：
   - positions、open orders、撤单、User WS、signed order submit 标记为 credentials missing。

使用 active profile 时：

- account-state 的 `wallet` 使用 `funder_address`。
- CLOB L2 header 的 `POLY_ADDRESS` 使用 `signer_address`。
- balance/allowance 的 `signature_type` 使用 profile 的 `signature_type`。
- User WS auth 使用 profile 的 `api_key`、`api_secret`、`api_passphrase`。

## 下单流程

1. 用户在 BTC 看盘页选择 outcome、side、price、size、order type、post only。
2. 前端校验 MetaMask 已连接，且 Polygon `chainId=137`。
3. 前端构造 Polymarket order args：
   - `token_id`
   - `side`
   - `price`
   - `size`
   - `order_type=GTC`
   - `post_only=true`
4. 前端调用 MetaMask 对订单做 EIP-712 签名，得到 `signed_order`。
5. 前端调用后端接口 `POST /api/polymarket/orders/signed`，提交 signed order 和订单元信息。
6. 后端校验 signed order 的 `signer` / `maker` 与 active profile 一致：
   - signer 必须匹配 active `signer_address` 或按 `signature_type=3` 匹配 active `funder_address` 的合约签名规则。
   - maker 必须匹配 active `funder_address`。
   - token_id、side、price、size 必须与前端提交的订单元信息一致。
7. 后端用 active profile 的 CLOB credentials 生成 L2 HMAC headers。
8. 后端把 signed order 提交到 Polymarket CLOB。
9. 提交成功后，后端刷新 open orders 并广播 account-state，前端“当前挂单”更新。

## 撤单与查询

- 撤单不需要 MetaMask 签订单。
- 前端点击“撤”后只发送 `order_id` 给后端。
- 后端使用 active profile 的 CLOB credentials 调用 cancel order。
- 查询 open orders、balance/allowance、User WS 认证也使用 active profile。
- 如果 Python SDK 查询 open orders / cancel 强制要求 signer/private key，应使用后端 REST L2 HMAC 实现，避免重新引入 `POLYMARKET_PRIVATE_KEY`。

## Profile 切换行为

切换 active profile 后：

- account-state 的 `wallet` 切到 active `funder_address`。
- `clob_address` 切到 active `signer_address`。
- positions 重新拉取。
- open orders 重新拉取。
- balance/allowance 重新拉取。
- User WS 断开并用新 credentials 重连。
- BTC 看盘页“我的账户”展示新 wallet 的仓位和挂单。
- 后续撤单和 signed order 提交都使用新的 active profile。

## SG / Close-only 行为

- SG 或 close-only 状态下保留行情、仓位、挂单、撤单。
- 禁用 BUY。
- SELL 只允许 `size <= 当前 token 持仓 shares`。
- 不允许通过买反向 outcome 代替平仓。
- close-only 检查应同时在前端和后端执行。
- 后端校验失败时返回 400，不提交 signed order。

## 前端页面要求

系统配置页新增“Polymarket 钱包 Profiles”模块：

- 显示当前 active profile。
- 列出已导入 profiles：
  - label
  - masked api key
  - signer
  - funder
  - signature type
  - active 状态
- 支持 activate。
- 支持删除非 active profile。
- 提供“生成导入命令”表单：
  - 连接 MetaMask
  - 切换 Polygon
  - 生成 CLOB credentials
  - 输入/确认 label、funder、signature type
  - 生成服务器命令
  - 清空生成结果
- 页面必须明确提示：当前无 HTTPS，不会通过 HTTP 上传 CLOB secret；用户需要手动在服务器执行导入命令。

BTC 看盘页下单模块：

- 使用当前 active profile 的账户状态展示仓位和挂单。
- BUY/SELL 下单每次必须触发 MetaMask 签名确认。
- 下单请求只提交 signed order，不包含 private key、api secret、passphrase。
- 下单成功后刷新 account-state。
- 撤单成功后刷新 account-state。

## 实施顺序

为了降低安全和交易链路的风险，按以下顺序落地：

1. 后端 DB + credentials service + 导入脚本
   - 新增 `polymarket_credentials` 表和迁移。
   - 新增 credentials 加密/解密服务。
   - 新增 `app.scripts.import_polymarket_credentials` 手动导入脚本。
   - 先验证导入失败、加密保存、masked 输出等安全边界。
2. 后端 runtime resolver 接入 account-state/open orders/cancel/User WS
   - 所有私有账户操作统一通过 active profile 获取凭证。
   - positions、open orders、balance/allowance、撤单、User WS auth 都切到 runtime credential resolver。
   - profile 切换后必须重新拉快照并触发 User WS 重连。
3. 后端 profile API 和 signed order API
   - 新增 credentials list/activate/delete API。
   - 新增 `POST /api/polymarket/orders/signed`。
   - signed order 只做临时转发和字段校验，不落库、不打印日志。
4. 前端系统配置页 profile 管理和导入命令生成
   - 支持连接 MetaMask、切 Polygon、生成 CLOB credentials。
   - 只生成用户手动执行的服务器导入命令，不通过 HTTP 上传 secret。
   - 支持 profile 列表、active 切换、删除非 active profile。
5. BTC 看盘页 signed order 下单接入
   - BUY/SELL 下单每次触发 MetaMask EIP-712 签名。
   - 只向后端提交 signed order 和订单元信息。
   - 下单/撤单成功后刷新 account-state，让“当前挂单”及时更新。
6. SG / close-only 前后端保护和完整验证
   - SG 或 close-only 状态禁用 BUY。
   - SELL 不允许超过当前 token 持仓。
   - 前端和后端都做校验，后端校验失败时不提交 signed order。
   - 跑后端测试、前端 build，并手动验证 profile 切换、下单、撤单、User WS。

## 后端实现任务

- 新增 `polymarket_credentials` 表和迁移。
- 新增 credentials 加密/解密服务。
- 新增导入脚本 `app.scripts.import_polymarket_credentials`。
- 新增 runtime credential resolver。
- 改造 positions/open orders/balance/cancel/User WS 使用 active profile。
- 新增 credentials list/activate/delete API。
- 新增 signed order submit API。
- 新增 close-only 后端校验。
- 更新 account-state payload，返回当前 wallet/profile 的 masked 信息。
- 确保日志不会输出 secret/passphrase/signed order。

## 验收标准

- 后端不需要 `POLYMARKET_PRIVATE_KEY` 也能启动。
- DB 中不出现明文 CLOB secret/passphrase/api key。
- 缺失或错误 `POLYMARKET_CREDENTIALS_ENCRYPTION_KEY` 时导入失败，并给出明确错误。
- 前端能生成导入命令，但不会通过 HTTP 上传 credentials。
- 前端刷新后不会保留 generated secret。
- 可以导入多个 wallet profile。
- 前端可以切换 active profile。
- 切换 profile 后，仓位、挂单、User WS、撤单和下单提交都使用新 profile。
- BUY/SELL 下单时 MetaMask 每次弹出订单签名确认。
- 后端收到的是 signed order，不含 private key、API secret、passphrase。
- 下单成功后，当前 market 的 open order 能出现在 BTC 看盘页“当前挂单”。
- 撤单成功后，open order 从“当前挂单”消失。
- SG / close-only 下 BUY 被禁用，SELL 不超过当前 token 持仓。

## Open Questions

- 前端使用哪个 Polymarket TS SDK 包名和版本需要在实现时确认，优先使用官方 SDK。
- signed order 的 V2 payload 格式以当前官方 SDK 输出为准，后端只做字段校验和转发，不自行重签。
- close-only 状态来源需要实现时确认：优先使用 Polymarket 官方 geoblock/交易限制接口；若不可用，先提供手动配置开关作为保护。
