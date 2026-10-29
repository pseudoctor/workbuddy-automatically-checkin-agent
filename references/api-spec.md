# WorkBuddy OAuth 设备授权 + 签到 API 完整参考

> 本文档是 `totorosir-workbuddy-checkin` 签到流程的技术参考，供 Agent 在排障、
> 移植到其他平台或编写自动化时查阅。所有端点均逆向自开源项目
> [workbuddy2api-panel](https://github.com/linguo2625469/workbuddy2api-panel)（MIT 协议）
> 与 totorosir-workbuddy-checkin 技能，实测时间 2026-10-27。

## 目录

1. [域与端点总览](#1-域与端点总览)
2. [OAuth 设备授权流程](#2-oauth-设备授权流程)
3. [常见错误码](#3-常见错误码)
4. [长效令牌（refresh token）通道](#4-长效令牌refresh-token通道)
5. [签到与积分查询端点](#5-签到与积分查询端点)
6. [踩坑实录（重要）](#6-踩坑实录重要)

---

## 1. 域与端点总览

| 域名 | 用途 |
|---|---|
| `copilot.tencent.com` | 插件网关：OAuth 签发/轮询/刷新、聊天补全（SSE） |
| `www.codebuddy.cn` | 业务站：签到、积分、成长任务（`/v2/*`）；OAuth 登录页的 Origin |
| `www.workbuddy.cn` | WorkBuddy 客户端业务域（旅行等，以登录态 `domain` 字段为准） |
| `www.workbuddy.ai` | 国际版（global realm）上游，国内版不用 |

关键区别：**签到接口不要打 `copilot.tencent.com`（会 404）**；令牌刷新走
`copilot.tencent.com/v2/plugin/auth/token/refresh`。

## 2. OAuth 设备授权流程

无 PKCE，state 由服务端签发。全程请求头必须带：

```
Content-Type: application/json
Accept: application/json, text/plain, */*
X-Requested-With: XMLHttpRequest
Origin: https://www.codebuddy.cn
Referer: https://www.codebuddy.cn/
User-Agent: CLI/2.63.2 CodeBuddy/2.63.2
```

### 2.1 签发授权链接

```
POST https://copilot.tencent.com/v2/plugin/auth/state?platform=CLI
Body: {}
```

成功响应（信封 `{code, msg, data}`，`code=0` 为成功）：

```json
{
  "code": 0, "msg": "OK",
  "data": {
    "state": "de8682b4-fc85-4523-b148-42d8a019ccba",
    "authUrl": "https://copilot.tencent.com/login?platform=CLI&state=de8682b4-..."
  }
}
```

- `authUrl` 发给用户，在浏览器打开并登录 CodeBuddy/WorkBuddy 账号
- state 有效期实测约 **10 分钟**；过老 state 一直返回 11217
- 响应会种 Cookie（如 `tgw_l7_route`）——**轮询时必须带上同一份 Cookie**

### 2.2 轮询凭证

```
GET https://copilot.tencent.com/v2/plugin/auth/token?state=<state>
```

- 未完成登录：`{"code":11217,"msg":"login ing..."}`
- 完成登录：`code=0`，`data` 含：

```json
{
  "accessToken": "<JWT，约 1.5KB>",
  "refreshToken": "<JWT，约 1.5KB>",
  "expiresIn": 2592000,
  "domain": "www.codebuddy.cn"
}
```

- `expiresIn`：access token 有效期秒数（实测 30 天）
- 轮询间隔建议 15–20 秒；登录完成后 state 即刻可取，无需重试间隔
- **state 一次性**：成功后该 state 作废

### 2.3 账号信息（可选）

```
GET https://copilot.tencent.com/v2/plugin/login/account?state=<state>
Authorization: Bearer <accessToken>
```

返回 `uid` / `enterpriseId` / `nickname`。轮询取到 token 后应立刻调用（state 仍在有效期时）。

## 3. 常见错误码

| code | 含义 | 处置 |
|---|---|---|
| 0 | 成功 | — |
| 11217 | `login ing...` 未完成登录 | 继续轮询；若超 10 分钟重新签发 state |
| 11218 | state 过期/无效（实测偶发） | 重新 `POST /plugin/auth/state` |
| 401 | accessToken 过期 | 用 refreshToken 刷新（见 §4） |

## 4. 长效令牌（refresh token）通道

技能「通道 C」的核心：refresh token 长期有效，每次签到先用它换新 access token，
再把服务端轮换出的新 refreshToken 回写本地，实现自续期。

```
POST https://copilot.tencent.com/v2/plugin/auth/token/refresh
Headers:
  Content-Type: application/json
  X-Refresh-Token: <refreshToken>
  X-Auth-Refresh-Source: plugin
  X-Domain: copilot.tencent.com
Body: {}
```

响应 `data` 含新的 `accessToken` / `refreshToken`（**旧 refreshToken 会轮换失效，
必须回写**，否则下次刷新失败）。checkin.py 已自动处理回写。

## 5. 签到与积分查询端点

以刷新后的 accessToken 作为 `Authorization: Bearer` 调用，业务域为 `www.codebuddy.cn`：

| 端点 | 方法 | 说明 |
|---|---|---|
| `/v2/billing/meter/daily-checkin` | POST | 每日签到（幂等，已签到返回业务错误但不扣分） |
| `/v2/billing/meter/get-user-resource` | POST | 积分余额查询（见下；`checkin.py` 的 `query_balance`） |
| 成长计划/积分/旅行等 | — | 以 checkin.py 实现为准（`internal` 端点随版本更新） |

签到成功响应示例：

```json
{"code":0,"msg":"OK","data":{"credit":100,"streak_days":2,"is_streak_day":false}}
```

余额查询（2026-10-29 实测，口径同开源 panel 的 `cmd/credit`）：

```
POST https://www.codebuddy.cn/v2/billing/meter/get-user-resource
Headers: Authorization: Bearer <accessToken>、X-Domain: www.codebuddy.cn
Body: {"PageNumber":1,"PageSize":100,"ProductCode":"p_tcaca","Status":[0,3],
       "PackageEndTimeRangeBegin":"<当日-1天>","PackageEndTimeRangeEnd":"<当日+101年>"}
```

响应 `data.Response.Data` 含 `TotalDosage` 与 `Accounts[]`（每包
`CapacityRemain/Used/Size` 与 `CycleCapacityRemain/Used/Size`）。
聚合规则：每包优先用 Cycle* 字段，Cycle 为空用 Capacity*；总额度取
各包额度之和与 `TotalDosage` 的较大值。2026-10-29 实测：总额度 2200、
已用 0、剩余 2200、4 个包（1500/500/100/100）。

注意：`checkin.py` 的 `post_json` 曾用 curl 配置文件单引号 `data` 传
body，带空格的 JSON 体会报 `invalid character '\''`；已改为 body 写
0600 临时文件、用 `data-binary @文件` 发送，余额查询即用此方式。

多账号（2026-10-29，客户端循环）：服务端没有多账号批量接口，
多账号就是对每个账号的 refreshToken 各自刷新、各签各的。
`checkin.py --checkin-all` 按 `default`（旧文件 `checkin-rt.json`）
在前、命名账号（`checkin-rt-<账号名>.json`）按文件名排序的顺序逐个
执行；每个账号的轮换回写只写它自己的文件，绝不共用文件。

## 6. 踩坑实录（重要）

以下是 2026-10-27 实机跑通全流程时遇到的真实坑，Agent 排障时优先对照：

1. **「授权了但一直 login ing」**：大多是 state 过期（>10 分钟）或轮询请求
   **没带签发时的 Cookie**。官方 CLI（login 工具）在 url 和 poll 两步共享同一
   `http.Client`（CookieJar），复现时务必 `-b/-c` 同一个 cookie jar。
2. **轮询不要开太多并发**：单 state 串行轮询即可，间隔 15–20s，过量请求无益。
3. **签到域 名**：`/v2/billing/meter/daily-checkin` 打 `www.codebuddy.cn`；
   打 `copilot.tencent.com` 会 404。
4. **refreshToken 会轮换**：每次刷新后服务端签发新的 refreshToken，checkin.py
   自动回写 `~/.workbuddy/checkin-rt.json`；如果自己写脚本，漏掉回写 = 下次失效。
5. **文件权限**：凭据文件应为 POSIX `0600`；泄漏 = 账号钥匙泄漏，撤销方式是
   重新登录客户端让会话刷新。
6. **派猫猫旅行需要账号有活跃 Buddy**：新账号/未开启 Buddy 时返回
   `no active buddy`，属正常现象，不影响签到。
7. **合规边界**：本流程仅供本人授权账号、私有环境自动化；不得商用/售卖账号池。
   参考 workbuddy2api-panel README「安全与合规」章节。
