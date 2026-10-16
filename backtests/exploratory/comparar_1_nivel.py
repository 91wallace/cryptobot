"""Comparativo de Rompimento: 1 Nível (Canal de Referência) vs 2 Níveis (Zona Neutra)
Executa em paralelo nos 5 pares de maior volume da Binance Futures em velas de 5m (72h).
"""
import concurrent.futures
import pandas as pd
from run_multi_futures_5m import fetch_binance_futures_5m
from strategy import EstrategiaFimathe
from backtest import BacktestFimathe


def simular_modo(romper_1_nivel: bool, dfs: dict[str, pd.DataFrame]):
    resultados = []
    todos_trades = []
    
    for sym, df in dfs.items():
        est = EstrategiaFimathe(
            janela_pivo=3,
            qtd_niveis=3,
            folga_stop=0.10,
            amplitude_min_pct=0.15,
            usar_filtro_50=False,
            modo_stop="fora_caixinha",
            atr_filtro_ativo=False,
            romper_no_canal=romper_1_nivel,
        )
        bt = BacktestFimathe(est, taxa_pct=0.04, slippage_pct=0.01)
        res = bt.executar(df)
        
        resultados.append({
            "symbol": sym,
            "trades": res["total_trades"],
            "wins": res["wins"],
            "losses": res["losses"],
            "win_rate": res["win_rate_pct"],
            "pnl": res["retorno_total_pct"],
            "profit_factor": res["profit_factor"],
            "max_dd": res["max_drawdown_pct"],
        })
        for t in res["trades"]:
            todos_trades.append((sym, t))
            
    return resultados, todos_trades


def main():
    pares = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "XRPUSDT"]
    print("=" * 86)
    print("    COMPARATIVO: 1 NÍVEL DE ROMPIMENTO (CANAL) VS 2 NÍVEIS (ZONA NEUTRA)")
    print("=" * 86)
    print(f"Pares avaliados: {', '.join(pares)} | Janela: 72 horas em velas de 5m")
    print("Baixando dados mais recentes da Binance Futures...\n")

    dfs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        fut_map = {executor.submit(fetch_binance_futures_5m, p): p for p in pares}
        for f in concurrent.futures.as_completed(fut_map):
            p = fut_map[f]
            dfs[p] = f.result()
            print(f"  [OK] {p}: {len(dfs[p])} velas")

    res_1niv, trades_1niv = simular_modo(romper_1_nivel=True, dfs=dfs)
    res_2niv, trades_2niv = simular_modo(romper_1_nivel=False, dfs=dfs)

    print("\n" + "=" * 86)
    print(f"{'PAR':<10} | {'1 NÍVEL (W/L)':<16} | {'1 NÍV PNL':<10} | {'2 NÍVEIS (W/L)':<16} | {'2 NÍV PNL':<10}")
    print("-" * 86)

    for p in pares:
        r1 = next(r for r in res_1niv if r["symbol"] == p)
        r2 = next(r for r in res_2niv if r["symbol"] == p)
        
        s1 = f"{r1['trades']}t ({r1['wins']}W/{r1['losses']}L)"
        s2 = f"{r2['trades']}t ({r2['wins']}W/{r2['losses']}L)"
        print(f"{p:<10} | {s1:<16} | {r1['pnl']:>+8.2f}% | {s2:<16} | {r2['pnl']:>+8.2f}%")

    print("-" * 86)
    t1_t = sum(r["trades"] for r in res_1niv)
    t1_w = sum(r["wins"] for r in res_1niv)
    t1_l = sum(r["losses"] for r in res_1niv)
    pnl1 = sum(r["pnl"] for r in res_1niv)
    wr1 = (t1_w / t1_t * 100) if t1_t > 0 else 0

    t2_t = sum(r["trades"] for r in res_2niv)
    t2_w = sum(r["wins"] for r in res_2niv)
    t2_l = sum(r["losses"] for r in res_2niv)
    pnl2 = sum(r["pnl"] for r in res_2niv)
    wr2 = (t2_w / t2_t * 100) if t2_t > 0 else 0

    s1_tot = f"{t1_t}t ({t1_w}W/{t1_l}L)"
    s2_tot = f"{t2_t}t ({t2_w}W/{t2_l}L)"
    print(f"{'TOTAL':<10} | {s1_tot:<16} | {pnl1:>+8.2f}% | {s2_tot:<16} | {pnl2:>+8.2f}%")
    print(f"{'WINRATE':<10} | {wr1:>14.1f}% | {'---':<10} | {wr2:>14.1f}% | {'---':<10}")
    print("=" * 86)

    print("\n--- TRADES DETALHADOS NO MODO 1 NÍVEL DE ROMPIMENTO ---")
    if trades_1niv:
        for sym, t in trades_1niv:
            dur = t.duracao_candles * 5
            print(f" • [{sym}] {t.direcao.upper():<4} Ent: {t.preco_entrada:<9.4f} ({t.indice_entrada.strftime('%d/%m %H:%M')}) -> Saída: {t.preco_saida:<9.4f} ({t.indice_saida.strftime('%d/%m %H:%M')}) | {t.motivo_saida.upper()} | Dur: {dur}m | PnL: {t.pnl_pct:+.2f}%")
    else:
        print(" Nenhum trade disparado no modo de 1 nível.")


if __name__ == "__main__":
    main()
