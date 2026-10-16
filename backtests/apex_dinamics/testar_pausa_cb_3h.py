"""Comparativo de Pausa do Circuit Breaker: 6 Horas vs 3 Horas.
Testa na estratégia 'Rompimento Trailingstop Lite-3.8' (SOL/USDT:USDT KuCoin Futures 5x):
  - Cenário A: Pausa padrão de 6.0 horas após 2 stops
  - Cenário B: Pausa reduzida para 3.0 horas após 2 stops
Período: 30 dias (8.640 velas de 5m) via Nautilus Trader
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


def testar_pausa_circuit_breaker():
    print("\n" + "=" * 90)
    print(" ⚔️ TESTE COMPARATIVO: CIRCUIT BREAKER DE 6 HORAS vs 3 HORAS (NAUTILUS TRADER)")
    print("=" * 90)
    print("Estratégia    : Rompimento Trailingstop Lite-3.8")
    print("Par           : SOL/USDT:USDT (KuCoin Futures)")
    print("Alavancagem   : 5x")
    print("Período       : 30 Dias (1 Mês - ~8.640 velas de 5m)")
    print("-" * 90)

    symbol_kc = "SOL/USDT:USDT"
    df = fetch_kucoin_futures_5m_dias(symbol_kc, dias=30)

    preco_medio = float(df["close"].mean())
    inst = criar_instrumento_kucoin_perp("SOLUSDT", preco_medio)
    bt = BarType.from_str(f"{inst.id}-5-MINUTE-LAST-EXTERNAL")
    bars = preparar_barras_nautilus(df, inst, bt)
    venue_kc = Venue("KUCOIN")

    cenarios = [
        ("6 Horas (Padrão Atual)", 6.0),
        ("3 Horas (Pausa Reduzida)", 3.0),
    ]

    resultados = []
    for nome, horas_pausa in cenarios:
        print(f"\nRodando simulação com pausa de {horas_pausa}h após 2 stops...")
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
            cb_pausa_horas=horas_pausa,
            alavancagem=5,
            run_id=f"CB-{int(horas_pausa)}H",
        )
        pnl = r["saldo_final"] - 1000.0
        resultados.append({
            "Pausa do Circuit Breaker": nome,
            "Retorno (%)": f"{r['retorno_pct']:+.2f}%",
            "Lucro Líquido ($)": f"${pnl:+,.2f}",
            "Sharpe Ratio": f"{r['sharpe']:.2f}",
            "Profit Factor": f"{r['profit_factor']:.2f}",
            "Max Drawdown (%)": f"{r['max_dd_pct']:.2f}%",
            "Total de Trades": r["trades"],
            "Winrate (%)": f"{r['winrate']:.1f}%",
            "_pnl": pnl,
        })

    df_res = pd.DataFrame(resultados)
    print("\n" + "=" * 90)
    print(" 🏆 COMPARATIVO CONSOLIDADO: PAUSA DE 6H vs 3H (30 DIAS)")
    print("=" * 90)
    print(df_res.drop(columns=["_pnl"]).to_string(index=False))
    print("=" * 90 + "\n")


if __name__ == "__main__":
    testar_pausa_circuit_breaker()
