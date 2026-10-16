"""Backtest de 5 horas (gráfico de 15m) no mercado futuros com alavancagem 20x."""
import pandas as pd
from exchange import Exchange
from strategy import EstrategiaFimathe, Direcao
from backtest import BacktestFimathe

ex = Exchange(market_type="futures", modo="paper")
# Baixa histórico com margem para identificação de pivôs anteriores
df_full = ex.buscar_candles("BTC/USDT:USDT", "15m", limite=60)

# As últimas 5 horas correspondem aos últimos 20 candles de 15m
df_5h = df_full.tail(20)

print("=" * 65)
print(" BACKTEST 5 HORAS - GRAFICO 15M (FUTUROS ALAVANCAGEM 20X)")
print("=" * 65)
print(f"Janela de 5 horas: {df_5h.index[0]} ate {df_5h.index[-1]} (Total: {len(df_5h)} candles)")
print(f"Preco Inicial: {df_5h['open'].iloc[0]:.2f} | Preco Final: {df_5h['close'].iloc[-1]:.2f}")
print(f"Maxima: {df_5h['high'].max():.2f} | Minima: {df_5h['low'].min():.2f}")

alavancagem = 20

# Teste com os modos
for modo in ["auto_adaptativo", "rompimento", "fora_caixinha", "linha_50"]:
    est = EstrategiaFimathe(
        janela_pivo=2,  # sensibilidade para timeframe curto
        qtd_niveis=3,
        folga_stop=0.10,
        amplitude_min_pct=0.15,
        usar_filtro_50=False,
        modo_stop=modo,
        atr_filtro_ativo=False,
    )
    bt = BacktestFimathe(est, taxa_pct=0.06, slippage_pct=0.03)
    
    # Executa com base em todo o contexto para poder identificar pivôs antes da janela
    res = bt.executar(df_full)
    
    # Filtra trades cuja entrada ocorreu dentro da janela das ultimas 5 horas
    ts_inicio_5h = df_5h.index[0]
    trades_5h = [t for t in res["trades"] if pd.to_datetime(t.indice_entrada) >= ts_inicio_5h]
    
    total = len(trades_5h)
    wins = sum(1 for t in trades_5h if t.pnl_pct > 0)
    win_rate = (wins / total * 100) if total > 0 else 0.0
    
    # PnL com alavancagem 20x
    pnl_sem_alav = sum(t.pnl_pct for t in trades_5h)
    pnl_20x = pnl_sem_alav * alavancagem
    
    print(f"\n[Modo: {modo.upper()}]")
    print(f"  Quantidade de Entradas: {total}")
    print(f"  Porcentagem de Acerto:  {win_rate:.1f}% ({wins}W / {total - wins}L)")
    print(f"  Profit (Sem alavancar): {pnl_sem_alav:+.2f}%")
    print(f"  Profit (Alavancagem 20x): {pnl_20x:+.2f}%")
    
    for t in trades_5h:
        pnl_trade_20x = t.pnl_pct * alavancagem
        print(f"    -> [{t.direcao.upper()}] Entrada: {t.preco_entrada:.2f} ({t.indice_entrada}) | Saida: {t.preco_saida:.2f} ({t.indice_saida}) [{t.motivo_saida}] | PnL 20x: {pnl_trade_20x:+.2f}%")
