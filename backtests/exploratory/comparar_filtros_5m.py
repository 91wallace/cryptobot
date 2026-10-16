"""Comparativo Direto: Fimathe Original (Sem Filtros) vs Fimathe Filtrada (Com Filtros)
Executa em paralelo nos 5 pares de maior volume da Binance Futures em velas de 5m (72h).
"""
import concurrent.futures
import pandas as pd
from run_multi_futures_5m import fetch_binance_futures_5m
from strategy import EstrategiaFimathe
from backtest import BacktestFimathe


def simular_config(nome_config: str, params: dict, dfs: dict[str, pd.DataFrame]):
    resultados = []
    todos_trades = []
    
    for sym, df in dfs.items():
        est = EstrategiaFimathe(**params)
        bt = BacktestFimathe(est, taxa_pct=0.04, slippage_pct=0.01)
        res = bt.executar(df)
        
        resultados.append({
            "symbol": sym,
            "trades": res["total_trades"],
            "wins": res["wins"],
            "losses": res["losses"],
            "win_rate": res["win_rate_pct"],
            "pnl": res["retorno_total_pct"],
            "max_dd": res["max_drawdown_pct"],
            "profit_factor": res["profit_factor"],
        })
        for t in res["trades"]:
            todos_trades.append((sym, t))
            
    return resultados, todos_trades


def main():
    pares = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "XRPUSDT"]
    print("=" * 84)
    print("        COMPARATIVO: FIMATHE ORIGINAL VS FIMATHE COM FILTROS (72H / 5M)")
    print("=" * 84)
    print(f"Pares avaliados: {', '.join(pares)}")
    print("Baixando dados mais recentes da Binance Futures...\n")

    dfs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        fut_map = {executor.submit(fetch_binance_futures_5m, p): p for p in pares}
        for f in concurrent.futures.as_completed(fut_map):
            p = fut_map[f]
            dfs[p] = f.result()
            print(f"  [OK] {p}: {len(dfs[p])} velas")

    # Configuração 1: Original (sem filtros rígidos)
    config_original = {
        "janela_pivo": 3,
        "qtd_niveis": 3,
        "folga_stop": 0.10,
        "amplitude_min_pct": 0.15,
        "usar_filtro_50": False,
        "modo_stop": "fora_caixinha",
        "atr_filtro_ativo": False,
    }

    # Configuração 2: Filtrada (Amplitude mínima + Filtro de Volatilidade ATR adaptativo)
    config_filtrada = {
        "janela_pivo": 3,
        "qtd_niveis": 3,
        "folga_stop": 0.10,
        "amplitude_min_pct": 0.40,
        "usar_filtro_50": False,
        "modo_stop": "fora_caixinha",
        "atr_filtro_ativo": True,
        "atr_periodo": 14,
        "fator_min_atr": 1.6,
        "fator_max_atr": 4.0,
    }

    res_orig, trades_orig = simular_config("ORIGINAL", config_original, dfs)
    res_filt, trades_filt = simular_config("FILTRADA", config_filtrada, dfs)

    # Exibição lado a lado por ativo
    print("\n" + "=" * 84)
    print("                 DESEMPENHO DETALHADO POR ATIVO")
    print("=" * 84)
    print(f"{'PAR':<10} | {'ORIGINAL (W/L)':<16} | {'ORIG PNL':<10} | {'FILTRADA (W/L)':<16} | {'FILT PNL':<10}")
    print("-" * 84)

    for i in range(len(pares)):
        sym = pares[i]
        o = next(r for r in res_orig if r["symbol"] == sym)
        f = next(r for r in res_filt if r["symbol"] == sym)
        
        o_wl = f"{o['trades']}t ({o['wins']}W/{o['losses']}L)"
        f_wl = f"{f['trades']}t ({f['wins']}W/{f['losses']}L)"
        
        print(f"{sym:<10} | {o_wl:<16} | {o['pnl']:>+8.2f}% | {f_wl:<16} | {f['pnl']:>+8.2f}%")

    print("-" * 84)
    tot_o_t = sum(r["trades"] for r in res_orig)
    tot_o_w = sum(r["wins"] for r in res_orig)
    tot_o_l = sum(r["losses"] for r in res_orig)
    tot_o_pnl = sum(r["pnl"] for r in res_orig)
    wr_o = (tot_o_w / tot_o_t * 100) if tot_o_t > 0 else 0

    tot_f_t = sum(r["trades"] for r in res_filt)
    tot_f_w = sum(r["wins"] for r in res_filt)
    tot_f_l = sum(r["losses"] for r in res_filt)
    tot_f_pnl = sum(r["pnl"] for r in res_filt)
    wr_f = (tot_f_w / tot_f_t * 100) if tot_f_t > 0 else 0

    tot_o_str = f"{tot_o_t}t ({tot_o_w}W/{tot_o_l}L)"
    tot_f_str = f"{tot_f_t}t ({tot_f_w}W/{tot_f_l}L)"
    print(f"{'TOTAL':<10} | {tot_o_str:<16} | {tot_o_pnl:>+8.2f}% | {tot_f_str:<16} | {tot_f_pnl:>+8.2f}%")
    print(f"{'WINRATE':<10} | {wr_o:>14.1f}% | {'---':<10} | {wr_f:>14.1f}% | {'---':<10}")
    print("=" * 84)

    print("\n--- TRADES DA CONFIGURAÇÃO FILTRADA ---")
    if trades_filt:
        for sym, t in trades_filt:
            dur = t.duracao_candles * 5
            print(f" • [{sym}] {t.direcao.upper():<4} Entrada: {t.preco_entrada:<9.4f} ({t.indice_entrada.strftime('%d/%m %H:%M')}) -> Saída: {t.preco_saida:<9.4f} ({t.indice_saida.strftime('%d/%m %H:%M')}) | {t.motivo_saida.upper()} | Dur: {dur}m | PnL: {t.pnl_pct:+.2f}%")
    else:
        print(" Nenhum trade acionado com os filtros atuais.")


if __name__ == "__main__":
    main()
