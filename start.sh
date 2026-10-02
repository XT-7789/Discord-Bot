#!/bin/bash
# 1. 强制在所有程序启动前，先从云端下载数据库文件，避免竞态条件
python -c 'import cloud_sync; cloud_sync.download_db("xwar.db")'

# 2. 启动机器人放入后台，并禁用缓冲以便实时看到报错
python -u bot.py &

# 3. 启动网页端并禁用缓冲
python -u dashboard.py
