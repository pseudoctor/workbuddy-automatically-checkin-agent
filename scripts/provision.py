#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""provision.py — WorkBuddy 签到技能一键开通脚本（编排器）

把「OAuth 设备授权 → 长效令牌落盘 → 首次签到验证」整条流程串起来。
直接运行 `python3 provision.py`，按提示操作即可。

前置条件:
  - python3（标准库即可，无第三方依赖）
  - curl（仅调用 OAuth 时需要；若用 --rt 直接录入令牌则不需要）
  - 本技能自带 scripts/checkin.py（仅签到执行器，不依赖外部 skill）

用法:
  python3 provision.py                 # 全流程：签发链接 → 等你登录 → 轮询 → 落盘 → 首次签到
  python3 provision.py --rt '<令牌>'   # 已有长效令牌：跳过 OAuth 直接落盘+验证
                                       # 注意：--rt 会把令牌放进命令行参数，优先用 OAuth 流程
  python3 provision.py --url-only      # 只签发授权链接并打印（供集成到其他 Agent 流程）

安全约定:
  - 令牌仅保存到 ~/.workbuddy/checkin-rt.json（默认账号）或
    ~/.workbuddy/checkin-rt-<账号名>.json（--account 指定时），POSIX 0600
  - 本脚本所有输出均不含令牌明文；OAuth 流程中令牌经 stdin 交给 checkin.py，不走 argv
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)

# checkin.py 就在本技能 scripts/ 目录，不再查找外部执行器
CHECKIN_CANDIDATES = [
    os.path.join(HERE, "checkin.py"),
    os.path.join(os.getcwd(), "checkin.py"),
]

OAUTH_SCRIPT = os.path.join(HERE, "workbuddy_oauth.sh")


def find_checkin():
    for p in CHECKIN_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def run(cmd, timeout=120, input_text=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          input=input_text)


def step_oauth_url():
    print("=" * 60)
    print("  第 1 步：签发 OAuth 设备授权链接")
    print("=" * 60)
    r = run(["bash", OAUTH_SCRIPT, "url"], timeout=40)
    url = (r.stdout or "").strip()
    if r.returncode != 0 or not url.startswith("http"):
        print("签发失败：", (r.stderr or r.stdout or "").strip()[:300], file=sys.stderr)
        sys.exit(1)
    print()
    print("请在浏览器中打开以下链接，登录你的 CodeBuddy / WorkBuddy 账号：")
    print()
    print("  " + url)
    print()
    print("（链接有效期约 10 分钟；如过期请重新运行本脚本）")


def step_wait_and_poll():
    print()
    input("  完成浏览器登录后，回到这里按回车继续 > ")
    print("正在收取凭证…（最多等待 3 分钟）")
    deadline = time.time() + 180
    while time.time() < deadline:
        r = run(["bash", OAUTH_SCRIPT, "poll"], timeout=40)
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out.startswith("{"):
            data = json.loads(out)
            if data.get("refreshToken"):
                print("✓ 凭证收取成功（refreshToken 长度 %d，有效期 %s 秒）"
                      % (len(data["refreshToken"]), data.get("expiresIn")))
                return data
        time.sleep(15)
    print("✗ 未收到授权。常见原因：", file=sys.stderr)
    print("  1) 浏览器登录未真正完成（回到登录页确认显示已登录）", file=sys.stderr)
    print("  2) 授权链接已过期 → 重新运行本脚本", file=sys.stderr)
    sys.exit(1)


ACCOUNT = None


def step_save_rt(refresh_token):
    print()
    print("=" * 60)
    print("  第 2 步：长效令牌落盘（自续期）")
    print("=" * 60)
    checker = find_checkin()
    if not checker:
        print("✗ 未找到本技能的 checkin.py。", file=sys.stderr)
        sys.exit(1)
    # 令牌经 stdin 传递，不放进命令行参数
    setup_cmd = ["python3", checker, "--setup-rt", "-"]
    if ACCOUNT:
        setup_cmd += ["--account", ACCOUNT]
    r = run(setup_cmd, timeout=60,
            input_text=refresh_token)
    out = (r.stdout or "").strip()
    if r.returncode == 0 and '"status": "ok"' in out.replace("'", '"'):
        try:
            info = json.loads(out)
            print("✓ 已保存到 %s" % info.get("file", "~/.workbuddy/checkin-rt.json"))
            return
        except Exception:
            pass
    print("落盘输出：", out[:300])
    if r.returncode != 0:
        print((r.stderr or "")[:300], file=sys.stderr)
        sys.exit(1)


def step_first_checkin():
    print()
    print("=" * 60)
    print("  第 3 步：首次签到验证")
    print("=" * 60)
    checker = find_checkin()
    if not checker:
        print("✗ 未找到本技能的 checkin.py。", file=sys.stderr)
        return
    checkin_cmd = ["python3", checker, "--checkin"]
    if ACCOUNT:
        checkin_cmd += ["--account", ACCOUNT]
    r = run(checkin_cmd, timeout=120)
    out = (r.stdout or "").strip()
    try:
        data = json.loads(out)
    except Exception:
        print("验证输出（截断）：", out[:500])
        r2 = r.stderr or ""
        if r2:
            print(r2[:300], file=sys.stderr)
        return
    resp = data.get("checkin_resp") or {}
    print("签到: %s | 本次积分: %s | 连签: %s 天"
          % ("成功" if data.get("checkin_http") == 200 and resp.get("code") == 0
             else "见原始输出",
             data.get("credit"), data.get("streak_days")))


def main():
    ap = argparse.ArgumentParser(description="WorkBuddy 签到技能一键开通")
    ap.add_argument("--rt", help="已有长效令牌（refresh token），跳过 OAuth 直接落盘")
    ap.add_argument("--url-only", action="store_true", help="仅签发授权链接后退出")
    ap.add_argument("--account", default=None, help="账号名（默认 default，即旧账号）")
    args = ap.parse_args()
    global ACCOUNT
    ACCOUNT = args.account

    if args.rt:
        step_save_rt(args.rt.strip())
        step_first_checkin()
        return
    step_oauth_url()
    if args.url_only:
        return
    data = step_wait_and_poll()
    step_save_rt(data["refreshToken"])
    step_first_checkin()
    print()
    print("全部完成！之后可运行 checkin.py --checkin 随时签到，")
    print("定时任务（每天 09:00）由调用方 agent 另行配置，本技能不自建定时。")


if __name__ == "__main__":
    main()
