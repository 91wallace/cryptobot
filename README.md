# 🤖 CryptoBot - Arquitetura e Organização do Projeto

Sistema modular de trading algorítmico quantitativo em criptoativos (Binance Spot & Futures), implementando a metodologia **Fimathe Dinâmica** e a estratégia oficial **Apex Dinamics™**.

---

## 📁 Estrutura de Diretórios

```
cryptobot/
├── core/                         # Motor central e infraestrutura
│   ├── exchange.py               # Conector CCXT com Binance Spot e USDT-M Futures
│   ├── risk.py                   # Gerenciador de capital, margem e risco
│   ├── circuit_breaker.py        # Circuit Breaker temporal escalonado anti-choppiness
│   ├── paper.py                  # Simulador de ordens paper trading com taxa e slippage
│   └── screener.py               # Screener de pares por Volume ($24h) e Volatilidade
│
├── strategies/                   # Estratégias quantitativas e técnicas
│   ├── strategy.py               # Lógica Fimathe, detecção de pivôs, canais e Apex Dinamics
│   ├── fimathe.py                # Algoritmo matemático das grades, canais e ATR
│   ├── fimathe_consolidado.py    # Variação com análise de consolidação
│   └── ESTRATEGIA_APEX_DINAMICS.md # Especificação técnica oficial da estratégia
│
├── backtests/                    # Suíte de backtests e simulações
│   ├── apex_dinamics/            # Suíte oficial da estratégia Apex Dinamics
│   │   ├── backtest_7dias_top4_10x.py       # Setup Campeão (7 dias, 10x, AVAX/SOL/NEAR/ETH)
│   │   ├── run_setup_vencedor_5dias.py      # Validação multi-par 5 dias
│   │   ├── simular_carteira_3posicoes.py    # Simulação com limite de 3 slots simultâneos
│   │   ├── backtest_5dias_50pares_10x.py    # Backtest amostral em 50 pares
│   │   ├── run_circuit_breaker_10pares.py   # Validação de eficácia do Circuit Breaker
│   │   └── run_trailing_fimathe_10pares.py  # Condução de Trailing Stop por subníveis
│   │
│   ├── engine/                   # Motor base de backtesting
│   │   └── backtest.py           # Engine de simulação histórica de candles e ordens
│   │
│   └── exploratory/              # Testes históricos e análises pontuais
│       ├── analisar_zec.py                  # Análise aprofundada de caso individual
│       ├── comparar_1_nivel.py              # Rompimento 1 nível vs 2 níveis
│       ├── comparar_filtros_5m.py           # Testes com e sem filtros de KER/RVOL
│       ├── diagnostico_pares.py             # Diagnóstico de spreads e liquidez
│       ├── backtest_top5_volume_5dias.py    # Top 5 pares em volume
│       └── ...                              # Backtests de 5m, 15m, 72h e consolidação
│
├── dashboard/                    # Interface visual interativa (Streamlit)
│   ├── app.py                    # Aplicação Streamlit com gráficos e backtests ao vivo
│   └── chat_assistant.py         # Assistente inteligente integrado com IA
│
├── research/                     # Modelagem quantitativa e MATLAB
│   ├── export_matlab.py          # Exportador de métricas e candles para MATLAB
│   ├── fimathe_analysis.m        # Script de análise gráfica MATLAB
│   └── fimathe_futures_15m.m     # Análise de futuros 15m no MATLAB
│
├── docs/                         # Documentação técnica e manuais
│   ├── ESTRATEGIA_APEX_DINAMICS.md # Manual Operacional da Estratégia Apex Dinamics
│   └── ARQUITETURA.md            # Guia de arquitetura e padrões de código
│
├── main.py                       # Orquestrador do bot em execução contínua
├── app.py                        # Atalho de inicialização do Dashboard
├── dashboard.bat                 # Launcher rápido em 1 clique (Windows CMD)
├── dashboard.ps1                 # Launcher rápido PowerShell
├── requirements.txt              # Dependências Python
└── .env                          # Variáveis de ambiente e chaves de API
```

---

## 🚀 Como Executar

### 1. Iniciar o Dashboard Visual (Web)
- Pelo atalho Windows: execute `dashboard.bat` ou `.\dashboard.ps1`
- Ou via terminal:
  ```bash
  streamlit run dashboard/app.py
  ```

### 2. Iniciar o Dashboard Interativo via Terminal (TUI)
- Pelo atalho Windows: execute `tui.bat` ou `.\tui.ps1`
- Ou via terminal:
  ```powershell
  python tui.py
  python tui.py --demo   # Modo de demonstração / fallback com dados simulados
  ```
- Ou via alias do PowerShell: `/tui`
- **Controles e Atalhos Interativos**:
  - `Q`: Encerra o dashboard restaurando o terminal
  - `Espaço` ou `P`: Pausar / Retomar atualização contínua
  - `R`: Forçar sincronização imediata
  - `D`: Alternar dinamicamente entre leitura do robô e modo demo
  - `1`, `2`, `3`, `4`: Alternar entre abas (Visão Geral, Operações, Estratégia, Logs)


### 3. Rodar o Setup Campeão Apex Dinamics (7 Dias, Top 4 Pares)
```bash
python backtests/apex_dinamics/backtest_7dias_top4_10x.py
```

### 4. Rodar Simulação de Carteira (3 Posições Simultâneas)
```bash
python backtests/apex_dinamics/simular_carteira_3posicoes.py
```

### 5. Executar o Bot em Modo Paper / Produção
```bash
python main.py
```

