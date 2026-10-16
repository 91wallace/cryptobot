"""Backtest Oficial de 30 Dias (1 Mês) da Estratégia 'Rompimento Trailingstop Lite-3.8' no Nautilus Trader.
Configuração:
  - Exchange: KuCoin Futures (USDT-M)
  - Par: SOL/USDT:USDT
  - Período: 30 dias (~8.640 velas de 5m)
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

from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.data import BarType
from backtests.apex_dinamics.gauntlet_optimizer import avaliar_candidato_nautilus, preparar_barras_nautilus
from backtests.apex_dinamics.nautilus_kucoin_5x import criar_instrumento_kucoin_perp
from backtests.apex_dinamics.backtest_lite38_14dias import fetch_kucoin_futures_5m_dias


def executar_backtest_30dias():
    print("\n" + "=" * 90)
    print(" 🚀 BACKTEST OFICIAL 30 DIAS (1 MÊS): ROMPIMENTO TRAILINGSTOP LITE-3.8 (NAUTILUS TRADER)")
    print("=" * 90)
    print("Exchange          : KuCoin Futures (USDT-M)")
    print("Par Selecionado   : SOL/USDT:USDT")
    print("Alavancagem       : 5x")
    print("Pivô / Amp / Deg  : Pivô 4 | Amplitude Mín 0.40% | Degrau 1.00x")
    print("Taxas Simuladas   : Maker 0.02% / Taker 0.06% (KuCoin)")
    print("Capital Inicial   : $1.000,00 USDT")
    print("-" * 90)

    symbol_kc = "SOL/USDT:USDT"
    df = fetch_kucoin_futures_5m_dias(symbol_kc, dias=30)

    preco_medio = float(df["close"].mean())
    inst = criar_instrumento_kucoin_perp("SOLUSDT", preco_medio)
    bt = BarType.from_str(f"{inst.id}-5-MINUTE-LAST-EXTERNAL")
    bars = preparar_barras_nautilus(df, inst, bt)
    venue_kc = Venue("KUCOIN")

    print(f"\nIniciando simulação de 1 mês no Nautilus Trader ({len(bars)} barras de 5m)...")
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
        run_id="LITE3.8-30D",
    )

    pnl = r["saldo_final"] - 1000.0

    print("\n" + "=" * 90)
    print(" 🏆 RESULTADO CONSOLIDADO (30 DIAS / 1 MÊS - KUCOIN FUTURES 5X)")
    print("=" * 90)
    print(f"Estratégia                    : Rompimento Trailingstop Lite-3.8")
    print(f"Ativo                         : SOLUSDT")
    print(f"Período                       : 30 Dias (~8.640 velas de 5m)")
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
    executar_backtest_30dias()
