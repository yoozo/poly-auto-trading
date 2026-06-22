# Step 4 Polymarket 前端直连交易提交方案

## Summary

- 记录新增需求：将 Polymarket 下单提交动作从“后端提交”改为“用户浏览器所在网络提交”，以规避服务器所在地区可能不支持交易的问题。
- 当前架构仍保持 MetaMask 负责订单签名；本需求重点评估和改造 CLOB `/order` 提交位置。
- 不覆盖已有 `step1-3`。

## 背景

- 当前下单链路是：前端用 MetaMask 生成 signed order，后端使用 CLOB API credentials 调 Polymarket `/order`。
- 用户担心服务器所在地区或网络环境无法下单，希望订单提交发生在自己的浏览器网络环境。
- Polymarket `/order` 不只需要 signed order，还需要 CLOB L2 auth headers，因此前端直连意味着浏览器运行时需要持有或派生 CLOB credentials。

## Requirements / Decisions

- 目标能力：支持由前端直接向 Polymarket 提交 signed order。
- 私钥仍必须只保留在 MetaMask 内，前端和后端都不能读取、保存或传输 private key / seed phrase。
- CLOB credentials 如进入前端，只能保存在内存态，不写 localStorage、IndexedDB、URL、日志、错误上报或后端接口。
- 页面刷新后前端 CLOB credentials 丢失，需要重新通过 MetaMask 派生或恢复。
- 如果 CORS 不支持浏览器直连 Polymarket 私有接口，需要切换到“本机 trading proxy”方案。
- 后端不得再作为远端服务器地区下单通道，但可继续承担行情、market 列表、仓位快照、User WS 等非下单能力，具体范围需按后续验证决定。

## Implementation Options

- 方案 A：前端仅提交订单
  - 前端签名并直接 POST Polymarket `/order`。
  - 后端仍负责余额、挂单、仓位、User WS。
  - 风险：下单成功后后端状态刷新仍可能受服务器网络/地区影响，页面可能短暂不同步。
- 方案 B：完整浏览器交易模式
  - 下单、撤单、open orders、balance 都由前端直连 Polymarket。
  - 后端只保留行情和非敏感聚合服务。
  - 风险：浏览器暴露 CLOB credentials 的面更大，且强依赖 Polymarket CORS。
- 方案 C：本机 trading proxy
  - 用户本机运行轻量代理，由本机网络提交订单、撤单、查询私有 CLOB 接口。
  - 浏览器和本机代理通信，远端服务器不接触下单流量。
  - 推荐作为安全性和可用性的平衡方案，尤其适合“服务器地区不支持，但用户本地网络支持”的场景。

## Implementation Phases

- Phase 1：验证可行性
  - 在本地浏览器验证 Polymarket CLOB `/order`、cancel、balance、orders 是否允许 CORS。
  - 验证 TS SDK 是否能在浏览器端生成 L2 auth headers 并提交订单。
  - 记录失败原因：CORS、认证、region、SDK browser runtime、preflight。
- Phase 2：最小改造
  - 增加交易提交模式配置：`backend` / `browser` / `local_proxy`。
  - BTC 下单区根据模式决定：继续调用后端 `/api/polymarket/orders/signed`，或前端直接提交 Polymarket。
  - 前端直连模式下，CLOB credentials 只保存在 React 内存态。
- Phase 3：状态同步优化
  - 前端直连下单成功后立即本地 optimistic pending。
  - 后端 account-state 刷新失败时不覆盖前端刚提交的 pending/order 状态。
  - 统一处理下单成功但挂单/余额延迟同步的提示。
- Phase 4：安全加固
  - 禁止把 CLOB secret/passphrase 写入任何持久化存储。
  - 清理 console、错误提示、network payload 中的敏感字段。
  - 页面刷新、登出、切换钱包时清空内存 credentials。
  - 生产部署建议 HTTPS；HTTP 仅作为本地开发/内网过渡。

## Acceptance Criteria

- 服务器不参与 Polymarket `/order` 提交时，用户仍能在自己网络环境完成下单。
- MetaMask 每次 BUY/SELL 仍弹出订单签名确认。
- 前端不接触 private key / seed phrase。
- CLOB secret/passphrase 不落库、不进 localStorage、不上传后端、不打印日志。
- 如果浏览器直连因 CORS 不可行，系统能明确提示并切换到本机 trading proxy 方案。
- 下单成功后，BTC 看盘页能尽快显示 pending/open order，不被后端延迟快照闪烁覆盖。

## Open Questions

- Polymarket CLOB 私有接口当前是否允许浏览器 CORS 直连，需要实测确认。
- 是否优先实现“前端仅提交订单”，还是直接做“本机 trading proxy”。
- 撤单是否也要从服务器迁移到浏览器/本机网络环境。
- User WS 和 open orders 是否继续走后端，还是跟随交易提交一起迁移。
