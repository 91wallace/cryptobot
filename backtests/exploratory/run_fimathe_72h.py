"""Backtest Fimathe para 72 horas em candles de 15m.
Calcula quantidade de entradas, winrate e profit acumulado.
"""
from pathlib import Path
import json
import urllib.request
import pandas as pd
import numpy as np

from strategy import EstrategiaFimathe
from backtest import BacktestFimathe
from fimathe import calcular_atr


def fetch_binance_15m_72h(symbol="BTCUSDT"):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=288"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
    
    records = []
    for item in data:
        records.append({
            "timestamp": pd.to_datetime(item[0], unit="ms", utc=True),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
        })
    df = pd.DataFrame(records).set_index("timestamp")
    df["atr_14"] = calcular_atr(df, 14)
    return df


def executar_comparativo_fimathe():
    df = fetch_binance_15m_72h("BTCUSDT")
    total_candles = len(df)
    inicio = df.index[0]
    fim = df.index[-1]

    print("=" * 65)
    print("      BACKTEST DA SUA ESTRATÉGIA FIMATHE (72H / 15M - BTCUSDT)")
    print("=" * 65)
    print(f"Candles avaliados : {total_candles} (15m = 72 horas exatas)")
    print(f"Período           : {inicio} até {fim}")
    print(f"Preço Inicial     : {df['close'].iloc[0]:.2f} USDT")
    print(f"Preço Final       : {df['close'].iloc[-1]:.2f} USDT")
    print("=" * 65)

    modos = ["linha_50", "fora_caixinha", "rompimento", "auto_adaptativo"]
    
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
        bt = BacktestFimathe(est, taxa_pct=0.04, slippage_pct=0.02)  # taxas padrão maker/taker spot/futuros
        res = bt.executar(df)

        print(f"\n>>> MODO DE STOP: {modo.upper()}")
        print(f"   Quantidade de Entradas : {res['total_trades']}")
        print(f"   Vitórias (Wins)        : {res['wins']}")
        print(f"   Derrotas (Losses)      : {res['losses']}")
        print(f"   Winrate (Taxa Acerto)  : {res['win_rate_pct']:.2f}%")
        print(f"   Profit Acumulado (%)   : {res['retorno_total_pct']:+.2f}%")
        print(f"   Profit Factor          : {res['profit_factor']:.2f}")
        print(f"   Max Drawdown           : {res['max_drawdown_pct']:.2f}%")

        if res["trades"]:
            print("   --- Trades Realizados ---")
            for t in res["trades"]:
                print(f"    • [{t.direcao.upper()}] Entrada: {t.preco_entrada:.2f} ({t.indice_entrada.strftime('%d/%m %H:%M')}) -> Saída: {t.preco_saida:.2f} ({t.indice_saida.strftime('%d/%m %H:%M')}) | {t.motivo_saida.upper()} | PnL: {t.pnl_pct:+.2f}%")
        else:
            print("   Nenhum trade acionado neste modo durante as 72h.")


if __name__ == "__main__":
    executar_comparativo_fimathe()
