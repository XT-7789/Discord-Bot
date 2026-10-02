#!/bin/bash

# 2. 启动机器人放入后台，并禁用缓冲以便实时看到报错
python -u bot.py &

# 3. 启动网页端并禁用缓冲
python -u dashboard.py
