# Original User Request

## Initial Request — 2026-10-14T22:49:51Z

This is a single self-contained feature; keep it small and focused.

Construir um dashboard interativo via terminal (TUI) para monitoramento em tempo real do bot de operações (cryptobot), exibindo status de execução, estratégia ativa, histórico de operações e métricas de lucro/performance.

Working directory: C:/Users/pece/projects/cryptobot
Integrity mode: development

## Requirements

### R1. Interface de Terminal Interativa (TUI)
- Desenvolver um painel interativo de terminal (TUI) utilizando bibliotecas especializadas (como Rich/Textual) com suporte nativo a Windows PowerShell e renderização fluida.
- Incluir blocos visuais dedicados para:
  1. Status do Bot (Online/Offline/Pausado, uptime, latência/ciclo).
  2. Estratégia Ativa e parâmetros essenciais em execução.
  3. Painel de Operações Recentes (tabela/lista de trades, pares, tipo de ordem, preço, status).
  4. Métricas Financeiras e Lucro (Profit consolidado, PnL diário/total, win rate).

### R2. Atualização Assíncrona e Controles Interativos
- Ciclo de renderização e atualização contínua e assíncrona, sem bloquear a navegação ou travar o terminal.
- Suporte a atalhos de teclado (ex: pausar/retomar refresh, alternar abas/visões, forçar sincronização, sair de forma limpa sem corromper o terminal).

### R3. Integração Desacoplada com o Estado do Cryptobot
- Conectar-se às fontes de dados existentes do `cryptobot` (arquivos de estado, sqlite/db, logs ou interfaces internas) de modo somente-leitura ou não-bloqueante, garantindo que o bot continue rodando independentemente do dashboard.
- Fornecer modo de fallback/simulação de dados caso o bot esteja inativo, permitindo testes isolados da interface.

## Acceptance Criteria

### Integridade Visual e TUI
- [ ] O dashboard inicializa no Windows PowerShell sem distorções de layout ou quebra de caracteres ANSI.
- [ ] Atalhos de saída (`q` ou `Ctrl+C`) encerram o processo restaurando o cursor e o estado padrão do terminal.

### Verificação Funcional
- [ ] As 4 seções principais (Status, Estratégia, Operações e Lucro) exibem dados dinâmicos e atualizados.
- [ ] Existe um script ou teste automatizado executável (ex: `pytest` ou smoke test headless) que valida a coleta de métricas e a integridade dos dados da TUI sem falhas.
