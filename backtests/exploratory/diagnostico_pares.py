import numpy as np
import pandas as pd
from run_multi_futures_5m import fetch_binance_futures_5m
from fimathe import calcular_atr

pares = ['NEARUSDT', 'BTCUSDT', 'SOLUSDT', 'XRPUSDT', 'ETHUSDT']

print(f"{'PAR':<10} | {'RET_ATIVO':<10} | {'ATR_PCT':<10} | {'DIREC_KER':<10} | {'VOL_USDT':<14} | {'RANGE_MAX%':<12} | {'RESULTADO'}")
print("-" * 88)

for p in pares:
    df = fetch_binance_futures_5m(p)
    ret_total = ((df['close'].iloc[-1] - df['close'].iloc[0]) / df['close'].iloc[0]) * 100
    
    # ATR médio percentual do ativo
    atr = calcular_atr(df, 14)
    atr_pct = (atr / df['close']).mean() * 100
    
    # Eficiência de tendência (Kaufman Efficiency Ratio - Direcionalidade sem ruído)
    caminho = df['close'].diff().abs().sum()
    deslocamento = abs(df['close'].iloc[-1] - df['close'].iloc[0])
    ker = deslocamento / caminho if caminho > 0 else 0
    
    # Volume total em USDT
    vol_financeiro = (df['volume'] * df['close']).sum()
    
    # Amplitude máxima de preço do período
    range_total = ((df['high'].max() - df['low'].min()) / df['low'].min()) * 100

    res = "+1.21% (2W/0L)" if p == "NEARUSDT" else ("+0.17% (1W/0L)" if p == "BTCUSDT" else ("-0.21% (1W/1L)" if p == "SOLUSDT" else ("-0.32% (1W/3L)" if p == "XRPUSDT" else "-2.24% (1W/4L)")))
    print(f"{p:<10} | {ret_total:>+8.2f}% | {atr_pct:>8.2f}% | {ker:>9.3f} | ${vol_financeiro/1e6:>10.1f}M | {range_total:>10.2f}% | {res}")
