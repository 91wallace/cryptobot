# 🏛️ Arquitetura do Sistema CryptoBot

## 1. Separação de Responsabilidades

| Camada | Diretório | Descrição |
| :--- | :--- | :--- |
| **Infraestrutura & Execução** | `core/` | Gerenciamento de conexões com exchanges (`exchange.py`), dimensionamento de posições e risco (`risk.py`), travas temporais (`circuit_breaker.py`), simulador paper trading (`paper.py`) e rastreador de oportunidades (`screener.py`). |
| **Modelos & Estratégias** | `strategies/` | Modelagem matemática da Fimathe (`fimathe.py`), detecção de pivôs/canais e gatilhos da **Apex Dinamics** (`strategy.py`). |
| **Backtesting & Validações** | `backtests/` | Motor genérico (`engine/backtest.py`), suíte oficial da estratégia vencedora (`apex_dinamics/`) e estudos exploratórios históricos (`exploratory/`). |
| **Visualização & Dashboard** | `dashboard/` | Interface visual Streamlit (`app.py`) e copiloto conversacional (`chat_assistant.py`). |
| **Pesquisa Numérica** | `research/` | Integração e rotinas em MATLAB (`export_matlab.py`, `*.m`). |
| **Documentação** | `docs/` | Manuais de estratégias e guias arquiteturais. |

## 2. Compatibilidade e Resolução de Módulos

O ambiente virtual foi configurado com paths automáticos no Python (`.venv/Lib/site-packages/cryptobot.pth`), permitindo que qualquer módulo importe tanto pelo caminho absoluto do pacote (`from core.risk import GerenciadorRisco`) quanto pelos nomes diretos legados (`from risk import GerenciadorRisco`), garantindo 100% de compatibilidade sem quebrar nenhum script existente.
