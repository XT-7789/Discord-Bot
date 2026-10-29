#!/data/data/com.termux/files/usr/bin/bash
# reload_and_update.sh - copy files + run DB update + restart bot

BOT_DIR="$HOME/Discord-Bot"

echo "[1/4] Copying updated files..."
cp /sdcard/Download/leveling.py "$BOT_DIR/leveling.py"
cp /sdcard/Download/economy_extra.py "$BOT_DIR/economy_extra.py"
cp /sdcard/Download/update_xp_rewards.py "$BOT_DIR/update_xp_rewards.py"

echo "[2/4] Running DB update..."
cd "$BOT_DIR"
python3 update_xp_rewards.py

echo "[3/4] Killing old bot process..."
pkill -f "python.*bot.py" 2>/dev/null || true
sleep 2

echo "[4/4] Starting bot..."
cd "$BOT_DIR"
nohup python -u bot.py > bot.log 2>&1 &
echo "Bot started (PID $!)"
echo "Done! Run: tail -f $BOT_DIR/bot.log to view logs"
