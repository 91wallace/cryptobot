"""Script de teste rápido para gráfico 15m no mercado futuro."""
import pandas as pd
from strategy import EstrategiaFimathe
from backtest import BacktestFimathe

df = pd.read_csv("data/fimathe_matlab_15m_500.csv")
df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
df = df.set_index("timestamp")

print("Candles:", len(df), "| Inicio:", df.index[0], "| Fim:", df.index[-1])
print("\n--- Ultimas 2 horas (8 candles de 15m) ---")
print(df.tail(8)[["open", "high", "low", "close"]])

for modo in ["fora_caixinha", "linha_50", "rompimento"]:
    est = EstrategiaFimathe(janela_pivo=3, qtd_niveis=3, usar_filtro_50=False, modo_stop=modo)
    bt = BacktestFimathe(est)
    res = bt.executar(df)
    print(f"\nModo: {modo} -> Trades: {res['total_trades']}, Retorno: {res['retorno_total_pct']:+.2f}%")
    for t in res["trades"]:
        print(f"  [{t.direcao.upper()}] Entrada: {t.preco_entrada:.2f} ({t.indice_entrada}) -> Saida: {t.preco_saida:.2f} ({t.indice_saida}) [{t.motivo_saida}] PnL: {t.pnl_pct:+.2f}%")
