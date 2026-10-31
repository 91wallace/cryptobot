# conf.md — Briefing de Projeto para Agentes/LLMs

> **Leia este arquivo primeiro.** Resumo operacional e técnico completo do bot de trading
> quantitativo **Apex Dinamics / Fimathe** (KuCoin Futures). Atualizado em 2026-10-31.
>
> **Regra de segurança deste documento:** aqui NÃO há IPs, portas, chaves, tokens, credenciais,
> usernames de servidor nem histórico de ordens pessoais. O acesso à VM de produção é feito
> SOMENTE pelo alias `oracle-vm` (configurado no `~/.ssh/config` do host local). Nunca publique
> nem transcreva segredos em chats, issues ou commits.

---

## 1. O que é o projeto

Bot de trading quantitativo em **Python** para **KuCoin Futures** (via CCXT), foco atual
**SOL/USDT:USDT** no timeframe **5m**. Componentes:

- **Bot live** (`main.py`, loop vela-a-vela): geração de sinal → veto Laya System 1 →
  gerenciador de risco → execução na corretora (spot ou futures, paper ou live) →
  gestão de posição (stop/trailing/alvo) → telemetria de saída.
- **Estratégia Apex Dinamics (Lite-3.8)** = `EstrategiaFimathe` (`strategies/strategy.py`),
  motor matemático em `strategies/fimathe.py` (grade geométrica, ATR, pivôs, KER).
- **Laya System 1**: camada cognitiva de auditoria/inferência (veto anti-erro + telemetria de feedback).
- **Backtests**: harnesses próprios `backtests/` e motor **Nautilus Trader** (`nautilus-trade/` vendorizado).
- **Dashboard/analítica**: `app.py`, `dashboard.py`, `fast_dashboard.py`, `tui/` (Streamlit/TUI), banco SQLite (`data/bot.db`).

Estado de produção (15/out – 31/out/2026): VM `oracle-vm`, bot live real-money
(paper desligado), operando apenas SOL/USDT:USDT.

---

## 2. Estratégias ativas — características e especificações

### 2.1 Apex Dinamics / Fimathe (a única estratégia ativa no live)

Identidade: `strategy_id = STRAT_APEX_DINAMICS_V1`, nome `apex_dinamics`, perfil `lite-3.8`.

Princípio: **grade geométrica sobre canal de referência + rompimento com momentum**.
Etapas (`strategies/strategy.py::EstrategiaFimathe.avaliar(df)`):

1. Detecção de pivôs (topos/fundos por fractal, `janela_pivo`, padrão 4).
2. Canal de Referência = último fundo e último topo confirmados.
3. Grade Fimathe (`calcular_grade_fimathe`): Zonas Neutras, Linha de 50%, Expansões
   (alvos intra/inter-canal; é possível mapear níveis proporcionais/Fibonacci).
4. Sinal quando o preço rompe a Zona Neutra a favor da tendência identificada nos pivôs.
5. Stop configurável por `modo_stop`: `fora_caixinha` (padrão do live), `linha_50`,
   `rompimento`, `auto_adaptativo`.

Filtros de qualidade do sinal (todos configuráveis por env):

| Filtro | Env | Estado live | O que faz |
|---|---|---|---|
| KER (Kaufman Efficiency Ratio) | `KER_FILTRO_ATIVO` / `KER_MINIMO` / `KER_PERIODO` | **ON, 0.25 / 12** | Descarta rompimentos serrilhados (anti-choppy). 0.25 é o sweet spot (0.35 overfiltra e mata o momentum, ver §5) |
| ATR | `ATR_FILTRO_ATIVO`, `ATR_PERIODO`, `FATOR_MIN_ATR`, `FATOR_MAX_ATR` | OFF | Limita volatilidade mín/máx operável |
| Amplitude do canal | `AMPLITUDE_MIN_PCT`, `AMPLITUDE_MAX_PCT` | 0.40 / 1.0 (%) | Canal muito estreito = ruído |
| Filtro linha 50% | `USAR_FILTRO_50` | OFF | Exige estado do candle pré-rompimento |
| Veto Laya | `LAYA_VETO_ATIVO`, `LAYA_BM25_MIN` | **ON, 12.0** | Consulta a memória System 1 antes de cada entrada (§4) |

