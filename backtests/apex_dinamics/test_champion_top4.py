"""Validação Final do Setup Campeão do Gauntlet Loop nos 4 Pares."""
import sys
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from backtests.apex_dinamics.gauntlet_optimizer import avaliar_candidato_nautilus, preparar_barras_nautilus
from backtests.apex_dinamics.backtest_7dias_top4_10x import fetch_binance_futures_5m_7dias
from backtests.apex_dinamics.run_nautilus_backtest import criar_instrumento_perp
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.data import BarType

pares = ["SOLUSDT", "AVAXUSDT", "NEARUSDT", "ETHUSDT"]
resumo = []
venue = Venue("BINANCE")

print("\n" + "="*80)
print("🏆 VALIDANDO SETUP CAMPEÃO DO GAUNTLET LOOP NOS 4 PARES (7 DIAS)")
print("   Configuração: Pivô 3 | Amp Mín 0.30% | Degrau 0.75x | Ordem Market")
print("="*80)

for p in pares:
    df = fetch_binance_futures_5m_7dias(p, target_candles=2016)
    inst = criar_instrumento_perp(p, float(df["close"].mean()))
    bt = BarType.from_str(f"{inst.id}-5-MINUTE-LAST-EXTERNAL")
    bars = preparar_barras_nautilus(df, inst, bt)

    r = avaliar_candidato_nautilus(
        symbol=p, bars=bars, instrument=inst, bar_type=bt, venue_binance=venue,
        pivo=3, amp_min=0.30, degrau=0.75, tipo_ordem="MARKET", run_id=f"{p}-CHAMP"
    )
    pnl = r["saldo_final"] - 1000.0
    resumo.append({
        "Par": p,
        "Retorno (%)": f"{r['retorno_pct']:+.2f}%",
        "PnL ($)": f"${pnl:+,.2f}",
        "Sharpe": f"{r['sharpe']:.2f}",
        "Max DD (%)": f"{r['max_dd_pct']:.2f}%",
        "Trades": r["trades"],
        "_pnl": pnl,
    })

df_res = pd.DataFrame(resumo)
print("\n" + df_res.drop(columns=["_pnl"]).to_string(index=False))
total_pnl = sum(x["_pnl"] for x in resumo)
print(f"\n💵 Total Carteira (7 Dias): ${total_pnl:+,.2f} ({total_pnl/1000*100:+.2f}%)\n")
