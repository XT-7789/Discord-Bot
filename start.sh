#!/bin/bash
# 1. 彻底杀掉旧的 Python 进程（防止僵尸进程占用数据库和端口）
killall -9 python 2>/dev/null
pkill -9 -f "python" 2>/dev/null
sleep 2

# 2. 启动机器人放入后台并记录日志
python -u bot.py > bot.log 2>&1 &
echo "[Start] Bot launched in background (PID: $!)"

# 3. 启动网页端并禁用缓冲
python -u dashboard.py
