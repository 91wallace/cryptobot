#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
if [ -f data/bot.pid ]; then
    PID=$(cat data/bot.pid)
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "Bot encerrado com sucesso (PID $PID)"
    else
        echo "Processo $PID não estava ativo."
    fi
    rm -f data/bot.pid
else
    pkill -f "python3 main.py" && echo "Processos do bot encerrados." || echo "Nenhum bot em execução."
fi