Arbitrado no canal: stop estrutural do sinal + alvo (`alvos[0]` ou alvo final se `APEX=true`, com trailing por degraus `LITE38_FATOR_DEGRAU=1.0`).

### 2.2 Políticas de execução/saída (camada sobre a estratégia)

Validadas em 31/out via calibração com trades reais + walk-forward de 45 dias (SOL/BTC/ETH)
+ matriz NET de taxas (KuCoin: taker 0.06%, maker 0.02%; RT MARKET 0.12% vs LIMIT_RETESTE 0.08%).

**Configuração de produção vigente (a partir de 31/out/2026):**

| Parâmetro | Env | Valor | Justificativa |
|---|---|---|---|
| Ativo | `SYMBOL` | SOL/USDT:USDT | Único par com expectativa líquida positiva (ver §5) |
| Tipo de ordem | `TIPO_ORDEM` | **LIMIT_RETESTE** | Ordem limit **maker (postOnly)** no nível rompido (parseado do `motivo` do Sinal, formato `(X.XXX)`). Prazo de reteste `PRAZO_RETESTE_CANDLES=4` (candles 5m); sem reteste → cancela; fill entregue no nível reduz slippage e paga 0.08% RT |
| Política de saída | `POLITICA_SAIDA` | **P90** | Stop fixo `P90_STOP_PCT=2.052%` sobre o preço efetivo de entrada; trailing ativa em `P90_TRIGGER_PCT=+0.859%`; espaçamento do trailing = stop (`P90_ESPACO_MULT=1.0`) |
| Fallback | — | política `LIVE` (padrão sem env) | Stop estrutural do sinal, trailing por degraus da estratégia (comportamento pré-calibração) |

Mecânica de execução detalhada (sem configs, com defaults que preservam o comportamento antigo):

- `ciclo()`: se houver **ordem de reteste pendente**, gerencia-a primeiro (scan de velas
  fechadas por touch do nível → `_preencher_reteste`); senão, se posição → `_gerenciar_posicao`;
  senão avalia sinal.
- Pendente persistido na tabela `estado` (chave `pendente`) → sobrevive a restart.
- Fill do reteste cria stop (`criar_stop_loss`) e `Posicao` com stop/trig/espaço sobre o
  **preço efetivo**; no paper o reteste é simulado (sem ordem na exchange).
- Trailing P90 é monotônico (stop nunca recua) e substitui a ordem de stop na exchange
  (nova criada antes da antiga cancelada).

### 2.3 Risco e circuit breaker (sempre ativos, independente de estratégia)

- `GerenciadorRisco` (`core/risk.py`): `RISCO_POR_TRADE_PCT`, `ALAVANCAGEM_MAX`,
  `MAX_POSICOES_ABERTAS`, `PERDA_DIARIA_MAX_PCT`, `RR_MINIMO`.
- Dimensionamento: `FRACAO_BANCA` (fração do saldo livre por posição × alavancagem) —
  nesse modo o tamanho é por fração, e 1R ≈ stop_pct × fração_banca × alavancagem.
- Circuit breaker: `CB_JANELA_HORAS=24`, `CB_MAX_LOSSES=1` → pausa `CB_PAUSA_HORAS=6`
  novas entradas após a 1ª perda na janela (`core/circuit_breaker.py` lógica avançada +
  `bot._registrar_circuit_breaker`).

---

## 3. Mecanismo do bot (fluxo ponta a ponta)

