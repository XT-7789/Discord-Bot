#!/bin/bash
# 1. 自动杀掉旧的 Python 机器人与后台进程，防止多重运行冲突
pkill -f "python.*bot.py" 2>/dev/null
pkill -f "python.*dashboard.py" 2>/dev/null
sleep 1

# 2. 启动机器人放入后台
python -u bot.py &

# 3. 启动网页端并禁用缓冲
python -u dashboard.py
