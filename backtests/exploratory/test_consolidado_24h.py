"""Backtest comparativo de 24h em candles de 5m: Fimathe Clássico vs Fimathe Consolidado (Inverso)."""
import pandas as pd
from exchange import Exchange
from strategy import EstrategiaFimathe
from fimathe_consolidado import EstrategiaFimatheConsolidado
from backtest import BacktestFimathe

ex = Exchange(market_type="futures", modo="paper")
print("Baixando 350 candles de 5m (KuCoin Futures BTC/USDT:USDT)...")
df_full = ex.buscar_candles("BTC/USDT:USDT", "5m", limite=350)
df_24h = df_full.tail(288)

print("=" * 70)
print(" BACKTEST 24H (5 MINUTOS) - MERCADO FUTUROS (BTC/USDT:USDT)")
print(f" Periodo: {df_24h.index[0]} ate {df_24h.index[-1]}")
print(f" Total Candles: {len(df_24h)} | Range: {df_24h['low'].min():.2f} a {df_24h['high'].max():.2f}")
print("=" * 70)

alavancagem = 20

# 1. Teste Fimathe Consolidado (Inverso / Reversão à Média)
for alvo in ["linha_50", "canal_oposto"]:
    est_rev = EstrategiaFimatheConsolidado(
        janela_pivo=3,
        qtd_niveis=3,
        folga_stop=0.10,
        amplitude_min_pct=0.10,
        alvo_modo=alvo,
    )
    bt_rev = BacktestFimathe(est_rev, taxa_pct=0.06, slippage_pct=0.03)
    res_rev = bt_rev.executar(df_full)
    trades = [t for t in res_rev["trades"] if t.indice_entrada >= df_24h.index[0]]
    
    total = len(trades)
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    win_rate = (wins / total * 100) if total > 0 else 0.0
    pnl_1x = sum(t.pnl_pct for t in trades)
    pnl_20x = pnl_1x * alavancagem
    
    print(f"\n[ESTRATÉGIA CONSOLIDADA (INVERSA) - Alvo: {alvo.upper()}]")
    print(f"  Quantidade de Entradas: {total}")
    print(f"  Porcentagem de Acerto:  {win_rate:.1f}% ({wins}W / {total - wins}L)")
    print(f"  Profit Líquido (1x):    {pnl_1x:+.2f}%")
    print(f"  Profit Líquido (20x):   {pnl_20x:+.2f}%")
    
    for i, t in enumerate(trades, 1):
        print(f"    Trade #{i} [{t.direcao.upper()}]: Entrada {t.preco_entrada:.2f} ({t.indice_entrada.strftime('%H:%M')}) -> Saída {t.preco_saida:.2f} ({t.indice_saida.strftime('%H:%M')}) [{t.motivo_saida}] | PnL 20x: {(t.pnl_pct * 20):+.2f}%")

print("\n" + "=" * 70)
