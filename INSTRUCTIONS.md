# INSTRUÇÕES OPERACIONAIS - CRYPTOBOT (FIMATHE LITE-3.8)

Este documento instrui o agente **Antigravity** quando executado na VM Oracle ou localmente. Deve ser consultado para entender a arquitetura, estratégia, ferramentas e diretrizes do robô de trading.

---

## 1. Visão Geral do Sistema

O **Cryptobot** é um sistema automatizado de trading quantitativo voltado para o par **SOL/USDT:USDT** (KuCoin Futures) operando no tempo gráfico de **5 minutos (5m)** com base na metodologia adaptativa **Fimathe (Lite-3.8)**.

### Pilares da Arquitetura
* **Linguagem / Runtime:** Python 3.9+ com ambiente virtual isolado em `.venv/`.
* **Conexão com Corretora:** Biblioteca `ccxt.kucoinfutures` encapsulada em `core/exchange.py`.
* **Modos de Operação:**
  * `MODO=live`: Envio de ordens reais na KuCoin Futures via chaves de API (`.env`).
  * `MODO=paper`: Simulador nativo com preços reais da KuCoin (`core/paper.py`), simulando slippage, taxas e execução de stops.
* **Persistência de Dados:** SQLite em `data/bot.db` (tabelas: `ordens`, `sinais`, `estado`).
* **Logs do Sistema:** Gravados rotativamente em `logs/bot.log` e capturas em `logs/bot_stdout.log` / `logs/bot_stderr.log`.

---

## 2. Estrutura de Diretórios e Componentes

```text
/home/opc/cryptobot/
├── .env                  # Variáveis de ambiente, API keys, parâmetros de risco e trading
├── main.py               # Orquestrador do ciclo: candles -> sinal -> risco -> ordens -> trailing
├── start_bot.sh          # Script de inicialização nohup desvinculado do SSH (comando 'start')
├── status_bot.sh         # Script de consulta de status e tail do log (comando 'status')
├── stop_bot.sh           # Script de encerramento seguro de processos (comando 'stop')
├── system1_audit.py      # Motor de auditoria estática pré/pós-execução (System 1)
│
├── core/
│   ├── exchange.py       # Wrapper CCXT com suporte a auto marginMode (cross/isolated) e retries
│   ├── risk.py           # Gestão de risco por trade, alavancagem dinâmica e freio diário
│   └── paper.py          # Simulador paper trading integrado com preços em tempo real
│
├── strategies/
│   └── strategy.py       # Algoritmo Fimathe (canais, canais de referência, zona neutra, stops)
│
├── data/
│   ├── bot.db            # Banco SQLite de ordens e histórico
│   ├── bot.pid           # PID do processo ativo em segundo plano
│   └── bot_meta.json     # Metadados de execução (modo, estratégia, horário)
│
└── logs/
    └── bot.log           # Log operacional detalhado
```

---

## 3. Estratégia: Fimathe Lite-3.8 Campeã

* **Detecção de Canais:** Identifica topos e fundos com pivôs dinâmicos (`JANELA_PIVO=4`).
* **Subníveis e Canais:** Define o *Canal de Referência* e a *Zona Neutra*.
* **Gatilho de Entrada:** Rompimento limpo no fechamento do candle fora do canal (`romper_no_canal=True`).
* **Stop Loss:** Configurado em `fora_caixinha` (2 níveis simétricos atrás do ponto de entrada).
* **Gestão de Alvos:**
  * **Alvo 1 (Degrau 1):** Stop movido para 0x0 (Break Even).
  * **Trailing Stop Progressivo:** A cada degrau (`LITE38_FATOR_DEGRAU * amplitude`) avançado a favor da operação, o stop é recalculado e elevado para o degrau anterior antes do cancelamento do stop antigo.
* **Circuit Breaker:** Suspensão automática de novas entradas por `CB_PAUSA_HORAS=6.0` caso ocorram losses consecutivos dentro de `CB_JANELA_HORAS=24.0`.

---

## 4. Particularidades Críticas da KuCoin Futures

### Margem Cruzada vs. Margem Isolada (`marginMode`)
* A KuCoin Futures rejeita ordens com o erro `330005 ("The order's margin mode does not match the selected one")` se o parâmetro `marginMode` não corresponder exatamente ao modo ativo da conta/par.
* **Solução implementada em `core/exchange.py`:**
  * O método `obter_margin_mode(symbol)` consulta via API (`fetch_margin_mode`) e detecta o modo configurado (`cross` ou `isolated`).
  * Todas as ordens de entrada, stops e fechamento enviam automaticamente `"marginMode": self.obter_margin_mode(symbol)`.
  * Se houver alteração manual pelo usuário, o bot limpa o cache e retenta automaticamente.

---

## 5. Ferramentas e Comandos de Operação na VM

Na VM Oracle (`163.176.209.126`), comandos diretos foram registrados no ambiente:

* **Iniciar o Robô:**
  ```bash
  start
  # ou ./start_bot.sh
  ```
* **Verificar Status e Logs Recentes:**
  ```bash
  status
  # ou ./status_bot.sh
  ```
* **Encerrar o Robô:**
  ```bash
  stop
  # ou ./stop_bot.sh
  ```
* **Acompanhar Logs em Tempo Real:**
  ```bash
  tail -f logs/bot.log
  ```
* **Executar no Ambiente Virtual Remoto:**
  Sempre utilize o interpretador `.venv/bin/python3` ou ative o ambiente com `source .venv/bin/activate`.

---

## 6. Diretrizes de Auditoria e Anti-Alucinação (System 1)

1. Nunca declare comandos ou alterações prontas sem validação estática de sintaxe (`python3 -m py_compile`).
2. Utilize o script `system1_audit.py` para verificar comandos e sintaxe de scripts modificados.
3. Não suba alterações via Git/GitHub se o usuário solicitou fluxo direto de sincronização; envie arquivos pontuais via `scp` ou gere pacote `.zip` limpo.
