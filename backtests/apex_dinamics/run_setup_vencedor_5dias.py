"""Backtest Exato do Setup Vencedor (simular_carteira_3posicoes.py) no Prazo de 5 Dias (1.440 velas de 5m por par).
Setup:
  - Capital Inicial: $1.000 USDT
  - Alavancagem: 10x
  - Limite de Posições Concorrentes: 3 pares simultâneos (~$333 por trade)
  - Pares: ALGOUSDT, TAOUSDT, NEARUSDT, SOLUSDT, ZECUSDT, XRPUSDT, ETHUSDT, BTCUSDT, AVAXUSDT, LINKUSDT
  - 1 Nível de Rompimento da Fimathe (romper_no_canal=True)
  - Trailing Stop Progressivo por níveis (ao bater nível 1 -> stop a 50% do nível 1, metade do nível 2 -> stop no nível 1, etc.)
  - Circuit Breaker Temporal de 6h (2 stops em 6h ou alternâncias em 4h suspendem o par por 6h; trailing stop conta como gain)
  - Janela: 5 dias completos (1.440 velas de 5m)
"""
import concurrent.futures
import pandas as pd
import numpy as np

from strategy import Direcao, EstrategiaFimathe
from circuit_breaker import CircuitBreakerPar
from backtest_5dias_50pares_10x import fetch_binance_futures_5m_5dias


