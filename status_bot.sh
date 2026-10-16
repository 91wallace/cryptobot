#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
if [ -f data/bot.pid ] && kill -0 "$(cat data/bot.pid)" 2>/dev/null; then
    echo "=============================================="
    echo " STATUS DO BOT NA VM: RODANDO ATIVO ✅"
    echo " PID: $(cat data/bot.pid)"
    echo "=============================================="
    echo "Últimos registros do log (logs/bot.log):"
    tail -n 15 logs/bot.log
else
    echo "=============================================="
    echo " STATUS DO BOT NA VM: PARADO 🛑"
    echo "=============================================="
fi
