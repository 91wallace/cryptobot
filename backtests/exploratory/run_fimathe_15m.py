"""Execução e Plotagem do Teste Fimathe Futuros 15m (equivalente ao script MATLAB)."""
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from strategy import EstrategiaFimathe
from backtest import BacktestFimathe

BASE_DIR = Path(__file__).resolve().parent
arquivo_csv = BASE_DIR / "data" / "fimathe_matlab_15m_500.csv"

if not arquivo_csv.exists():
    arquivo_csv = BASE_DIR / "data" / "fimathe_matlab_15m.csv"

df = pd.read_csv(arquivo_csv)
df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
df = df.set_index("timestamp")

total_candles = len(df)
idx_2h = df.tail(8)

print("=" * 60)
print(" EXECUÇÃO DO TESTE FIMATHE FUTUROS 15M (KUCOIN BTC/USDT:USDT)")
print("=" * 60)
print(f"Total de candles analisados: {total_candles}")
print(f"Período: {df.index[0]} até {df.index[-1]}")
print("\n--- DETALHAMENTO DA JANELA DAS ÚLTIMAS 2 HORAS (8 candles) ---")
print(idx_2h[["open", "high", "low", "close", "atr_14"]])

# Execução da estratégia nos 3 modos
modos = ["fora_caixinha", "linha_50", "rompimento"]
resultados = {}
for modo in modos:
    est = EstrategiaFimathe(
        janela_pivo=3,
        qtd_niveis=3,
        folga_stop=0.10,
        amplitude_min_pct=0.20,
        usar_filtro_50=False,
        modo_stop=modo,
        atr_filtro_ativo=False,
    )
    bt = BacktestFimathe(est, taxa_pct=0.10, slippage_pct=0.05)
    res = bt.executar(df)
    resultados[modo] = res

    print(f"\n[Modo: {modo.upper()}]")
    print(f"  Total Trades:     {res['total_trades']}")
    print(f"  Taxa de Acerto:   {res['win_rate_pct']:.1f}% ({res['wins']}W / {res['losses']}L)")
    print(f"  Retorno Total:    {res['retorno_total_pct']:+.2f}%")
    print(f"  Profit Factor:    {res['profit_factor']:.2f}")
    print(f"  Max Drawdown:     {res['max_drawdown_pct']:.2f}%")
    for t in res["trades"]:
        print(f"    -> [{t.direcao.upper()}] Entrada: {t.preco_entrada:.2f} ({t.indice_entrada}) | Saída: {t.preco_saida:.2f} ({t.indice_saida}) [{t.motivo_saida}] PnL Líquido: {t.pnl_pct:+.2f}%")

# Geração do gráfico equivalente ao MATLAB
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios": [2.5, 1]})

ax1.plot(df.index, df["close"], color="#222222", linewidth=1.2, label="BTC Futures (Close)")
ax1.set_title("Backtest Fimathe Futuros (15 min) - KuCoin BTC/USDT:USDT", fontsize=13, fontweight="bold")
ax1.set_ylabel("Preço (USDT)")
ax1.grid(True, linestyle="--", alpha=0.5)

# Destaque das últimas 2 horas
ax1.axvspan(idx_2h.index[0], idx_2h.index[-1], color="yellow", alpha=0.25, label="Últimas 2 horas")

# Plot dos trades executados (Modo fora_caixinha 1:1)
for t in resultados["fora_caixinha"]["trades"]:
    ts_in = pd.to_datetime(t.indice_entrada)
    ts_out = pd.to_datetime(t.indice_saida)
    cor = "green" if t.direcao == "buy" else "red"
    marker = "^" if t.direcao == "buy" else "v"
    ax1.scatter([ts_in], [t.preco_entrada], color=cor, s=100, marker=marker, zorder=5, label=f"Entrada {t.direcao.upper()}")
    ax1.scatter([ts_out], [t.preco_saida], color="blue", s=80, marker="o", zorder=5, label=f"Saída ({t.motivo_saida})")

ax1.legend(loc="upper left")

# Gráfico de ATR
ax2.plot(df.index, df["atr_14"], color="#800080", linewidth=1.1, label="ATR (14 períodos)")
ax2.set_title("Volatilidade Intra-Day (ATR 14)", fontsize=11)
ax2.set_xlabel("Data/Hora (UTC)")
ax2.set_ylabel("ATR (USDT)")
ax2.grid(True, linestyle="--", alpha=0.5)
ax2.legend(loc="upper left")

plt.tight_layout()
caminho_grafico = BASE_DIR / "data" / "grafico_fimathe_15m.png"
caminho_grafico.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(caminho_grafico, dpi=150)
print(f"\nGráfico salvo com sucesso em: {caminho_grafico}")
