#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal WorkBuddy daily-checkin executor (Muse adaptation, 2026-10-28).

Replaces the external totorosir-workbuddy-checkin rt_auth.py dependency for
the checkin-only path, so the refreshToken is never handed to an unaudited
third-party script. Standard library only, no token is ever printed.

Usage:
  python3 checkin.py --setup-rt -        # read refreshToken from stdin (not argv, avoids ps exposure)
  python3 checkin.py --setup-rt <token>  # legacy argv form, still accepted
  python3 checkin.py --checkin           # refresh -> checkin -> report (no tokens in output)
  python3 checkin.py --status            # only report whether a credential file exists
  python3 checkin.py                     # same as --checkin
  python3 checkin.py --checkin --account <label>   # one named account
  python3 checkin.py --checkin-all       # every saved account, one by one
  python3 checkin.py --list-accounts     # labels of saved accounts

Multi-account (2026-10-29): each account has its own credential file, so
rotation can never overwrite another account's token. The account that
existed before multi-account support keeps the legacy file and is called
"default": ~/.workbuddy/checkin-rt.json (0600, atomic replace).
A named account lives in ~/.workbuddy/checkin-rt-<label>.json (0600).
Labels may be any name (including Chinese, e.g. 生生之谓易) except a path
separator, control character, ".", ".." or a name over 64 characters.
签到成功或已领时，输出还会附带余额查询：balance_total（总额度）、
balance_used（已用）、balance_remain（剩余）、balance_packages（套餐数）。
Refresh failure does NOT overwrite the old token: rotation only happens on
a successful refresh response, matching the api-spec rotation rule.
"""
import json
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

WB_DIR = os.path.expanduser("~/.workbuddy")
TOKEN_FILE = os.path.join(WB_DIR, "checkin-rt.json")  # legacy default account
DEFAULT_ACCOUNT = "default"
def _valid_label(label):
    if not label or len(label) > 64 or label in (".", ".."):
        return False
    return not any(c in label for c in ("/", "\\", "\x00")) and \
        not any(ord(c) < 32 for c in label)


def token_file_for(account):
    """Return (label, path) for an account. None/''/'default' = legacy file."""
    label = (account or "").strip() or DEFAULT_ACCOUNT
    if label == DEFAULT_ACCOUNT:
        return label, TOKEN_FILE
    if not _valid_label(label):
        raise ValueError("账号名不能含路径分隔符或控制字符，长度 1–64")
    return label, os.path.join(WB_DIR, "checkin-rt-%s.json" % label)


def list_accounts():
    """Saved accounts in stable order: default first, then named, sorted."""
    out = []
    if os.path.isfile(TOKEN_FILE):
        out.append((DEFAULT_ACCOUNT, TOKEN_FILE))
    import glob
    for path in sorted(glob.glob(os.path.join(WB_DIR, "checkin-rt-*.json"))):
        label = os.path.basename(path)[len("checkin-rt-"):-len(".json")]
        if _valid_label(label) and label != DEFAULT_ACCOUNT:
            out.append((label, path))
    return out
REFRESH_URL = "https://copilot.tencent.com/v2/plugin/auth/token/refresh"
CHECKIN_URL = "https://www.codebuddy.cn/v2/billing/meter/daily-checkin"
BALANCE_URL = "https://www.codebuddy.cn/v2/billing/meter/get-user-resource"
UA = "CLI/2.63.2 CodeBuddy/2.63.2"
TIMEOUT = 25


_DOH_CACHE = {}


def resolve_via_doh(host):
    """Muse 改进 (2026-10-28)：本 VM 本地解析器是 sinkhole，直连腾讯会超时。
    用 DoH (1.1.1.1) 解析真实 IP；到腾讯的 TLS 仍按原域名校验证书，DoH 返回
    错误 IP 时证书校验会失败，不会静默接受。失败时返回 None，走系统解析。"""
    if host in _DOH_CACHE:
        return _DOH_CACHE[host]
    ip = None
    for _ in range(3):
        try:
            ctx = ssl._create_unverified_context()
            req = urllib.request.Request(
                "https://1.1.1.1/dns-query?name=" + host + "&type=A",
                headers={"accept": "application/dns-json"})
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                data = json.loads(resp.read().decode() or "{}")
            answers = [a["data"] for a in data.get("Answer", [])
                       if a.get("type") == 1]
            ip = answers[0] if answers else None
            if ip:
                break
        except Exception:
            ip = None
    if not ip:
        # curl 兜底：urllib 偶发 DoH 失败时用 curl 再试一次
        try:
            r = subprocess.run(
                ["curl", "-sk", "--max-time", "10",
                 "https://1.1.1.1/dns-query?name=" + host + "&type=A",
                 "-H", "accept: application/dns-json"],
                capture_output=True, text=True, timeout=15)
            data = json.loads(r.stdout or "{}")
            answers = [a["data"] for a in data.get("Answer", [])
                       if a.get("type") == 1]
            ip = answers[0] if answers else None
        except Exception:
            ip = None
    _DOH_CACHE[host] = ip
    return ip


def post_json(url, headers, body=None):
    """用 curl 发 POST。不用 http.client：它在 Python 3.12 不接受
    server_hostname 参数，传 IP 当 host 又会让 SNI/证书对不上。
    curl 的 --resolve 既定向到 DoH 解析出的真实 IP，又按原域名做
    SNI 与证书校验。令牌等 header 写进 0600 的临时配置文件再用
    -K 读入，不放进命令行参数，避免 ps 可见。"""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    ip = resolve_via_doh(host)
    if not ip and host in ("copilot.tencent.com", "www.codebuddy.cn"):
        # 本 VM 系统解析是 sinkhole，DoH 失败时绝不能回落到系统解析，
        # 否则会向 198.18.x 超时 25 秒，还可能误报为签到失败。
        return 0, {"error": "doh_failed"}
    payload = json.dumps(body if body is not None else {})
    cfg = None
    bodyfile = None
    try:
        # 2026-10-29 修复：curl 配置文件里的单引号 data 是原样传给服务端的，
        # 带空格的 JSON 体（如余额查询）会报 invalid character '\''。
        # 改为把 body 写进 0600 临时文件，用 data-binary @文件发送。
        fd, bodyfile = tempfile.mkstemp(dir=os.path.expanduser("~/.workbuddy"),
                                        prefix=".body-")
        os.chmod(bodyfile, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        fd, cfg = tempfile.mkstemp(dir=os.path.expanduser("~/.workbuddy"),
                                   prefix=".curlcfg-")
        os.chmod(cfg, 0o600)
        lines = ["url = \"%s\"" % url, "request = \"POST\"",
                 "data-binary = \"@%s\"" % bodyfile,
                 "max-time = %d" % TIMEOUT,
                 "silent = true"]
        if ip:
            lines.append("resolve = \"%s:443:%s\"" % (host, ip))
        for k, v in headers.items():
            lines.append("header = \"%s: %s\"" % (k, v.replace('"', '')))
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        r = subprocess.run(["curl", "-K", cfg, "-w", "\n%{http_code}"],
                           capture_output=True, text=True, timeout=TIMEOUT + 10)
        out = (r.stdout or "").strip()
        if "\n" not in out:
            return 0, {"error": "curl_no_output"}
        raw, code_s = out.rsplit("\n", 1)
        try:
            status = int(code_s)
        except ValueError:
            return 0, {"error": "curl_bad_http_code"}
        try:
            return status, json.loads(raw or "{}")
        except Exception:
            return status, {}
    except Exception as e:
        return 0, {"error": type(e).__name__}
    finally:
        for p in (cfg, bodyfile):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass


def looks_like_jwt(token):
    parts = (token or "").strip().split(".")
    return len(parts) == 3 and all(parts) and len(token) >= 200


def save_token(token, token_file=None):
    token_file = token_file or TOKEN_FILE
    os.makedirs(os.path.dirname(token_file), exist_ok=True)
    tmp = token_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"refreshToken": token}, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, token_file)
    os.chmod(token_file, 0o600)


def load_token(token_file=None):
    with open(token_file or TOKEN_FILE) as f:
        return json.load(f)["refreshToken"]


def query_balance(access):
    """查当前积分：POST get-user-resource，聚合全部套餐。

    口径与开源 panel 的 cmd/credit 一致：每包优先用 Cycle* 字段，
    Cycle 为空时用 Capacity* 字段；总额度取各包额度之和与 TotalDosage
    的较大值。只返回数字，不含任何令牌与账号标识。
    失败时返回 (None, None, None, None, error)，不影响签到本身的判断。"""
    import datetime
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    body = {
        "PageNumber": 1,
        "PageSize": 100,
        "ProductCode": "p_tcaca",
        "Status": [0, 3],
        "PackageEndTimeRangeBegin": (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
        "PackageEndTimeRangeEnd": (now + datetime.timedelta(days=365 * 101)).strftime("%Y-%m-%d %H:%M:%S"),
    }
    http, resp = post_json(BALANCE_URL, {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": "Bearer " + access,
        "X-Domain": "www.codebuddy.cn",
        "User-Agent": UA,
    }, body)
    if http != 200 or resp.get("code") != 0:
        return None, None, None, None, resp.get("error") or ("http %d code %s" % (http, resp.get("code")))
    data = ((resp.get("data") or {}).get("Response") or {}).get("Data") or {}
    remain = used = size = 0
    for a in data.get("Accounts") or []:
        if a.get("CycleCapacitySize", 0) > 0:
            r = max(0, min(a.get("CycleCapacityRemain", 0), a["CycleCapacitySize"]))
            s = a["CycleCapacitySize"]
            u = s - r
            if a.get("CycleCapacityUsed", 0) > u:
                u = a["CycleCapacityUsed"]
                if s >= u:
                    r = s - u
        else:
            r, u, s = a.get("CapacityRemain", 0), a.get("CapacityUsed", 0), a.get("CapacitySize", 0)
            if u == 0 and s > r:
                u = s - r
        remain += r
        used += u
        size += s
    dosage = data.get("TotalDosage") or 0
    if dosage > size:
        size = dosage
        if size - remain > used:
            used = size - remain
    return size, used, remain, len(data.get("Accounts") or []), None


def cmd_setup(arg, account=None):
    try:
        label, token_file = token_file_for(account)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    token = sys.stdin.read().strip() if arg == "-" else (arg or "").strip()
    if not looks_like_jwt(token):
        print("不像有效 refreshToken：应为三段式 JWT，未落盘", file=sys.stderr)
        return 2
    save_token(token, token_file)
    print(json.dumps({"status": "ok", "account": label, "file": token_file,
                      "token_len": len(token)}, ensure_ascii=False))
    return 0


def do_checkin(token_file, label):
    """One account's refresh -> checkin -> balance. Returns (result, rc)."""
    try:
        old_rt = load_token(token_file)
    except Exception:
        return {"account": label, "status": "no_credential",
                "hint": "先走 OAuth 授权，再用 --setup-rt 落盘"}, 2
    http, resp = post_json(REFRESH_URL, {
        "Content-Type": "application/json",
        "X-Refresh-Token": old_rt,
        "X-Auth-Refresh-Source": "plugin",
        "X-Domain": "copilot.tencent.com",
        "User-Agent": UA,
    })
    data = resp.get("data") or {}
    new_access = data.get("accessToken")
    new_rt = data.get("refreshToken")
    if http != 200 or resp.get("code") != 0 or not new_access:
        # Do not overwrite the old token on a failed refresh.
        return {"account": label, "status": "refresh_failed",
                "refresh_http": http, "refresh_code": resp.get("code"),
                "refresh_error": resp.get("error")}, 1
    if new_rt and new_rt != old_rt:
        save_token(new_rt, token_file)
    http2, resp2 = post_json(CHECKIN_URL, {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Authorization": "Bearer " + new_access,
        "Origin": "https://www.codebuddy.cn",
        "Referer": "https://www.codebuddy.cn/",
        "User-Agent": UA,
    })
    d2 = resp2.get("data") or {}
    code2 = resp2.get("code")
    if http2 == 200 and code2 == 0:
        status, rc = "ok", 0
    elif code2 == 10001:
        # 幂等状态：今天已签到（无论是本脚本还是客户端先领的），
        # 奖励已在已领状态，不算凭证失败，定时任务不应为此告警。
        status, rc = "already_checked_in", 0
    else:
        status, rc = "checkin_http_error", 1
    if status in ("ok", "already_checked_in"):
        # 播报用余额：签到成功或已领时都顺带查一次，失败时不掩盖签到错误。
        b_total, b_used, b_remain, b_packs, b_err = query_balance(new_access)
    else:
        b_total = b_used = b_remain = b_packs = b_err = None
    return {
        "account": label,
        "status": status,
        "checkin_http": http2,
        "checkin_resp": {"code": code2, "msg": resp2.get("msg")},
        "checkin_error": resp2.get("error"),
        "credit": d2.get("credit"),
        "streak_days": d2.get("streak_days"),
        "is_streak_day": d2.get("is_streak_day"),
        "balance_total": b_total,
        "balance_used": b_used,
        "balance_remain": b_remain,
        "balance_packages": b_packs,
        "balance_error": b_err,
    }, rc


