"""Backtest Fimathe para Mercado Futuro (Binance Futures USDT-M)
Período de 72 horas em velas de 5 minutos (864 candles).
Taxas de futuros: Maker 0.02% / Taker 0.05%
"""
from pathlib import Path
import json
import urllib.request
import pandas as pd
import numpy as np

from strategy import EstrategiaFimathe
from backtest import BacktestFimathe
from fimathe import calcular_atr


def fetch_binance_futures_5m_72h(symbol="BTCUSDT"):
    # 72 horas em velas de 5m = 72 * 12 = 864 candles
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=5m&limit=864"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
    
    records = []
    for item in data:
        # Binance Futures Kline format:
        # [0: open_time, 1: open, 2: high, 3: low, 4: close, 5: volume, ...]
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


def executar_backtest_futuros_5m():
    print("Baixando dados de 72h em velas de 5m do Binance Futures (BTCUSDT)...")
    df = fetch_binance_futures_5m_72h("BTCUSDT")
    total_candles = len(df)
    inicio = df.index[0]
    fim = df.index[-1]

    print("=" * 70)
    print("      BACKTEST FIMATHE - MERCADO FUTURO (BINANCE FUTURES 5M)")
    print("=" * 70)
    print(f"Candles avaliados : {total_candles} velas de 5m (72 horas)")
    print(f"Período           : {inicio} até {fim}")
    print(f"Preço Abertura    : {df['open'].iloc[0]:.2f} USDT")
    print(f"Preço Atual/Fech. : {df['close'].iloc[-1]:.2f} USDT")
    print("Taxas de Futuros  : 0.04% (Taker) + 0.01% Slippage")
    print("=" * 70)

    modos = ["linha_50", "fora_caixinha", "rompimento", "auto_adaptativo"]
    
    for modo in modos:
        # Janela de pivôs adaptada para tempo menor (5m):
        # janela=3 (considera 3 candles pra cada lado para confirmar topos/fundos)
        est = EstrategiaFimathe(
            janela_pivo=3,
            qtd_niveis=3,
            folga_stop=0.10,
            amplitude_min_pct=0.15,  # 0.15% adequado para 5m
            usar_filtro_50=False,
            modo_stop=modo,
            atr_filtro_ativo=False,
        )
        
        # Custos reais de Futuros Binance
        bt = BacktestFimathe(est, taxa_pct=0.04, slippage_pct=0.01)
        res = bt.executar(df)

        print(f"\n>>> MODO DE STOP: {modo.upper()}")
        print(f"   Quantidade de Entradas : {res['total_trades']}")
        print(f"   Vitórias (Wins)        : {res['wins']}")
        print(f"   Derrotas (Losses)      : {res['losses']}")
        print(f"   Winrate (Taxa Acerto)  : {res['win_rate_pct']:.2f}%")
        print(f"   Profit Acumulado Líq.  : {res['retorno_total_pct']:+.2f}%")
        print(f"   Profit Factor          : {res['profit_factor']:.2f}")
        print(f"   Max Drawdown           : {res['max_drawdown_pct']:.2f}%")

        if res["trades"]:
            print("   --- Detalhes dos Trades ---")
            for idx, t in enumerate(res["trades"], 1):
                duracao_min = t.duracao_candles * 5
                print(f"    {idx}. [{t.direcao.upper()}] Ent: {t.preco_entrada:.2f} ({t.indice_entrada.strftime('%d/%m %H:%M')}) -> Saída: {t.preco_saida:.2f} ({t.indice_saida.strftime('%d/%m %H:%M')}) | {t.motivo_saida.upper()} | Duração: {duracao_min}m | PnL Líq: {t.pnl_pct:+.2f}%")


if __name__ == "__main__":
    executar_backtest_futuros_5m()