```
loop (LOOP_INTERVAL_SEC):
  ┌─ haver ordem de reteste pendente? → escanear fill pelo candle 5m → criar posição ──┐
  │  haver posição?  → consultar stop na exchange → trailing → alvo/stop manual         │
  │  circuit breaker suspenso? → não operar                                            │
  │  novo candle 5m fechado? → estrategia.avaliar(df)                                  │
  │     └─ Sinal → risco.validar_sinal → LAYA VETO → execução MARKET ou LIMIT_RETESTE   │
  └─ em qualquer fechamento de posição → _encerrar → PnL → circuit breaker → telemetria │
```

Ordem de decisão de um sinal (o LLM deve entender a precedência):
1. `validar_sinal` (risco: direção, RR, posições, perda diária).
2. `is_action_vetoed` (Laya; BM25 sobre memória de erros/playbooks).
3. Sizing (`fracao_banca` ou `calcular_quantidade`).
4. Colocação: `market` imediata OU pendência `limit postOnly` no nível rompido.
5. Stop always-first: ordem de stop criada antes de qualquer cancelamento.

Dados do papel/mode: `PAPER` não setado = live (com `PAPER_CAPITAL/PAPER_TAXA_PCT/PAPER_SLIPPAGE_PCT` para simulador).

---

## 4. Laya System 1 — uso para inferência e aprendizado

Arquivos (repo e VM):

- `core/laya_telemetry.py` → `LayaTradeFeedbackCollector` singleton `default_telemetry_collector`.
- Memória em JSONL: `data/laya_trade_feedback.jsonl` (resultados por trade) e
  `data/laya_training_dataset.jsonl` (treino).
- Índice/playbooks: construídos in-memory sobre o JSONL (não há arquivo separado de engine).

Como o live usa:

- **Inferência (veto)**: `is_action_vetoed(prompt, min_bm25)` no `_processar_sinal`.
  Match BM25 ≥ `LAYA_BM25_MIN` contra padrões de falha/playbooks ⇒ entrada bloqueada e
  registrada em `sinais` com motivo `Veto Laya System 1`.
- **Feedback**: cada `_encerrar` grava `TradeOutcome` (PnL, MFE/MAE, exit_reason, execução,
  strategy_id, mfe/mae %, reward_score, regime, ...) no JSONL — memória cresce em produção
  automaticamente.

Re-ingestão calibrada (offline): `backtests/calibrador_trades_reais.py`
(reconstrói trades reais com ordens + velas para MFE/MAE e re-alimenta a memória;
`--resync` para dedupe por `trade_id=real_apex_<ordem_entrada_id>`). Um registro
de playbook válido como esperado praticamente: um prompt de rompimento em SOL deve
retornar veto `True` com motivo `[STRAT_APEX_DINAMICS] ...` (testado 31/out).

---

## 5. Histórico de decisão técnica (por que a config atual)

Toda a validação partiu de 18 trades reais do live (14–29/out) recarregados via
`ordens_export.json` + `bot.log` + velas 5m. Campanha:

1. **Calibração oficial** → política sugerida P90 para SOL.
2. **Walk-forward 45d** (entradas geradas pela própria `EstrategiaFimathe`, SOL+BTC+ETH),
   três bases não-comparáveis entre si — leia cada bloco isoladamente:

   **A. Bruto (sem taxas, sem KER)** — primeira validação:
   | Política | Total R |
   |---|---|
   | LIVE (stop estrutural, MARKET) | -32.4R |
   | P90 | -5.4R |
   | HIBRIDA (stop 0.60%, trailing) | -10.6R |

   **B. Bruto (sem taxas, com KER 0.25)** — KER curinga o sinal:
   | Política | Total R |
   |---|---|
   | LIVE | -26.4R |
   | P90 | -5.1R |
   | HIBRIDA | **-0.6R (breakeven agregado)** |

   Com KER 0.35 tudo piora (P90 -3.0R; HIBRIDA -13.7R): 0.35 overfiltra e mata o momentum.

   **C. NET de taxas, KER 0.25** → `validacao_fees_ordens.py`:
   | Config | Total R líquido | SOL |
   |---|---|---|
   | LIVE (MARKET) | -131.4R | -38.8R |
   | P90 (MARKET) | -21.8R | — |
   | HIBRIDA (MARKET) | -75.6R | — |
   | **P90 (LIMIT_RETESTE)** | -11.4R agregado | **+4.31R** |
   | HIBRIDA (LIMIT_RETESTE) | -50.2R | — |

