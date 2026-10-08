#!/usr/bin/env bash
# Send the daily reminder without any agent: run from cron / a systemd timer / launchd.
#
#   TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... ./notify.sh        # Telegram Bot API
#   NTFY_TOPIC=my-secret-topic ./notify.sh                          # ntfy.sh push (ntfy app on phone)
#   TELEGRAM_RICH=1 ... ./notify.sh                                 # send via sendRichMessage (bold, task list);
#                                                                   # falls back to plain sendMessage on a 4xx
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
rich=""
if [ "${TELEGRAM_RICH:-}" = "1" ]; then
  # shellcheck disable=SC2086
  rich="$($LEARN nudge --markdown ${LEARN_NUDGE_ARGS:-})"
fi

if [ "${1:-}" = "--print" ]; then
  printf '%s\n' "$msg"
elif [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  api="${TELEGRAM_API_BASE:-https://api.telegram.org}/bot${TELEGRAM_BOT_TOKEN}"
  sent=0
  if [ -n "$rich" ]; then
    # Bot API 10.1+ sendRichMessage: exactly one of markdown/html/blocks inside rich_message.
    # Only a 4xx (unsupported / bad request) falls through to plain text; a timeout or 5xx has an
    # unknown outcome, so we do NOT resend (avoids duplicates).
    payload="$(python3 -c 'import json,sys; print(json.dumps({"chat_id": sys.argv[1], "rich_message": {"markdown": sys.argv[2]}}))' "$TELEGRAM_CHAT_ID" "$rich")"
    code="$(curl -sS -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d "$payload" "$api/sendRichMessage" || echo 000)"
    case "$code" in
      200) sent=1 ;;
      4??) ;;                     # fall back to plain
      *) echo "sendRichMessage returned $code; not retrying" >&2; exit 1 ;;
    esac
  fi
  if [ "$sent" = 0 ]; then
    curl -fsS "$api/sendMessage" --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" --data-urlencode "text=${msg}" >/dev/null
  fi
elif [ -n "${NTFY_TOPIC:-}" ]; then
  curl -fsS -H "Title: Learn" -d "$msg" "${NTFY_SERVER:-https://ntfy.sh}/${NTFY_TOPIC}" >/dev/null
else
  echo "set TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID or NTFY_TOPIC (or pass --print)" >&2
  exit 2
fi
