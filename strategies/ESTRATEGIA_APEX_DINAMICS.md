# 🛡️ APEX DINAMICS™
### *Manual Operacional & Especificação Técnica da Estratégia*

---

## 📌 1. Visão Geral e Identidade

* **Nome Oficial:** **Apex Dinamics**
* **Mercado Alvo:** Criptoativos (Binance USDT-M Futures)
* **Tempo Gráfico:** **5 Minutos (M5)**
* **Estilo Operacional:** Trend Following Intraday & Scalping Conduzido
* **Alavancagem Padrão:** **10x** (Cross ou Margem Isolada)
* **Pares Selecionados:** Portfólio Top Liquidez/Técnica (`AVAXUSDT`, `SOLUSDT`, `NEARUSDT`, `ETHUSDT`)

---

## 🧱 2. Pilares de Entrada (Fimathe Nível 1 Puro)

A estratégia baseia-se na identificação matemática das caixinhas de oscilação do mercado:

1. **Detecção dos Canais (Canal de Referência & Zona Neutra):**
   * Mapeamento de Topos e Fundos via pivôs curtos (`janela_pivo = 3`).
   * **Amplitude Mínima Calibrada:** $\text{Amplitude} \ge 0,30\%$ do preço do ativo (elimina micro-canais ruidosos e poupa custos de taxa).
2. **Gatilho de Rompimento:**
   * **1 Nível de Rompimento (`romper_no_canal = True`):** A entrada é acionada assim que uma vela fecha fora do canal de referência na direção da tendência.
   * Direção Bilateral (opera **Compra** e **Venda**).
3. **Stop Loss Inicial:**
   * Alocado fora da caixinha oposta (zona neutra) com **10% de folga** técnica (`modo_stop = "fora_caixinha"`).

---

## 🪜 3. Condução: Trailing Stop Progressivo Fimathe (Subníveis)

O diferencial dinâmico da estratégia: **o stop nunca é estático e acompanha a expansão em degraus elásticos de $0,75 \times \text{Amplitude}$** (calibrado no Gauntlet Loop).

* **Zona de Tolerância:** Enquanto o preço não atinge 1 Nível completo ($1,0 \times \text{Amp}$), o Stop Loss inicial é preservado para suportar pullbacks saudáveis.
* **Degrau 1 (Break-even com Lucro):** Quando o preço atinge **$1,0 \times \text{Amp}$**, o Stop salta para **$+0,75 \times \text{Amp}$** (lucro já travado).
* **Degraus Subsequentes:** A cada $0,75\times$ de avanço ($1,75 \times$, $2,50 \times \dots$), o stop avança acompanhando a máxima.
* **Alvo Final:** Expansão máxima de até 3 a 4 níveis Fimathe.

---

## 🛑 4. Escudo de Proteção: Circuit Breaker Temporal Escalonado

Evita perdas consecutivas em dias de congestão e falsos rompimentos (*choppiness*):

| Evento de Gatilho | Padrão Detectado | Ação do Bot |
| :--- | :--- | :--- |
| **2 Stops Rápidos** | 2 Losses consecutivos em janela $\le$ 6 horas | Suspensão do par por **6 horas** |
| **Alternância Rápida** | Padrão *Loss / Gain / Loss* em janela $\le$ 4 horas | Suspensão do par por **6 horas** |
| **Bate-e-Volta** | Padrão *Gain / Loss / Gain / Loss* em $\le$ 4 horas | Suspensão do par por **6 horas** |
| **Reincidência** | Novo acionamento após retorno da suspensão | Suspensão ampliada para **24 horas** |

> [!NOTE]
> Saídas conduzidas pelo **Trailing Stop no Lucro** são contabilizadas pelo Circuit Breaker como **Gains**, garantindo que operações lucrativas não acionem bloqueios indevidos.

---

## 💼 5. Gestão de Carteira e Risco

* **Teto de Posições Simultâneas:** Máximo de **3 posições abertas** ao mesmo tempo.
* **Alocação por Operação:** $\text{Capital por Slot} = \frac{\text{Saldo Atual da Conta}}{3}$ (~$333 em uma banca de $1.000).
* **Fila de Espera:** Caso surja sinal em um 4º par com os 3 slots ocupados, o sinal é descartado sem comprometer a margem da carteira.

---

## 📊 6. Performance Validada (Backtest 7 Dias - Binance Futures)

* **Rentabilidade Líquida:** **+16,85%** em 7 dias
* **Winrate Geral:** **54,88%** (45 Wins / 37 Losses)
* **Maior Vencedor:** `AVAXUSDT` (**+$322,05** | Winrate de **68%**)