3. Conclusões duradouras: **BTC fora do motor** (negativo em qualquer config);
   **stop curto (híbrida) inviável** pelo custo; **KER 0.25** filtra serrilhado sem matar
   momentum; **entradas maker (LIMIT_RETESTE)** são a alavanca de custo principal.
4. Expectativa da config vigente: **~+0.06R líquido/trade**, ≈ +2.9R/mês
   (indicativo — regime-dependente; revalidar com dados novos).

Saídas auditadas em `data/validacao_fees_ordens.json`,
`data/validacao_walkforward_politica*.json`, `data/simulacao_politica_*.json`,
`data/calibracao_trades_reais.json` (VM e local).

---

## 6. Estrutura de diretórios (essencial)

```
cryptobot/
├── main.py                    # bot live: loop descendente descrito em §3
├── core/
│   ├── exchange.py            # adapter CCXT: KuCoin spot/futures, paper, taxas
│   ├── risk.py                # GerenciadorRisco / ParametrosRisco
│   ├── circuit_breaker.py     # lógica avançada de CB
│   ├── paper.py               # SimuladorPaper (ordens a mercado + stops)
│   └── laya_telemetry.py      # Laya System 1 (veto + telemetria)
├── strategies/
│   ├── strategy.py            # EstrategiaFimathe = Apex Dinamics (sinais)
│   ├── fimathe.py             # motor matemático (grade/ATR/calibração/KER)
│   ├── base.py                # BaseStrategy, Direcao, Sinal, StrategyRegistry
│   └── nautilus_apex_strategy.py  # estratégia para o motor Nautilus (§7)
├── backtests/
│   ├── calibrador_trades_reais.py         # reconstrução Laya de trades reais
│   ├── validacao_walkforward_politica.py  # walk-forward de saídas (--ker, --ker-minimo)
│   ├── validacao_fees_ordens.py           # matriz MARKET vs LIMIT_RETESTE NET de taxas
│   ├── simular_politica_calibrada.py      # antes/depois por trade
│   ├── simular_politica_hibrida.py        # grid híbrida
│   ├── smoke_exec_limit_reteste.py        # smoke test de execução (sem rede)
│   └── apex_dinamics/                     # harnesses Nautilus e backtests 5m KuCoin
│       ├── backtest_kucoin_7dias_top4_10x.py  # datafetcher reutilizado por todos
│       ├── gauntlet_optimizer.py              # otimizador exaustivo + métricas
│       └── ... (comparativos, walk_forward 14d, calibrate_individual_pairs)
├── data/                      # bot.db (SQLite), JSONLs Laya, caches de velas,
│                              # outputs de validação, bot.pid, paper_*.json
├── logs/                      # bot.log (rotativo configurável), stdout/stderr live VM
├── tui/                       # terminal dashboard
├── app.py, dashboard.py, fast_dashboard.py  # dashboards (Streamlit)
├── tests/                     # ⚠️ coleta quebrada (§9)
├── research/, docs/, AGENTS.md, INSTRUCTIONS.md
└── scripts VM: start_bot.sh / stop_bot.sh / status_bot.sh (alias dashboard.sh)
```

---

## 7. Motor Nautilus (backtests de alta fidelidade)

- `nautilus-trade/` **vendorizado**: motor usado por `backtests/apex_dinamics/*nautilus*`.
- `strategies/nautilus_apex_strategy.py` (LOCAL; **não existe na VM hoje**): `ApexDinamicsNautilusStrategy`
  com `tipo_ordem="MARKET"/"LIMIT_RETESTE"` (nível de reteste = borda do canal ±
  `reteste_offset_amp×amplitude`), expiração de reteste (`limite_expiracao_velas`),
  ordens via Nautilus `LimitOrder/MarketOrder`.
