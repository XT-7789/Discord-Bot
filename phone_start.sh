#!/data/data/com.termux/files/usr/bin/bash
# X BOT Tier 4 phone supervisor. Keep this running inside tmux.
set -u

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BOT_DIR" || exit 1
mkdir -p logs

if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock
fi

stop_children() {
  if [ -n "${DASHBOARD_PID:-}" ]; then
    kill "$DASHBOARD_PID" 2>/dev/null || true
  fi
  if command -v termux-wake-unlock >/dev/null 2>&1; then
    termux-wake-unlock
  fi
}
trap stop_children EXIT INT TERM

(
  while true; do
    printf '\n[%s] Starting dashboard\n' "$(date '+%F %T')" >> logs/dashboard-phone.log
    python -u dashboard.py >> logs/dashboard-phone.log 2>&1
    printf '[%s] Dashboard stopped; restarting in 5 seconds\n' "$(date '+%F %T')" >> logs/dashboard-phone.log
    sleep 5
  done
) &
DASHBOARD_PID=$!

while true; do
  printf '\n[%s] Starting bot\n' "$(date '+%F %T')" | tee -a logs/bot-phone.log
  python -u bot.py 2>&1 | tee -a logs/bot-phone.log
  printf '[%s] Bot stopped; restarting in 5 seconds\n' "$(date '+%F %T')" | tee -a logs/bot-phone.log
  sleep 5
done
