"""Backtest Oficial de 14 Dias da Estratégia 'Rompimento Trailingstop Lite-3.8' no Nautilus Trader.
Configuração:
  - Exchange: KuCoin Futures (USDT-M)
  - Par: SOL/USDT:USDT
  - Período: 14 dias (~4.032 velas de 5m)
  - Alavancagem: 5x
  - Pivô: 4
  - Amplitude Mínima: 0.40%
  - Degrau Trailing Stop: 1.00x
  - Taxas KuCoin: 0.02% Maker / 0.06% Taker
"""
import sys
import os
import time
from decimal import Decimal
import pandas as pd
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "strategies")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "core")))

import ccxt
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.data import BarType
from backtests.apex_dinamics.gauntlet_optimizer import avaliar_candidato_nautilus, preparar_barras_nautilus
from backtests.apex_dinamics.nautilus_kucoin_5x import criar_instrumento_kucoin_perp


def fetch_kucoin_futures_5m_dias(symbol: str, dias: int = 14) -> pd.DataFrame:
    """Busca candles de 5m paginados na KuCoin Futures API para o número de dias especificado."""
    target_candles = dias * 288
    kf = ccxt.kucoinfutures({"enableRateLimit": True, "timeout": 25000})
    now_ms = int(time.time() * 1000)
    total_ms = target_candles * 5 * 60 * 1000
    start_ms = now_ms - total_ms

    all_ohlcv = []
    curr_since = start_ms

    print(f"Baixando {target_candles} velas de 5m ({dias} dias) para {symbol} da KuCoin Futures...")
    while len(all_ohlcv) < target_candles:
        try:
            ohlcv = kf.fetch_ohlcv(symbol, timeframe="5m", since=curr_since, limit=200)
            if not ohlcv:
                break
            all_ohlcv.extend(ohlcv)
            curr_since = ohlcv[-1][0] + (5 * 60 * 1000)
            if curr_since >= now_ms:
                break
            time.sleep(0.08)
        except Exception as e:
            time.sleep(0.5)
            break

    unique_map = {item[0]: item for item in all_ohlcv}
    sorted_items = [unique_map[k] for k in sorted(unique_map.keys())]

    if len(sorted_items) > target_candles:
        sorted_items = sorted_items[-target_candles:]

    records = []
    for item in sorted_items:
        records.append({
            "timestamp": pd.to_datetime(item[0], unit="ms", utc=True),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
        })

    df = pd.DataFrame(records).set_index("timestamp")
    print(f"Total carregado: {len(df)} velas de 5m ({df.index[0]} até {df.index[-1]}).")
    return df


def executar_backtest_14dias():
    print("\n" + "=" * 90)
    print(" 🚀 BACKTEST OFICIAL 14 DIAS: ROMPIMENTO TRAILINGSTOP LITE-3.8 (NAUTILUS TRADER)")
    print("=" * 90)
    print("Exchange          : KuCoin Futures (USDT-M)")
    print("Par Selecionado   : SOL/USDT:USDT")
    print("Alavancagem       : 5x")
    print("Pivô / Amp / Deg  : Pivô 4 | Amplitude Mín 0.40% | Degrau 1.00x")
    print("Taxas Simuladas   : Maker 0.02% / Taker 0.06% (KuCoin)")
    print("Capital Inicial   : $1.000,00 USDT")
    print("-" * 90)

    symbol_kc = "SOL/USDT:USDT"
    df = fetch_kucoin_futures_5m_dias(symbol_kc, dias=14)

    preco_medio = float(df["close"].mean())
    inst = criar_instrumento_kucoin_perp("SOLUSDT", preco_medio)
    bt = BarType.from_str(f"{inst.id}-5-MINUTE-LAST-EXTERNAL")
    bars = preparar_barras_nautilus(df, inst, bt)
    venue_kc = Venue("KUCOIN")

    print("\nExecutando simulação de 14 dias no Nautilus Trader...")
    r = avaliar_candidato_nautilus(
        symbol="SOLUSDT",
        bars=bars,
        instrument=inst,
        bar_type=bt,
        venue_binance=venue_kc,
        pivo=4,
        amp_min=0.40,
        degrau=1.00,
        tipo_ordem="MARKET",
        alavancagem=5,
        run_id="LITE3.8-14D",
    )

    pnl = r["saldo_final"] - 1000.0

    print("\n" + "=" * 90)
    print(" 🏆 RESULTADO CONSOLIDADO (14 DIAS DE OPERAÇÃO - KUCOIN FUTURES 5X)")
    print("=" * 90)
    print(f"Estratégia                    : Rompimento Trailingstop Lite-3.8")
    print(f"Ativo                         : SOLUSDT")
    print(f"Período                       : 14 Dias (~4.032 velas de 5m)")
    print(f"Capital Inicial               : $ 1.000,00 USDT")
    print(f"Saldo Final                   : $ {r['saldo_final']:,.2f} USDT")
    print(f"LUCRO LÍQUIDO TOTAL ($)       : $ {pnl:>+,.2f} USDT")
    print(f"RENTABILIDADE TOTAL (%)       : {r['retorno_pct']:>+8.2f}%")
    print(f"Índice de Sharpe (Anualizado) : {r['sharpe']:.2f}")
    print(f"Profit Factor                 : {r['profit_factor']:.2f}")
    print(f"Rebaixamento Máximo (Max DD)  : {r['max_dd_pct']:.2f}%")
    print(f"Total de Trades               : {r['trades']}")
    print(f"Taxa de Acerto (Winrate)      : {r['winrate']:.1f}%")
    print("=" * 90 + "\n")


if __name__ == "__main__":
    executar_backtest_14dias()
