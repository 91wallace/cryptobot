"""Backtest de 24 horas no gráfico de 15 minutos (Fimathe Consolidado)."""
import pandas as pd
from exchange import Exchange
from fimathe_consolidado import EstrategiaFimatheConsolidado
from backtest import BacktestFimathe

ex = Exchange(market_type="futures", modo="paper")
# 24 horas em 15m = 96 candles
df_full = ex.buscar_candles("BTC/USDT:USDT", "15m", limite=150)
df_24h = df_full.tail(96)

print("=" * 68)
print(" BACKTEST 24H - GRAFICO 15 MINUTOS (FIMATHE CONSOLIDADO)")
print("=" * 68)
print(f"Janela 24h: {df_24h.index[0]} ate {df_24h.index[-1]} ({len(df_24h)} candles)")
print(f"Range BTC: {df_24h['low'].min():.2f} a {df_24h['high'].max():.2f}")

alavancagem = 20

for alvo in ["canal_oposto", "linha_50"]:
    est = EstrategiaFimatheConsolidado(
        janela_pivo=3,
        qtd_niveis=3,
        folga_stop=0.10,
        amplitude_min_pct=0.15,
        alvo_modo=alvo,
    )
    bt = BacktestFimathe(est, taxa_pct=0.06, slippage_pct=0.03)
    res = bt.executar(df_full)
    
    trades = [t for t in res["trades"] if t.indice_entrada >= df_24h.index[0]]
    total = len(trades)
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    win_rate = (wins / total * 100) if total > 0 else 0.0
    pnl_1x = sum(t.pnl_pct for t in trades)
    pnl_20x = pnl_1x * alavancagem
    
    print(f"\n[ALVO: {alvo.upper()}]")
    print(f"  Quantidade de Trades:  {total}")
    print(f"  Taxa de Acerto:        {win_rate:.1f}% ({wins}W / {total - wins}L)")
    print(f"  Profit Liquido (1x):   {pnl_1x:+.2f}%")
    print(f"  Profit Liquido (20x):  {pnl_20x:+.2f}%")
    
    for i, t in enumerate(trades, 1):
        p_20 = t.pnl_pct * alavancagem
        print(f"    Trade #{i} [{t.direcao.upper()}]: Entrada {t.preco_entrada:.2f} ({t.indice_entrada.strftime('%H:%M')}) -> Saida {t.preco_saida:.2f} ({t.indice_saida.strftime('%H:%M')}) [{t.motivo_saida}] | PnL 20x: {p_20:+.2f}%")
