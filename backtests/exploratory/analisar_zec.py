from run_trailing_fimathe_10pares import fetch_binance_futures_5m, BacktestFimatheTrailingStop
from strategy import EstrategiaFimathe, detectar_pivos, identificar_canal_referencia
from fimathe import calcular_atr, calcular_metricas_rompimento
import pandas as pd
import numpy as np

df = fetch_binance_futures_5m('ZECUSDT')
est = EstrategiaFimathe(janela_pivo=3, qtd_niveis=4, folga_stop=0.10, amplitude_min_pct=0.20, usar_filtro_50=False, modo_stop='fora_caixinha', romper_no_canal=True)
bt = BacktestFimatheTrailingStop(est)
res = bt.executar(df)

print(f"Total Trades ZEC: {res['total_trades']}")
print(f"Resultado: {res['wins']}W / {res['losses']}L | PnL: {res['retorno_total_pct']:+.2f}%")

trades_df = []
for idx, t in enumerate(res['trades'], 1):
    sub = df.loc[:t.indice_entrada]
    m = calcular_metricas_rompimento(sub)
    atr = float(calcular_atr(sub, 14).iloc[-1])
    atr_pct = (atr / float(sub['close'].iloc[-1])) * 100
    
    # Razão de reversão de direção (quantas vezes alternou Buy/Sell)
    trades_df.append({
        "n": idx,
        "direcao": t.direcao,
        "entrada": t.preco_entrada,
        "data": t.indice_entrada.strftime("%d/%m %H:%M"),
        "saida": t.motivo_saida,
        "pnl": t.pnl_pct,
        "dur": t.duracao_candles * 5,
        "rvol": m["rvol"],
        "ker": m["ker"],
        "atr_pct": atr_pct,
    })

tdf = pd.DataFrame(trades_df)
print(tdf.to_string(index=False))
