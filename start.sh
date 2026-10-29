#!/bin/bash
# 1. 彻底杀掉旧的 Python 进程
killall -9 python 2>/dev/null
pkill -9 -f "python" 2>/dev/null
sleep 1

# 2. 网页端已按需求冻结 (Frozen)
# python -u dashboard.py

# 3. 运行精简版 X BOT (前台输出日志，轻量稳定)
echo "[X BOT] Starting streamlined bot (War & Dashboard frozen)..."
python -u bot.py
