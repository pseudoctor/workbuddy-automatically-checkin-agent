#!/usr/bin/env bash
# workbuddy_oauth.sh — WorkBuddy/CodeBuddy OAuth 设备授权 + 轮询凭证（无第三方依赖，仅 curl+python3）
#
# 用法:
#   bash workbuddy_oauth.sh url     # 第一步：签发授权链接（stdout 输出 authUrl，state 存 .wb_oauth_state.json）
#   bash workbuddy_oauth.sh poll    # 第二步：登录完成后轮询一次凭证（成功 stdout 输出完整 JSON，state 文件删除）
#   bash workbuddy_oauth.sh watch   # 可选：后台自动轮询（默认 30 分钟），成功即退出
#
# 输出 JSON 字段: accessToken / refreshToken / expiresIn / domain / state
# 安全: 凭证请立即用 checkin.py --setup-rt -（stdin）落盘，勿写入日志或聊天。
set -euo pipefail

BASE="https://copilot.tencent.com"
ORIGIN="https://www.codebuddy.cn"
UA="CLI/2.63.2 CodeBuddy/2.63.2"
# Muse 改进 (2026-10-28)：state/cookie 不再放当前目录，改放 ~/.workbuddy，
# 避免 cookie 随工作目录被打包/同步出去；jar 设 0600。
WORKBUDDY_DIR="${HOME}/.workbuddy"
STATE_FILE="$WORKBUDDY_DIR/.wb_oauth_state.json"
JAR_FILE="$WORKBUDDY_DIR/.wb_oauth_cookies.txt"
WATCH_MINUTES="${WATCH_MINUTES:-30}"
# Muse 改进 (2026-10-28)：本 VM 本地解析器是 sinkhole（所有域名都返回 198.18.x，
# 直连腾讯会超时 25s）。改用 DoH（1.1.1.1）解析真实 IP 再 --resolve；TLS 仍按原
# 域名校验证书，DoH 返回错误 IP 时证书校验会失败，不会静默接受。
doh_ip() {
  curl -sk --max-time 10 "https://1.1.1.1/dns-query?name=$1&type=A" \
    -H "accept: application/dns-json" 2>/dev/null | python3 -c \
    "import json,sys; d=json.load(sys.stdin); a=[x['data'] for x in d.get('Answer',[]) if x['type']==1]; print(a[0] if a else '')" 2>/dev/null || true
}
COPILOT_IP="$(doh_ip copilot.tencent.com)"
COPILOT_RESOLVE=""
[ -n "$COPILOT_IP" ] && COPILOT_RESOLVE="--resolve copilot.tencent.com:443:$COPILOT_IP"

headers_state() {
  # shellcheck disable=SC2086
  curl -s --connect-timeout 10 --max-time 25 $COPILOT_RESOLVE -c "$JAR_FILE" -X POST \
    "$BASE/v2/plugin/auth/state?platform=CLI" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/plain, */*" \
    -H "X-Requested-With: XMLHttpRequest" \
    -H "Origin: $ORIGIN" -H "Referer: $ORIGIN/" -H "User-Agent: $UA" \
    -d '{}'
}

headers_token() {
  # 关键：-b/-c 复用与签发 state 时同一份 Cookie（tgw_l7_route），对齐官方 CLI 行为
  # shellcheck disable=SC2086
  curl -s --connect-timeout 10 --max-time 20 $COPILOT_RESOLVE -b "$JAR_FILE" -c "$JAR_FILE" \
    "$BASE/v2/plugin/auth/token?state=$1" \
    -H "Accept: application/json, text/plain, */*" \
    -H "X-Requested-With: XMLHttpRequest" \
    -H "Origin: $ORIGIN" -H "Referer: $ORIGIN/" -H "User-Agent: $UA"
}

cmd_url() {
  local resp state authurl
  mkdir -p "$WORKBUDDY_DIR" && chmod 700 "$WORKBUDDY_DIR"
  resp=$(headers_state)
  [ -f "$JAR_FILE" ] && chmod 600 "$JAR_FILE"
  state=$(echo "$resp" | python3 -c "import json,sys;d=json.load(sys.stdin);print((d.get('data') or {}).get('state',''))")
  authurl=$(echo "$resp" | python3 -c "import json,sys;d=json.load(sys.stdin);print((d.get('data') or {}).get('authUrl',''))")
  if [ -z "$state" ] || [ -z "$authurl" ]; then
    echo "签发失败: $resp" >&2; exit 1
  fi
  printf '{"state":"%s"}\n' "$state" > "$STATE_FILE"
  chmod 600 "$STATE_FILE"
  echo "$authurl"
}

poll_once() {
  local state resp code
  [ -f "$STATE_FILE" ] || { echo "未找到 $STATE_FILE，请先执行 url 子命令" >&2; exit 1; }
  state=$(python3 -c "import json;print(json.load(open('$STATE_FILE'))['state'])")
  resp=$(headers_token "$state")
  code=$(echo "$resp" | python3 -c "import json,sys
try: print(json.load(sys.stdin).get('code'))
except: print('x')" 2>/dev/null || echo x)
  if [ "$code" = "0" ]; then
    echo "$resp" | python3 -c "
import json,sys
d=json.load(sys.stdin)
data=d.get('data') or {}
out={'accessToken':data.get('accessToken'),'refreshToken':data.get('refreshToken'),'expiresIn':data.get('expiresIn'),'domain':data.get('domain'),'state':'$state'}
print(json.dumps(out,ensure_ascii=False))"
    rm -f "$STATE_FILE" "$JAR_FILE"
    return 0
  fi
  return 1
}

cmd_poll() {
  if poll_once; then return 0; fi
  echo "登录未完成（login ing）。请确认已在浏览器打开授权链接并完成登录后重试。" >&2
  exit 1
}

cmd_watch() {
  local state deadline
  [ -f "$STATE_FILE" ] || { echo "未找到 $STATE_FILE，请先执行 url 子命令" >&2; exit 1; }
  deadline=$(( $(date +%s) + WATCH_MINUTES * 60 ))
  echo "开始轮询（最长 ${WATCH_MINUTES} 分钟）…" >&2
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if poll_once; then exit 0; fi
    sleep 20
  done
  echo "轮询超时：授权 state 可能已过期，请重新执行 url 子命令。" >&2
  exit 1
}

case "${1:-}" in
  url)   cmd_url ;;
  poll)  cmd_poll ;;
  watch) cmd_watch ;;
  *) echo "用法: bash $0 <url|poll|watch>" >&2; exit 1 ;;
esac
