#!/bin/bash
BOT_DIR="$HOME/Discord-Bot"
cd "$BOT_DIR"

echo "[1/3] Copying updated files..."
cp /sdcard/Download/bot.py "$BOT_DIR/bot.py"
cp /sdcard/Download/suites.py "$BOT_DIR/suites.py"
cp /sdcard/Download/leveling.py "$BOT_DIR/leveling.py"
cp /sdcard/Download/economy_extra.py "$BOT_DIR/economy_extra.py"
cp /sdcard/Download/update_xp_rewards.py "$BOT_DIR/update_xp_rewards.py"

echo "[2/3] Updating database (xp_rewards & streak removal)..."
python3 "$BOT_DIR/update_xp_rewards.py"

echo "[3/3] Starting bot..."
bash "$BOT_DIR/start.sh"
