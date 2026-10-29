# workbuddy-automatically-checkin-agent

WorkBuddy / CodeBuddy「Buddy 加油站」自动签到 skill：OAuth 设备授权获取账号凭证 → 长效令牌落盘（自续期）→ 首次签到验证 → 每天 9 点左右定时签到。

- 自带最小执行器 `scripts/checkin.py`（仅签到，Python 标准库），不依赖任何外部执行器 skill
- 每日播报固定五项：本次签到获得积分、积分总额度、已用积分、剩余积分、连签天数
- 支持多账号：每个账号各一份凭证，各自刷新、各自签到，互不干扰
- 签到是幂等的：当天已领会返回 `already_checked_in`，不会重复领

本仓库是给 agent 安装用的，不含任何账号凭证。下载本 skill 不等于获得任何人的账号，接收方必须用自己的账号单独走一次 OAuth 授权。

## 版本

- skill 名：`workbuddy-automatically-checkin-agent`
- 打包时间：2026-10-29
- 压缩包：`workbuddy-automatically-checkin-agent.zip`，sha256：`f070c3c6191b46e5f3626fd1c66a935482256d5bc7e3416e7e27cbdd58e47deb`
- 以后 skill 再改，请同步更新源码和压缩包，并更新上面这行 sha，否则对方 agent 可能拿到旧版。

## 安装（给 agent）

方式 A：克隆仓库（把下面地址换成实际仓库地址）：

```bash
git clone <仓库地址>
cd workbuddy-automatically-checkin-agent
```

方式 B：只下载压缩包（把下面地址换成实际仓库地址，或换成 Release 附件地址）：

```bash
curl -L -o workbuddy-automatically-checkin-agent.zip <压缩包地址>
unzip workbuddy-automatically-checkin-agent.zip
cd workbuddy-automatically-checkin-agent
```

前置检查：

```bash
python3 --version # 需要 python3
curl --version # OAuth 步骤需要 curl
python3 scripts/checkin.py --status # 无凭证时应返回 no_credential，而不是找不到文件
```

## 首次开通（一个账号）

向用户索要授权是唯一入口，绝不代输账号密码。两种方式二选一：

方式 A（推荐）：

```bash
bash scripts/workbuddy_oauth.sh url # 输出授权链接
# 用户在浏览器打开链接并登录（约 10 分钟内有效），确认“登录完成”后：
python3 scripts/poll_and_save.py # 轮询 → 经 stdin 落盘 → 首次签到，全程不打印令牌
```

方式 B（一键交互）：`python3 scripts/provision.py`，按提示回车即可走完全流程。

注意：不要用 `workbuddy_oauth.sh watch/poll` 走完全流程，它会把含令牌的 JSON 打到标准输出，只在排障且确认输出不进日志时才用。

凭证只落在接收方自己的 `~/.workbuddy/checkin-rt.json`（0600），令牌走 stdin，不放进命令行参数；刷新失败时旧令牌不被覆盖。

验证：

```bash
python3 scripts/checkin.py --checkin
```

`checkin_http==200` 且 `checkin_resp.code==0` 为签到成功；`code==10001` 为当天已领（幂等，不算失败）。

## 多账号

已有账号不用重登，它叫 `default`，凭证仍是 `~/.workbuddy/checkin-rt.json`。

加一个账号（一次只做一个，OAuth 的 state/cookie 是单份文件，不能两个授权并行；账号名可以是中文名，只要不含路径分隔符或控制字符、长度不超过 64）：

```bash
bash scripts/workbuddy_oauth.sh url # 浏览器用登录，确认“登录完成”
python3 scripts/poll_and_save.py --account <账号名>
python3 scripts/checkin.py --checkin --account <账号名>
python3 scripts/checkin.py --list-accounts
```

绝不能把两个账号的令牌写进同一个文件，轮换会互相覆盖导致其中一个失效。新账号凭证在 `~/.workbuddy/checkin-rt-<账号名>.json`（0600）。

删一个账号：删掉它对应的 `checkin-rt-<账号名>.json` 文件即可停止该账号的自动签到（只是本地停用）。

## 每日定时（接收方 agent 在自己的环境里创建，本 skill 本身不创建定时）

推荐每天 9 点左右（Asia/Shanghai，不用精确到 09:00），定时入口是本 skill 的 `scripts/daily_checkin.sh`：触发后先随机等 0–30 分钟再跑 `checkin.py --checkin-all`（多账号时逐个签到），实际签到在 09:00–09:30 之间浮动，输出追加到 `~/.workbuddy/checkin.log`。

汇报规则（按账号分开）：

- 签到成功：汇报五项——本次签到获得积分、积分总额度、已用积分、剩余积分、连签天数；余额查询失败时如实写“余额查询失败”，不编造数字
- 已领（`already_checked_in`）：对该账号静默，不影响其他账号
- 失败（`refresh_failed` / `no_credential` / `checkin_http_error` / `doh_failed`）：汇报该账号的失败原因和下一步（令牌失效时重新走 OAuth），不把令牌写进汇报或日志，不重复重试

## 给其他 agent 的指令（可直接复制）

> 请从本仓库安装 workbuddy-automatically-checkin-agent：先确认 python3 和 curl 可用，跑 `scripts/checkin.py --status`；然后用 `scripts/workbuddy_oauth.sh url` 签发授权链接发给用户，用户在浏览器登录并确认“登录完成”后，用 `scripts/poll_and_save.py` 轮询落盘并首次签到（全程不打印令牌）；再跑 `scripts/checkin.py --checkin` 验证；最后在你自己的环境里建每天 09:00 Asia/Shanghai 的定时，入口是 `scripts/daily_checkin.sh`，成功时按 README 的五项格式汇报，已领静默，失败汇报并提示重新授权。不要安装其他执行器，不要用桌面 UI 点按路线，不要把令牌写进聊天或日志。

## 安全与合规

- 本仓库不含任何账号凭证、令牌、日志、state/cookie 文件，也没有个人信息；不要把 `~/.workbuddy/` 下的文件、签到日志或任何令牌提交进仓库（见 `.gitignore`）。
- 本流程仅供本人授权账号、私有环境自动化；不得商用、不得批量注册小号、不得售卖账号池或额度。
- 所用接口为非官方逆向接口（源自开源项目 workbuddy2api-panel，MIT 协议）：授权页是 CodeBuddy（`copilot.tencent.com`），签到与余额走 `www.codebuddy.cn`；上游随时可能变更导致不可用，届时以实际接口为准。
- 详细端点、踩坑与聚合口径见 `references/api-spec.md`，完整开通流程见 `SKILL.md`。