def main():
    pares = [
        "ALGOUSDT", "TAOUSDT", "NEARUSDT", "SOLUSDT", "ZECUSDT",
        "XRPUSDT", "ETHUSDT", "BTCUSDT", "AVAXUSDT", "LINKUSDT"
    ]

    print("=" * 96)
    print("   BACKTEST DO SETUP VENCEDOR EM 5 DIAS (1.440 VELAS DE 5M) | CARTEIRA $1.000 | 10X")
    print("=" * 96)
    print(f"Pares avaliados (10 pares): {', '.join(pares)}")
    print("Regras Exatas do Setup:")
    print("  • Capital Inicial: $1.000 USDT")
    print("  • Limite de 3 posições simultâneas (~$333 por vaga)")
    print("  • Alavancagem: 10x")
    print("  • 1 Nível de Rompimento (Canal de Referência)")
    print("  • Trailing Stop progressivo por níveis")
    print("  • Circuit Breaker de 6h ativo por par")
    print("Baixando dados de 5 dias em paralelo...\n")

    dfs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        fut_map = {executor.submit(fetch_binance_futures_5m_5dias, p): p for p in pares}
        for f in concurrent.futures.as_completed(fut_map):
            p = fut_map[f]
            try:
                df_res = f.result()
                if len(df_res) >= 500:
                    dfs[p] = df_res
                    print(f"  [OK] {p:<10}: {len(df_res)} velas")
            except Exception as e:
                print(f"  [ERRO] {p}: {e}")

    todos_ts = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    inicio_dt = todos_ts[0].strftime("%d/%m/%Y %H:%M UTC")
    fim_dt = todos_ts[-1].strftime("%d/%m/%Y %H:%M UTC")
    print(f"\nPeríodo simulado: {inicio_dt} até {fim_dt}")

    # Configuração Exata da Estratégia Fimathe do Setup Vencedor
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

    circuit_breakers = {p: CircuitBreakerPar(horas_base=6.0, horas_reincidencia=24.0) for p in dfs}

    capital_inicial = 1000.0
    alavancagem = 10.0
    max_posicoes = 3
    saldo = capital_inicial
    posicoes_abertas = {}
    historico_trades = []
    sinais_descartados_limite = 0

    taxa = 0.04 / 100
    slippage = 0.01 / 100
    janela_min = 4 * est.janela_pivo + 2

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
            
            # Trailing stop por níveis
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

                # Registrar no Circuit Breaker (trailing_stop é considerado gain)
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

            suspenso, _ = circuit_breakers[sym].esta_suspenso(ts)
            if suspenso:
                continue

            sub_hist = df_p.iloc[:idx_loc]
            sinal = est.avaliar(sub_hist)
            if sinal is not None:
                if len(posicoes_abertas) >= max_posicoes:
                    sinais_descartados_limite += 1
                    continue

                compra = sinal.direcao == Direcao.COMPRA
                c_close = float(df_p.loc[ts, "close"])
                p_entrada = c_close * (1 + slippage if compra else 1 - slippage)
                amp = sinal.grade["amplitude"]
                alvo_final = sinal.alvos[-1] if len(sinal.alvos) >= 2 else (p_entrada + (amp * 3) if compra else p_entrada - (amp * 3))

                # Capital alocado: saldo atual / 3 vagas
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

    # Consolidação dos Resultados
    total_trades = len(historico_trades)
    wins = [t for t in historico_trades if t["lucro_usd"] > 0]
    losses = [t for t in historico_trades if t["lucro_usd"] <= 0]
    wr = (len(wins) / total_trades * 100) if total_trades > 0 else 0
    lucro_total_usd = saldo - capital_inicial
    retorno_total_pct = (lucro_total_usd / capital_inicial) * 100

    print("\n" + "=" * 96)
    print("       RELATÓRIO DO BACKTEST: SETUP VENCEDOR EM 5 DIAS (10 PARES / 10X)")
    print("=" * 96)
    print(f"Capital Inicial               : $ {capital_inicial:,.2f} USDT")
    print(f"Saldo Final da Conta          : $ {saldo:,.2f} USDT")
    print(f"LUCRO LÍQUIDO TOTAL ($)       : $ {lucro_total_usd:>+,.2f} USDT")
    print(f"RENTABILIDADE TOTAL (%)       : {retorno_total_pct:>+8.2f}%")
    print(f"Alavancagem Fixa              : {alavancagem:.0f}x")
    print(f"Limite de Posições Concorrentes: {max_posicoes} pares simultâneos")
    print("-" * 96)
    print(f"Total de Trades Executados    : {total_trades}")
    print(f"Trades Vencedores (Wins)      : {len(wins)}")
    print(f"Trades Perdedores (Losses)    : {len(losses)}")
    print(f"Taxa de Acerto (Winrate)      : {wr:.2f}%")
    print(f"Sinais Descartados por Limite : {sinais_descartados_limite}")
    print("=" * 96)

    # Desempenho individual
    print("\n--- DESEMPENHO INDIVIDUAL POR PAR ---")
    print(f"{'PAR':<10} | {'TRADES':<7} | {'W/L':<8} | {'WINRATE':<9} | {'PnL TOTAL ($)':<14}")
    print("-" * 55)
    for p in pares:
        tr_p = [t for t in historico_trades if t["symbol"] == p]
        w_p = sum(1 for t in tr_p if t["lucro_usd"] > 0)
        l_p = sum(1 for t in tr_p if t["lucro_usd"] <= 0)
        wr_p = (w_p / len(tr_p) * 100) if tr_p else 0
        lucro_p = sum(t["lucro_usd"] for t in tr_p)
        wl_s = f"{w_p}W/{l_p}L"
        print(f"{p:<10} | {len(tr_p):<7} | {wl_s:<8} | {wr_p:>7.1f}% | ${lucro_p:>+12.2f}")
    print("=" * 55)

    if wins:
        top_trades = sorted(wins, key=lambda x: x["lucro_usd"], reverse=True)
        print("\n--- TOP 5 MAIORES GAINS CONDUZIDOS PELO TRAILING STOP ---")
        for i, t in enumerate(top_trades[:5], 1):
            dur_m = int((t["ts_out"] - t["ts_in"]).total_seconds() // 60)
            print(f" {i}. [{t['symbol']:<8}] {t['direcao'].upper():<4} PnL: {t['pnl_pct']:>+5.2f}% (10x) | Lucro: +${t['lucro_usd']:>6.2f} | Dur: {dur_m}m | {t['motivo'].upper()}")


if __name__ == "__main__":
    main()
