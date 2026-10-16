#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
mkdir -p logs data
source .venv/bin/activate
nohup python3 main.py > logs/bot_stdout.log 2> logs/bot_stderr.log &
echo $! > data/bot.pid
echo "Bot iniciado com sucesso na VM (PID $(cat data/bot.pid))"
