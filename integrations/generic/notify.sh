#!/usr/bin/env bash
# Send the daily reminder without any agent: run from cron / a systemd timer / launchd.
#
#   TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... ./notify.sh        # Telegram Bot API
#   NTFY_TOPIC=my-secret-topic ./notify.sh                          # ntfy.sh push (ntfy app on phone)
#   ./notify.sh --print                                             # just print (dry run)
#
# Silent when there is nothing to do (`learn nudge` prints nothing).
# Example crontab (09:00, Mon–Sat):  0 9 * * 1-6  /path/to/notify.sh
set -euo pipefail

LEARN="${LEARN_BIN:-$(command -v learn || true)}"
if [ -z "$LEARN" ]; then
  LEARN="python3 $(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/learn.py"
fi

# shellcheck disable=SC2086
msg="$($LEARN nudge ${LEARN_NUDGE_ARGS:-})"
[ -z "$msg" ] && exit 0

if [ "${1:-}" = "--print" ]; then
  printf '%s\n' "$msg"
elif [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  curl -fsS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" --data-urlencode "text=${msg}" >/dev/null
elif [ -n "${NTFY_TOPIC:-}" ]; then
  curl -fsS -H "Title: Learn" -d "$msg" "${NTFY_SERVER:-https://ntfy.sh}/${NTFY_TOPIC}" >/dev/null
else
  echo "set TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID or NTFY_TOPIC (or pass --print)" >&2
  exit 2
fi
