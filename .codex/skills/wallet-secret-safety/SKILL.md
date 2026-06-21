---
name: wallet-secret-safety
description: Use when working on wallet, MetaMask, Polymarket trading, CLOB credentials, order signing, signed orders, private keys, seed phrases, API secrets, transaction flows, or any feature that touches wallet security. Enforce the project's highest-priority rule: private keys and seed phrases must never pass through network, backend APIs, logs, storage, env files, browser storage, screenshots, or automated copy flows.
---

# Wallet Secret Safety

本项目中，wallet 安全是最高优先级约束。涉及 MetaMask、Polymarket、CLOB、下单、撤单、订单签名、credentials、wallet 配置时，必须先检查是否触碰以下规则。

## Hard Rules

- `seed phrase` 和 `private key` 绝对不能通过网络传输。
- `seed phrase` 和 `private key` 绝对不能进入前端页面、后端接口、日志、数据库、`.env`、浏览器存储、截图、自动剪贴板流程。
- 不允许实现任何要求用户输入、上传、粘贴、复制、保存 private key / seed phrase 的功能。
- 私钥签名必须发生在 MetaMask 或用户明确控制的钱包/硬件钱包内部。
- 前端只能请求 MetaMask 获取地址、切链、签名；不能读取私钥或助记词。
- 如果某个方案需要 private key / seed phrase 经过网络、后端、日志、浏览器存储或配置文件，必须停止并提醒用户该方案不可接受。

## CLOB Credentials Rules

- `POLYMARKET_CLOB_SECRET` 和 `POLYMARKET_CLOB_PASSPHRASE` 也是敏感信息。
- 当前没有 HTTPS 时，前端不得通过 HTTP 自动提交 CLOB credentials 到后端。
- 可以在前端用 MetaMask 生成 CLOB credentials，但只能短暂展示给用户，由用户手动复制到服务器 `.env` 或手动执行生成的服务器命令。
- 不要把 CLOB credentials 写入 localStorage、IndexedDB、console、日志、错误上报、数据库或 git。
- 生成 credentials 的页面必须提供清空 secret 的操作。
- 如需简化配置，优先生成“用户手动执行”的命令，不要新增 HTTP 写入 secret 的接口。

## Signed Order Rules

- signed order 可以通过 HTTP 临时提交给后端，但要视为敏感临时数据。
- signed order 只能代表用户已经签过的那一笔订单，不能被篡改成其他订单。
- 签名前 UI 必须清楚展示 side、outcome/token、price、size、order type、post-only。
- signed order 不落库、不缓存、不打印日志，提交后即丢弃。
- 不要在异常信息中返回完整 signed order。

## Required Reminder Points

开发过程中一旦发现以下场景，必须明确提醒用户：

- 需要在网页输入 private key / seed phrase。
- 需要把 private key / seed phrase 放服务器 `.env`。
- 需要通过 API 传 private key / seed phrase。
- 需要把 CLOB credentials 通过 HTTP 自动传后端。
- 第三方 SDK 要求把 secret 暴露到前端运行时。
- 日志、错误上报、调试输出可能包含 private key、seed phrase、CLOB secret、passphrase 或 signed order。
- 需要浏览器持久化保存 wallet secret 或 CLOB secret。

## Preferred Architecture

- MetaMask 持有私钥并负责签名。
- 服务器不保存 `POLYMARKET_PRIVATE_KEY`。
- CLOB credentials 在无 HTTPS 环境下由用户手动配置到服务器 `.env`。
- BUY/SELL 订单由前端请求 MetaMask 签 signed order，后端只负责提交 signed order。
- open orders、cancel、User WS 由后端使用 CLOB credentials 处理。
- 长期生产部署应使用 HTTPS；HTTP 只能作为受限本地/内网过渡方案。
