# 🚀 Rompimento Trailingstop Lite-3.8™
### *Manual Operacional & Especificação Técnica Oficial da Estratégia*

---

## 📌 1. Visão Geral e Identidade

* **Nome Oficial:** **Rompimento Trailingstop Lite-3.8**
* **Exchange Primária:** KuCoin Futures (USDT-M)
* **Par Campeão Selecionado:** `SOL/USDT:USDT` (Paridade Oficial)
* **Tempo Gráfico:** **5 Minutos (M5)**
* **Alavancagem Fixa:** **5x** (Margem Isolada ou Cross)
* **Estilo Operacional:** Trend Following Intraday & Scalping Conduzido por Degraus

---

## 🧱 2. Pilares de Entrada (Fimathe Nível 1 Puro)

A estratégia baseia-se na identificação matemática das caixinhas de oscilação do mercado:

1. **Detecção dos Canais (Canal de Referência & Zona Neutra):**
   * Mapeamento de Topos e Fundos via pivôs estruturais calibrados (`janela_pivo = 4`).
   * **Amplitude Mínima Anti-Ruído:** $\text{Amplitude} \ge 0,40\%$ do preço do ativo (elimina micro-canais ruidosos e poupa custos de taxa na KuCoin).
   * **Proteção de Volatilidade Esticada:** Bloqueio de caixinhas desproporcionais via teto de ATR.
2. **Gatilho de Rompimento:**
   * **1 Nível de Rompimento Puro (`romper_no_canal = True`):** A entrada é acionada assim que uma vela fecha fora do canal de referência na direção da tendência confirmada.
   * Direção Bilateral (opera **Compra/Long** e **Venda/Short**).
3. **Stop Loss Inicial Técnico:**
   * Alocado fora da caixinha oposta (zona neutra) com **10% de folga** técnica (`modo_stop = "fora_caixinha"`).

---

## 🪜 3. Condução: Trailing Stop Progressivo em Degraus

O diferencial dinâmico da estratégia: **o stop nunca é estático e acompanha a expansão em degraus elásticos de $1,00 \times \text{Amplitude}$** (calibrado no Nautilus Trader).

* **Zona de Tolerância:** Enquanto o preço não atinge 1 Nível completo ($1,0 \times \text{Amp}$), o Stop Loss inicial é preservado para suportar pullbacks saudáveis.
* **Degrau 1 (Break-even com Lucro):** Quando o preço atinge **$1,0 \times \text{Amp}$**, o Stop salta para travar lucro positivo.
* **Degraus Subsequentes:** A cada $1,0\times$ de avanço ($2,0 \times$, $3,0 \times \dots$), o stop avança acompanhando a máxima/mínima.
* **Alvo Final:** Expansão máxima de até 4 níveis Fimathe.

---

## 🛑 4. Escudo de Proteção: Circuit Breaker Temporal Escalonado

Evita perdas consecutivas em momentos de congestão e falsos rompimentos (*choppiness*):

| Evento de Gatilho | Padrão Detectado | Ação do Bot |
| :--- | :--- | :--- |
| **2 Stops Rápidos** | 2 Losses consecutivos em janela $\le$ 6 horas | Suspensão do par por **6 horas** |
| **Alternância Rápida** | Padrão *Loss / Gain / Loss* em janela $\le$ 4 horas | Suspensão do par por **6 horas** |
| **Bate-e-Volta** | Padrão *Gain / Loss / Gain / Loss* em $\le$ 4 horas | Suspensão do par por **6 horas** |
| **Reincidência** | Novo acionamento após retorno da suspensão | Suspensão ampliada para **24 horas** |

> [!NOTE]
> Saídas conduzidas pelo **Trailing Stop no Lucro** são contabilizadas pelo Circuit Breaker como **Gains**, garantindo que operações lucrativas não acionem bloqueios indevidos.

---

## 💼 5. Gestão de Risco e Configuração (.env)

* **Alavancagem Máxima:** **5x**
* **Ativo Operacional:** `SOL/USDT:USDT` (Exclusivo)
* **Limite de Posições:** **Máximo 1 posição ativa por vez** (sem piramidação)
* **Tempo Gráfico:** `5m`
* **Pivô:** `4`
* **Amplitude Mínima:** `0.40%`
* **Degrau do Trailing:** `1.00x Amplitude`
* **Taxa Referencial:** `0.06%` (Taker KuCoin)

---

## 📊 6. Performance Validada no Nautilus Trader (KuCoin Futures - 5x)

### 🥇 A) Janela Oficial de 30 Dias / 1 Mês (11/09/2026 a 11/10/2026 - 8.640 velas)
* **Rentabilidade Líquida:** **+24,89%**
* **PnL Líquido Total:** **+$248,88 USDT** (Banca $1.000)
* **Saldo Final:** **$1.248,88 USDT**
* **Índice de Sharpe (Anualizado):** **7,38** (Grau Institucional Alto)
* **Rebaixamento Máximo (Max DD):** **8,71%**
* **Total de Trades Executados:** **40 trades** (~1,3 trade/dia)
* **Taxa de Acerto (Winrate):** **47,5%** (19 Wins / 21 Losses)
* **Payoff:** Ganhos elásticos conduzidos pelo Trailing Stop superando largamente as perdas curtas.

### B) Janela de 14 Dias (27/09/2026 a 11/10/2026 - 4.032 velas)
* **Rentabilidade Líquida:** **+8,31%** (+$83,05 USDT)
* **Sharpe Ratio:** **6,07**
* **Max DD:** **8,71%**
* **Winrate:** **52,9%** (17 trades)

### C) Janela Curta de 7 Dias (04/10/2026 a 11/10/2026 - 2.016 velas)
* **Rentabilidade Líquida:** **+11,95%** (+$119,45 USDT)
* **Sharpe Ratio:** **14,34**
* **Max DD:** **4,24%**
* **Winrate:** **50,0%** (8 trades)