- ⚠️ Scripts antigos (`nautilus_kucoin_5x.py`, `walk_forward_14dias_top4.py`,
  `run_nautilus_backtest.py`, `gauntlet_optimizer.py`) importam essa strategy:
  **rodar esses na VM quebra** (arquivo ausente lá). No local funcionam.
- Validações de execução de produção (walk-forward P90/LIMIT_RETESTE) foram feitas no
  **harness pandas próprio**, não no Nautilus — os resultados são equivalentes em R.

---

## 8. Deploy / operação na VM (sem dados restritos)

- Alias SSH: `oracle-vm` (definido no `~/.ssh/config` do host local; **nunca citar IP/usuário em commits**).
- Diretório remoto: `~/cryptobot/` com venv em `~/cryptobot/.venv` (Python **3.9**).
- Ciclo de deploy: editar no repo local → compilar (`py_compile`) → `scp` dos arquivo(s)
  alterados → reiniciar `./stop_bot.sh && ./start_bot.sh` → confirmar a linha de
  "Bot Apex Dinamics iniciado ... tipo_ordem/.../KER=..." em `logs/bot.log`.
- VM **não é repositório git**; o repositório canônico está no GitHub (local).
- Config em `~/cryptobot/.env` (nunca versionado). Flags atuais do regime: ver §2.
- **Nunca** enviar ordens, tocar produção ou credenciais sem autorização expressa do usuário.

---

## 9. Armadilhas conhecidas (evite repetir as surpresas)

1. **Python 3.9 na VM**: `X | None` só funciona em anotações porque os arquivos usam
   `from __future__ import annotations`. Em código novo do live, mantenha essa prática.
2. pandas antigo na VM: indexDatetime com granularidade em nanossegundos gera máscara
   vazia ao alinhar por timestamp — alinhe memória de velas da grade com floor `// 300 * 300`
   (padrão usado pelo calibrador); não dependa da comparação direta de índices.
3. **RTK pode truncar saídas de terminal** no host local (filtering): para diagnóstico
   dependente de linhas exatas, use scripts curtos/base64 (padrão adotado) e saída integral.
4. `pytest` amplo **quebra na coleta** (imports legados, dependência `dashboard`,
   árvore vendorizada `nautilus-trade` incompatível) — rode testes direcionados.
5. Quoting de Python via ssh quebra no pwsh: padrão = base64-encode script local →
   `echo <b64> | base64 -d > /tmp/x.py && .venv/bin/python /tmp/x.py`.
6. Taxas consideradas nas validações: KuCoin futures taker 0.06%, maker 0.02%.
7. Dupla-gravação antiga no JSONL do Laya (corrida contaminada + resync): dedupe por
   `trade_id` antes de reprocessar tudo.
8. Encerramento oficial do bot na VM = `./stop_bot.sh` (usa `data/bot.pid`, caso contrário
   `pkill`); iniciar sempre via `./start_bot.sh` (cancela instância anterior e grava o PID).
9. `data/ordens_export.json` é a exportação oficial de ordens do live — fonte de trades
   para calibração (dados pessoais: nunca versionar nem publicar).

---

## 10. Convenções de comunicação e auditoria

- Comunicação em **português**; preservar alertas, números, negações e clareza.
- Antes de executar/sugerir comandos: validar PATH/SO/caminhos (System 1 —
  `system1_audit.py cmd "<comando>" "<raiz>"`); snippets Python/JSON validados
  por compilação antes de entregar ao usuário.
- Ler `README.md`, `INSTRUCTIONS.md` e `docs/ARQUITETURA.md`/`docs/quality.md`/
  `docs/setup.md` antes de mudanças relevantes; diferencie fatos de hipóteses.
- Métodos preferidos de lookup no repositório: motor comportamental `jg` (Jevgrep) e
  `graphify-out/` (grafo: `graphify query "<pergunta>"`; regenerar após mudanças
  estruturais com `graphify update . --no-cluster`).