def cmd_checkin(account=None):
    try:
        label, token_file = token_file_for(account)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    result, rc = do_checkin(token_file, label)
    print(json.dumps(result, ensure_ascii=False))
    return rc


def cmd_checkin_all():
    accounts = list_accounts()
    if not accounts:
        print(json.dumps({"status": "no_credential", "accounts": [],
                          "hint": "先走 OAuth 授权，再用 --setup-rt 落盘"},
                         ensure_ascii=False))
        return 2
    results = []
    failed = 0
    for label, token_file in accounts:
        result, rc = do_checkin(token_file, label)
        results.append(result)
        if rc != 0:
            failed += 1
    if failed == 0:
        status = "ok" if any(r["status"] == "ok" for r in results) else "already_checked_in"
        rc = 0
    elif failed == len(results):
        status, rc = "checkin_failed", 1
    else:
        status, rc = "partial_failed", 1
    print(json.dumps({"status": status, "accounts": results},
                     ensure_ascii=False))
    return rc


def cmd_list_accounts():
    print(json.dumps({"accounts": [label for label, _ in list_accounts()]},
                     ensure_ascii=False))
    return 0


def cmd_status(account=None):
    try:
        label, token_file = token_file_for(account)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    exists = os.path.isfile(token_file)
    print(json.dumps({"account": label, "credential_file_exists": exists,
                      "file": token_file}, ensure_ascii=False))
    return 0 if exists else 2


def main(argv):
    # --account may appear after the command, in either form.
    account = None
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == "--account" and i + 1 < len(argv):
            account = argv[i + 1]
            i += 2
        elif argv[i].startswith("--account="):
            account = argv[i][len("--account="):]
            i += 1
        else:
            args.append(argv[i])
            i += 1
    if args == ["--status"]:
        return cmd_status(account)
    if args == ["--list-accounts"]:
        return cmd_list_accounts()
    if args == ["--checkin-all"]:
        return cmd_checkin_all()
    if args and args[0] == "--setup-rt":
        return cmd_setup(args[1] if len(args) > 1 else "", account)
    if args in ([], ["--checkin"]):
        return cmd_checkin(account)
    print("用法: checkin.py [--setup-rt -|<token> | --checkin | --status | "
          "--checkin-all | --list-accounts] [--account <label>]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
