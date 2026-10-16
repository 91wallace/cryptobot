"""Backtest de 24 horas no gráfico de 5 minutos (KuCoin Futures)."""
import pandas as pd
from exchange import Exchange
from strategy import EstrategiaFimathe
from backtest import BacktestFimathe

ex = Exchange(market_type="futures", modo="paper")

# 24 horas em candles de 5m = 288 candles (buscamos 350 para ter contexto de pivôs)
print("Baixando 350 candles de 5m da KuCoin Futures...")
df_full = ex.buscar_candles("BTC/USDT:USDT", "5m", limite=350)

# As últimas 24 horas correspondem aos últimos 288 candles
df_24h = df_full.tail(288)

print("=" * 68)
print(" BACKTEST 24 HORAS - GRAFICO 5 MINUTOS (KUCOIN BTC FUTURES)")
print("=" * 68)
print(f"Janela 24h: {df_24h.index[0]} ate {df_24h.index[-1]} ({len(df_24h)} candles)")
print(f"Preco Inicial: {df_24h['open'].iloc[0]:.2f} | Preco Final: {df_24h['close'].iloc[-1]:.2f}")
print(f"Maxima: {df_24h['high'].max():.2f} | Minima: {df_24h['low'].min():.2f}")
print(f"Variacao Liquida do Ativo: {((df_24h['close'].iloc[-1] - df_24h['open'].iloc[0]) / df_24h['open'].iloc[0] * 100):+.2f}%")

alavancagens = [1, 5, 20]

modos = ["auto_adaptativo", "rompimento", "linha_50", "fora_caixinha"]

for modo in modos:
    est = EstrategiaFimathe(
        janela_pivo=3,
        qtd_niveis=3,
        folga_stop=0.10,
        amplitude_min_pct=0.10,  # no 5m amplitudes percentuais são menores
        usar_filtro_50=False,
        modo_stop=modo,
        atr_filtro_ativo=False,
    )
    bt = BacktestFimathe(est, taxa_pct=0.06, slippage_pct=0.03)
    res = bt.executar(df_full)
    
    # Filtra trades abertos nas últimas 24h
    ts_inicio_24h = df_24h.index[0]
    trades_24h = [t for t in res["trades"] if pd.to_datetime(t.indice_entrada) >= ts_inicio_24h]
    
    total = len(trades_24h)
    wins = sum(1 for t in trades_24h if t.pnl_pct > 0)
    win_rate = (wins / total * 100) if total > 0 else 0.0
    pnl_1x = sum(t.pnl_pct for t in trades_24h)
    
    print(f"\n========================================================")
    print(f" MODO: {modo.upper()}")
    print(f"========================================================")
    print(f" Quantidade de Entradas: {total}")
    print(f" Porcentagem de Acerto:  {win_rate:.1f}% ({wins}W / {total - wins}L)")
    print(f" Retorno (Sem alavancar): {pnl_1x:+.2f}%")
    print(f" Retorno (Alavancagem 5x):  {(pnl_1x * 5):+.2f}%")
    print(f" Retorno (Alavancagem 20x): {(pnl_1x * 20):+.2f}%")
    
    if trades_24h:
        print(" Detalhe das Operações:")
        for t in trades_24h:
            print(f"   -> [{t.direcao.upper()}] Entrada: {t.preco_entrada:.2f} ({t.indice_entrada.strftime('%H:%M')}) | Saida: {t.preco_saida:.2f} ({t.indice_saida.strftime('%H:%M')}) [{t.motivo_saida}] | PnL 1x: {t.pnl_pct:+.2f}% | PnL 20x: {(t.pnl_pct * 20):+.2f}%")
