#!/usr/bin/env bash
# daily_checkin.sh — WorkBuddy 每日签到定时入口（2026-10-28，多账号版 2026-10-29）
# 多账号：跑 checkin.py --checkin-all，逐个账号签到，输出为一个 JSON
#（accounts[]，每项含该账号的五项播报字段），一个账号失败不影响其他账号。
#
# 用户要求“9 点左右就可以，不用精确到 9 点”：本脚本在定时触发后先随机
# 等 0–30 分钟（WORKBUDDY_DAILY_DELAY_MAX，默认 1800 秒；测试时可设小值），
# 再跑 checkin.py。实际签到时间每天在 09:00–09:30（北京时间）之间浮动。
# 输出只有 checkin.py 的 JSON（不含令牌），并追加到 ~/.workbuddy/checkin.log。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="$HOME/.workbuddy/checkin.log"
MAX="${WORKBUDDY_DAILY_DELAY_MAX:-1800}"
DELAY=$((RANDOM % (MAX + 1)))
echo "[$(date -Iseconds)] workbuddy daily checkin: waiting ${DELAY}s" >> "$LOG"
sleep "$DELAY"
OUT="$(python3 "$HERE/checkin.py" --checkin-all 2>&1)"
RC=$?
echo "[$(date -Iseconds)] $OUT" >> "$LOG"
echo "$OUT"
exit "$RC"
