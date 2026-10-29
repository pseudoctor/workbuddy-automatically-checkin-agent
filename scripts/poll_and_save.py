#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Secure OAuth poll + save + first checkin (Muse improvement, 2026-10-28).

Unlike workbuddy_oauth.sh poll, this script NEVER prints a token:
it captures the poll JSON internally, passes refreshToken to checkin.py
via stdin, deletes state/cookie on success, then runs the first checkin
and prints only the checkin result (credit / streak_days).

Exit codes: 0 = checkin attempted (see JSON), 2 = login not finished /
state missing / poll failed, 3 = credential save failed.

Multi-account: pass --account <label> to save/check in that account;
omit it for the legacy default account. Authorize accounts one at a time:
the OAuth state/cookie files are singletons, so never run two authorizations
concurrently.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WB = os.path.expanduser("~/.workbuddy")
STATE_FILE = os.path.join(WB, ".wb_oauth_state.json")
JAR_FILE = os.path.join(WB, ".wb_oauth_cookies.txt")
CHECKIN = os.path.join(HERE, "checkin.py")


def doh_ip(host):
    try:
        import ssl
        import urllib.request
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(
            "https://1.1.1.1/dns-query?name=" + host + "&type=A",
            headers={"accept": "application/dns-json"})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode() or "{}")
        ans = [a["data"] for a in data.get("Answer", []) if a.get("type") == 1]
        return ans[0] if ans else None
    except Exception:
        return None


def main():
    import argparse
    ap = argparse.ArgumentParser(description="WorkBuddy OAuth 安全轮询 + 落盘 + 首次签到")
    ap.add_argument("--account", default=None, help="账号名（默认 default，即旧账号）")
    account = ap.parse_args().account
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)["state"]
    except Exception:
        print(json.dumps({"status": "no_state",
                          "hint": "请先执行 workbuddy_oauth.sh url 重新签发"},
                         ensure_ascii=False))
        return 2
    ip = doh_ip("copilot.tencent.com")
    resolve = ["--resolve", "copilot.tencent.com:443:" + ip] if ip else []
    r = subprocess.run(
        ["curl", "-s", "--connect-timeout", "10", "--max-time", "20"] + resolve +
        ["-b", JAR_FILE, "-c", JAR_FILE,
         "https://copilot.tencent.com/v2/plugin/auth/token?state=" + state,
         "-H", "Accept: application/json, text/plain, */*",
         "-H", "X-Requested-With: XMLHttpRequest",
         "-H", "Origin: https://www.codebuddy.cn",
         "-H", "Referer: https://www.codebuddy.cn/",
         "-H", "User-Agent: CLI/2.63.2 CodeBuddy/2.63.2"],
        capture_output=True, text=True, timeout=40)
    try:
        data = json.loads(r.stdout or "{}")
    except Exception:
        print(json.dumps({"status": "poll_parse_failed"}, ensure_ascii=False))
        return 2
    if data.get("code") != 0:
        print(json.dumps({"status": "login_not_finished",
                          "code": data.get("code")}, ensure_ascii=False))
        return 2
    refresh = (data.get("data") or {}).get("refreshToken") or ""
    if refresh.count(".") != 2:
        print(json.dumps({"status": "bad_token_format"}, ensure_ascii=False))
        return 3
    setup_cmd = ["python3", CHECKIN, "--setup-rt", "-"]
    if account:
        setup_cmd += ["--account", account]
    s = subprocess.run(setup_cmd,
                       input=refresh, capture_output=True, text=True, timeout=60)
    if s.returncode != 0:
        print(json.dumps({"status": "save_failed",
                          "detail": (s.stderr or "")[:200]}, ensure_ascii=False))
        return 3
    # Token is now safely on disk; remove one-time state/cookie.
    for p in (STATE_FILE, JAR_FILE):
        try:
            os.remove(p)
        except OSError:
            pass
    checkin_cmd = ["python3", CHECKIN, "--checkin"]
    if account:
        checkin_cmd += ["--account", account]
    c = subprocess.run(checkin_cmd,
                       capture_output=True, text=True, timeout=120)
    out = (c.stdout or "").strip()
    try:
        result = json.loads(out)
    except Exception:
        print(json.dumps({"status": "checkin_output_unparsed",
                          "raw": out[:300]}, ensure_ascii=False))
        return 0
    result["credential_saved"] = True
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
