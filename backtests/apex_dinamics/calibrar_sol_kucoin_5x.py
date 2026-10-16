import os
import sys
import pandas as pd
from decimal import Decimal

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "strategies")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "core")))

from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.data import BarType
from backtests.apex_dinamics.gauntlet_optimizer import avaliar_candidato_nautilus, preparar_barras_nautilus
from backtests.apex_dinamics.backtest_kucoin_7dias_top4_10x import fetch_kucoin_futures_5m_7dias
from backtests.apex_dinamics.nautilus_kucoin_5x import criar_instrumento_kucoin_perp

def testar_sol_kucoin():
    print("Baixando dados de SOL/USDT:USDT da KuCoin Futures...")
    df = fetch_kucoin_futures_5m_7dias("SOL/USDT:USDT", 2016)
    inst = criar_instrumento_kucoin_perp("SOLUSDT", float(df["close"].mean()))
    bt = BarType.from_str(f"{inst.id}-5-MINUTE-LAST-EXTERNAL")
    bars = preparar_barras_nautilus(df, inst, bt)
    venue_kc = Venue("KUCOIN")

    print("\n" + "="*80)
    print("🎯 OTIMIZAÇÃO NAUTILUS TRADER: SOL/USDT NA KUCOIN (5X ALAVANCAGEM)")
    print("="*80)

    resultados = []
    for pivo in [3, 4, 5]:
        for amp in [0.20, 0.30, 0.40, 0.50]:
            for deg in [0.75, 1.0, 1.25]:
                r = avaliar_candidato_nautilus(
                    symbol="SOLUSDT",
                    bars=bars,
                    instrument=inst,
                    bar_type=bt,
                    venue_binance=venue_kc,
                    pivo=pivo,
                    amp_min=amp,
                    degrau=deg,
                    tipo_ordem="MARKET",
                    alavancagem=5,
                    run_id=f"SOL-{pivo}-{int(amp*100)}-{int(deg*100)}"
                )
                pnl = r["saldo_final"] - 1000.0
                resultados.append({
                    "Pivô": pivo,
                    "Amp Min": f"{amp:.2f}%",
                    "Degrau": f"{deg:.2f}x",
                    "Retorno (%)": f"{r['retorno_pct']:+.2f}%",
                    "PnL ($)": f"${pnl:+,.2f}",
                    "Sharpe": f"{r['sharpe']:.2f}",
                    "Max DD (%)": f"{r['max_dd_pct']:.2f}%",
                    "Trades": r["trades"],
                    "Winrate (%)": f"{r['winrate']:.1f}%",
                    "_pnl": pnl
                })

    df_res = pd.DataFrame(resultados).sort_values(by="_pnl", ascending=False)
    print(df_res.drop(columns=["_pnl"]).to_string(index=False))

if __name__ == "__main__":
    testar_sol_kucoin()
