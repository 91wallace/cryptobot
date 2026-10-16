"""Validação da Carteira Adaptativa (Momentum Taker no SOL/AVAX + Maker Reteste no ETH/NEAR)."""
import sys
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from backtests.apex_dinamics.gauntlet_optimizer import avaliar_candidato_nautilus, preparar_barras_nautilus
from backtests.apex_dinamics.backtest_7dias_top4_10x import fetch_binance_futures_5m_7dias
from backtests.apex_dinamics.run_nautilus_backtest import criar_instrumento_perp
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.data import BarType

venue = Venue("BINANCE")
configs = [
    ("SOLUSDT", "MARKET", 3, 0.30, 0.75),
    ("AVAXUSDT", "MARKET", 3, 0.30, 0.75),
    ("ETHUSDT", "LIMIT_RETESTE", 3, 0.30, 0.75),
    ("NEARUSDT", "LIMIT_RETESTE", 4, 0.35, 0.75),
]
resumo = []
for p, t_ord, piv, amp, deg in configs:
    df = fetch_binance_futures_5m_7dias(p, target_candles=2016)
    inst = criar_instrumento_perp(p, float(df["close"].mean()))
    bt = BarType.from_str(f"{inst.id}-5-MINUTE-LAST-EXTERNAL")
    bars = preparar_barras_nautilus(df, inst, bt)
    r = avaliar_candidato_nautilus(
        symbol=p, bars=bars, instrument=inst, bar_type=bt, venue_binance=venue,
        pivo=piv, amp_min=amp, degrau=deg, tipo_ordem=t_ord, run_id=f"{p}-ADAPT"
    )
    pnl = r["saldo_final"] - 1000.0
    resumo.append({
        "Par": p,
        "Modo": t_ord,
        "Retorno (%)": f"{r['retorno_pct']:+.2f}%",
        "PnL ($)": f"${pnl:+,.2f}",
        "Sharpe": f"{r['sharpe']:.2f}",
        "Max DD (%)": f"{r['max_dd_pct']:.2f}%",
        "Trades": r["trades"],
        "_pnl": pnl,
    })

df_res = pd.DataFrame(resumo)
print("\n" + "="*80)
print("🏆 RESULTADO CARTEIRA ADAPTATIVA NO NAUTILUS TRADER (7 DIAS)")
print("="*80)
print(df_res.drop(columns=["_pnl"]).to_string(index=False))
tot = sum(x["_pnl"] for x in resumo)
print(f"\n💵 Total Carteira: ${tot:+,.2f} ({tot/1000*100:+.2f}%)\n")
