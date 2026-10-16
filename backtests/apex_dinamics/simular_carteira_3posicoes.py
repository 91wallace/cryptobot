"""Simulação com Carteira Compartilhada e Limite de 3 Posições Simultâneas.
Parâmetros:
  - Capital Inicial: $1.000 USDT
  - Máximo de Posições Concorrentes: 3 (se já tiver 3 pares abertos, recusa novos sinais)
  - Alocação por Trade: 33.3% do saldo (ou seja, ~$333 por posição)
  - Alavancagem: testada em 1x, 3x e 5x
  - Circuit Breaker Temporal de 6h ativo por par
  - Trailing Stop Progressivo por níveis
"""
import concurrent.futures
import pandas as pd
import numpy as np

from strategy import Direcao, EstrategiaFimathe
from circuit_breaker import CircuitBreakerPar
from run_circuit_breaker_10pares import fetch_binance_futures_5m


def simular_portfolio_max_posicoes(
    dfs: dict[str, pd.DataFrame],
    capital_inicial: float = 1000.0,
    max_posicoes: int = 3,
    alavancagem: float = 1.0,
    taxa_pct: float = 0.04,
    slippage_pct: float = 0.01,
):
    # 1. Alinhar todos os candles por timestamp global
    todos_ts = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    
    # Estratégia Fimathe 1 Nível
    est = EstrategiaFimathe(
        janela_pivo=3,
        qtd_niveis=4,
        folga_stop=0.10,
        amplitude_min_pct=0.20,
        usar_filtro_50=False,
        modo_stop="fora_caixinha",
        atr_filtro_ativo=False,
        romper_no_canal=True,
        ker_filtro_ativo=False,
    )
    
    circuit_breakers = {p: CircuitBreakerPar(horas_suspensao=6.0) for p in dfs}
    
    saldo = capital_inicial
    posicoes_abertas = {}  # {symbol: {dados}}
    historico_trades = []
    sinais_recusados_por_limite = 0

    janela_min = 4 * est.janela_pivo + 2
    taxa = taxa_pct / 100
    slippage = slippage_pct / 100

    for ts in todos_ts:
        # A) Gerenciar posições abertas
        pares_fechar = []
        for sym, pos in list(posicoes_abertas.items()):
            df_p = dfs[sym]
            if ts not in df_p.index:
                continue
            candle = df_p.loc[ts]
            high_c = float(candle["high"])
            low_c = float(candle["low"])
            
            compra = pos["direcao"] == Direcao.COMPRA
            p_ent = pos["entrada"]
            amp = pos["amplitude"]
            
            # Trailing stop progressivo
            maior_fav = high_c if compra else low_c
            avanco = (maior_fav - p_ent) if compra else (p_ent - maior_fav)
            passos = int(avanco // (0.5 * amp)) if amp > 0 else 0
            if passos >= 2:
                mult_stop = (passos - 1) * 0.5
                if compra:
                    n_stop = p_ent + (mult_stop * amp)
                    if n_stop > pos["stop"]: pos["stop"] = n_stop
                else:
                    n_stop = p_ent - (mult_stop * amp)
                    if n_stop < pos["stop"]: pos["stop"] = n_stop

            atingiu_stop = (low_c <= pos["stop"]) if compra else (high_c >= pos["stop"])
            atingiu_alvo = (high_c >= pos["alvo_final"]) if compra else (low_c <= pos["alvo_final"])

            if atingiu_stop or atingiu_alvo:
                if atingiu_alvo and not atingiu_stop:
                    motivo = "alvo_final"
                    px_out = pos["alvo_final"] * (1 - slippage if compra else 1 + slippage)
                else:
                    motivo = "trailing_stop" if (pos["stop"] > p_ent if compra else pos["stop"] < p_ent) else "stop_loss"
                    px_out = pos["stop"] * (1 - slippage if compra else 1 + slippage)

                fator = 1.0 if compra else -1.0
                retorno_pct = ((px_out - p_ent) / p_ent) * 100 * fator
                custos_pct = (taxa * 2) * 100
                pnl_pct_liq = (retorno_pct - custos_pct) * alavancagem

                lucro_usd = pos["capital_alocado"] * (pnl_pct_liq / 100)
                saldo += lucro_usd

                circuit_breakers[sym].registrar_trade(ts, pnl_pct_liq, motivo_saida=motivo)
                
                historico_trades.append({
                    "symbol": sym,
                    "direcao": pos["direcao"].value,
                    "entrada": p_ent,
                    "saida": px_out,
                    "ts_in": pos["ts"],
                    "ts_out": ts,
                    "pnl_pct": pnl_pct_liq,
                    "lucro_usd": lucro_usd,
                    "saldo_apos": saldo,
                    "motivo": motivo,
                })
                pares_fechar.append(sym)

        for sym in pares_fechar:
            del posicoes_abertas[sym]

        # B) Avaliar novos sinais se tiver vagas abertas (< max_posicoes)
        for sym, df_p in dfs.items():
            if sym in posicoes_abertas:
                continue
            if ts not in df_p.index:
                continue
            
            idx_loc = df_p.index.get_loc(ts)
            if idx_loc < janela_min:
                continue

            # Checar Circuit Breaker
            suspenso, _ = circuit_breakers[sym].esta_suspenso(ts)
            if suspenso:
                continue

            sub_hist = df_p.iloc[:idx_loc]
            sinal = est.avaliar(sub_hist)
            if sinal is not None:
                # Se já atingiu o limite de posições simultâneas
                if len(posicoes_abertas) >= max_posicoes:
                    sinais_recusados_por_limite += 1
                    continue

                compra = sinal.direcao == Direcao.COMPRA
                c_close = float(df_p.loc[ts, "close"])
                p_entrada = c_close * (1 + slippage if compra else 1 - slippage)
                amp = sinal.grade["amplitude"]
                alvo_final = sinal.alvos[-1] if len(sinal.alvos) >= 2 else (p_entrada + (amp * 3) if compra else p_entrada - (amp * 3))

                # Capital alocado = Saldo disponível / slots restantes
                capital_slot = saldo / max_posicoes

                posicoes_abertas[sym] = {
                    "ts": ts,
                    "direcao": sinal.direcao,
                    "entrada": p_entrada,
                    "stop": sinal.stop_loss,
                    "amplitude": amp,
                    "alvo_final": alvo_final,
                    "capital_alocado": capital_slot,
                }

    return {
        "capital_inicial": capital_inicial,
        "saldo_final": saldo,
        "lucro_usd": saldo - capital_inicial,
        "retorno_pct": ((saldo - capital_inicial) / capital_inicial) * 100,
        "total_trades": len(historico_trades),
        "wins": sum(1 for t in historico_trades if t["lucro_usd"] > 0),
        "losses": sum(1 for t in historico_trades if t["lucro_usd"] <= 0),
        "sinais_recusados": sinais_recusados_por_limite,
        "trades": historico_trades,
    }


def main():
    pares = [
        "ALGOUSDT", "TAOUSDT", "NEARUSDT", "SOLUSDT", "ZECUSDT",
        "XRPUSDT", "ETHUSDT", "BTCUSDT", "AVAXUSDT", "LINKUSDT"
    ]
    print("=" * 88)
    print("  SIMULAÇÃO DE CARTEIRA: CAPITAL DE $1.000 COM MÁXIMO DE 3 POSIÇÕES CONCORRENTES")
    print("=" * 88)
    print(f"Pares monitorados: {', '.join(pares)} | Janela: 72 horas em velas de 5m")
    print("Regra de Alocação:")
    print("  • Banca dividida em 3 slots (~$333 por posição aberta)")
    print("  • Se 3 moedas estiverem posicionadas, não abre a 4ª até uma ser encerrada")
    print("  • Circuit Breaker de 6h ativo por par")
    print("Baixando dados mais recentes em paralelo...\n")

    dfs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        fut_map = {executor.submit(fetch_binance_futures_5m, p): p for p in pares}
        for f in concurrent.futures.as_completed(fut_map):
            p = fut_map[f]
            try:
                dfs[p] = f.result()
                print(f"  [OK] {p:<10}: {len(dfs[p])} velas")
            except Exception as e:
                print(f"  [ERRO] {p}: {e}")

    print("\n" + "=" * 88)
    print(f"{'ALAVANCAGEM':<14} | {'SALDO FINAL':<14} | {'LUCRO LÍQUIDO ($)':<18} | {'RETORNO (%)':<13} | {'TRADES (W/L)'}")
    print("-" * 88)

    for alav in [1.0, 3.0, 5.0, 10.0]:
        res = simular_portfolio_max_posicoes(dfs, capital_inicial=1000.0, max_posicoes=3, alavancagem=alav)
        wl = f"{res['total_trades']}t ({res['wins']}W/{res['losses']}L)"
        print(f"{alav:<14.0f}x | ${res['saldo_final']:<13.2f} | ${res['lucro_usd']:>+15.2f} | {res['retorno_pct']:>+11.2f}% | {wl}")

    print("=" * 88)


if __name__ == "__main__":
    main()
