"""Estudo Comparativo de Alavancagens: 1x, 2x, 3x, 5x, 7x e 10x.
Estratégia: Rompimento Trailingstop Lite-3.8
Ativo: SOL/USDT:USDT (KuCoin Futures)
Período: 30 Dias (~8.640 velas de 5m) via Nautilus Trader
Configuração Fixa:
  - 1 Posição por Ativo (sem piramidação)
  - Pivô: 4
  - Amplitude Mínima: 0.40%
  - Degrau Trailing Stop: 1.00x
  - Circuit Breaker: Pausa de 6.0h após 2 stops consecutivos
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

from nautilus_trader.model.identifiers import Venue, InstrumentId, Symbol
from nautilus_trader.model.data import BarType
from nautilus_trader.model.currencies import USDT, Currency
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Price, Quantity, Money
from backtests.apex_dinamics.gauntlet_optimizer import avaliar_candidato_nautilus, preparar_barras_nautilus
from backtests.apex_dinamics.backtest_lite38_14dias import fetch_kucoin_futures_5m_dias


def criar_instrumento_leverage(simbolo_base: str, preco_medio: float, lev: int) -> CryptoPerpetual:
    base_curr = Currency.from_str(simbolo_base.replace("USDT", ""))
    venue_kucoin = Venue("KUCOIN")
    margin_init = Decimal(str(round(1.0 / lev, 4)))
    margin_maint = margin_init / Decimal("2")

    return CryptoPerpetual(
        instrument_id=InstrumentId(Symbol(f"{simbolo_base}-PERP-{lev}X"), venue_kucoin),
        raw_symbol=Symbol(f"{simbolo_base}/USDT:USDT"),
        base_currency=base_curr,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=2,
        size_precision=2,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.01"),
        max_quantity=Quantity.from_str("100000.00"),
        min_quantity=Quantity.from_str("0.01"),
        max_notional=None,
        min_notional=Money(5.00, USDT),
        max_price=Price.from_str("1000000.00"),
        min_price=Price.from_str("0.0001"),
        margin_init=margin_init,
        margin_maint=margin_maint,
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0006"),
        ts_event=0,
        ts_init=0,
    )


def testar_grade_alavancagens():
    print("\n" + "=" * 92)
    print(" 📊 ESTUDO COMPARATIVO DE ALAVANCAGENS NO NAUTILUS TRADER (KUCOIN FUTURES - 30 DIAS)")
    print("=" * 92)
    print("Estratégia    : Rompimento Trailingstop Lite-3.8")
    print("Ativo         : SOL/USDT:USDT (Exclusivo - 1 Posição por Vez)")
    print("Pivô / Amp / D: Pivô 4 | Amplitude Mín 0.40% | Degrau 1.00x | CB 6h")
    print("Alavancagens  : 1x, 2x, 3x, 5x, 7x e 10x")
    print("-" * 92)

    df = fetch_kucoin_futures_5m_dias("SOL/USDT:USDT", dias=30)
    preco_medio = float(df["close"].mean())
    venue_kc = Venue("KUCOIN")

    alavancagens = [1, 2, 3, 5, 7, 10]
    resultados = []

    for lev in alavancagens:
        print(f"\nRodando simulação no Nautilus com alavancagem de {lev}x...")
        inst = criar_instrumento_leverage("SOLUSDT", preco_medio, lev)
        bt = BarType.from_str(f"{inst.id}-5-MINUTE-LAST-EXTERNAL")
        bars = preparar_barras_nautilus(df, inst, bt)

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
            cb_pausa_horas=6.0,
            alavancagem=lev,
            run_id=f"LEV-{lev}X",
        )
        pnl = r["saldo_final"] - 1000.0
        resultados.append({
            "Alavancagem": f"{lev}x",
            "Retorno Líquido (%)": f"{r['retorno_pct']:+.2f}%",
            "Lucro Líquido ($)": f"${pnl:+,.2f}",
            "Saldo Final ($)": f"${r['saldo_final']:,.2f}",
            "Sharpe Ratio": f"{r['sharpe']:.2f}",
            "Profit Factor": f"{r['profit_factor']:.2f}",
            "Max Drawdown (%)": f"{r['max_dd_pct']:.2f}%",
            "Trades": r["trades"],
            "Winrate (%)": f"{r['winrate']:.1f}%",
            "_lev": lev,
        })

    df_res = pd.DataFrame(resultados).sort_values(by="_lev", ascending=True)
    print("\n" + "=" * 92)
    print(" 🏆 QUADRO GERAL COMPARATIVO DE ALAVANCAGEM (30 DIAS / 1 MÊS - SOL)")
    print("=" * 92)
    print(df_res.drop(columns=["_lev"]).to_string(index=False))
    print("=" * 92 + "\n")


if __name__ == "__main__":
    testar_grade_alavancagens()
