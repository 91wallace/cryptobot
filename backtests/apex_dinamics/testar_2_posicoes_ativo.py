"""Comparativo de Posições Simultâneas no Mesmo Ativo (SOL/USDT:USDT KuCoin Futures 5x).
Testa:
  - Cenário A (1 Posição por Ativo - Padrão):
      Alocação: 50% da conta por trade. Se já estiver posicionado, ignora novos rompimentos até fechar.
  - Cenário B (Até 2 Posições por Ativo - Piramidação/Escalonamento):
      Alocação: 25% da conta por trade (total 50% de margem com 2 posições).
      Se surgir um novo rompimento ou avanço de nível válido na mesma direção, abre a 2ª posição
      com seu próprio stop loss e condução de trailing independente.
Período: 30 dias (~8.640 velas de 5m) com dados reais da KuCoin Futures.
"""
import sys
import os
import time
import pandas as pd
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "strategies")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "core")))

from strategy import Direcao, EstrategiaFimathe
from circuit_breaker import CircuitBreakerPar
from backtests.apex_dinamics.backtest_lite38_14dias import fetch_kucoin_futures_5m_dias


def simular_posicoes_por_ativo(df_p: pd.DataFrame, max_posicoes: int = 1, alavancagem: float = 5.0):
    est = EstrategiaFimathe(
        janela_pivo=4,
        qtd_niveis=4,
        folga_stop=0.10,
        amplitude_min_pct=0.40,
        usar_filtro_50=False,
        modo_stop="fora_caixinha",
        atr_filtro_ativo=False,
        romper_no_canal=True,
        ker_filtro_ativo=False,
    )
    cb = CircuitBreakerPar(horas_base=6.0, horas_reincidencia=24.0)

    saldo = 1000.0
    taxa = 0.06 / 100
    slippage = 0.01 / 100
    janela_min = 4 * est.janela_pivo + 2
    posicoes = []  # lista de posições ativas: [pos1, pos2]
    historico_trades = []
    equity_curve = [saldo]

    for idx in range(janela_min, len(df_p)):
        ts = df_p.index[idx]
        candle = df_p.iloc[idx]
        high_c = float(candle["high"])
        low_c = float(candle["low"])

        # 1. Gerenciar posições abertas
        posicoes_restantes = []
        for pos in posicoes:
            compra = pos["direcao"] == Direcao.COMPRA
            p_ent = pos["entrada"]
            amp = pos["amplitude"]

            # Trailing stop progressivo em degraus de 1.0x
            maior_fav = high_c if compra else low_c
            avanco = (maior_fav - p_ent) if compra else (p_ent - maior_fav)
            passos = int(avanco // amp) if amp > 0 else 0
            if passos >= 1:
                mult_stop = passos * 0.75  # trava lucro progressivo
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

                cb.registrar_trade(ts, pnl_pct_liq, motivo_saida=motivo)
                historico_trades.append({
                    "ts_in": pos["ts"],
                    "ts_out": ts,
                    "lucro_usd": lucro_usd,
                    "pnl_pct": pnl_pct_liq,
                    "motivo": motivo
                })
            else:
                posicoes_restantes.append(pos)

        posicoes = posicoes_restantes
        equity_curve.append(saldo)

        # 2. Avaliar novas entradas se tiver vaga disponível (< max_posicoes)
        if len(posicoes) < max_posicoes:
            suspenso, _ = cb.esta_suspenso(ts)
            if not suspenso:
                sub_hist = df_p.iloc[:idx]
                sinal = est.avaliar(sub_hist)
                if sinal is not None:
                    # Se já tiver 1 posição, só adiciona a 2ª se for na MESMA direção (piramidação a favor da tendência)
                    pode_entrar = True
                    if len(posicoes) == 1:
                        if posicoes[0]["direcao"] != sinal.direcao:
                            pode_entrar = False  # Não abre posições opostas simultâneas (hedging conflitante)

                    if pode_entrar:
                        compra = sinal.direcao == Direcao.COMPRA
                        c_close = float(candle["close"])
                        p_entrada = c_close * (1 + slippage if compra else 1 - slippage)
                        amp = sinal.grade["amplitude"]
                        alvo_final = sinal.alvos[-1] if len(sinal.alvos) >= 2 else (p_entrada + (amp * 3) if compra else p_entrada - (amp * 3))

                        # Alocação fracionada: se max_posicoes = 2, cada posição usa 25% da banca (total 50%)
                        # Se max_posicoes = 1, usa 50% da banca
                        fracao = 0.50 / max_posicoes
                        capital_alocado = saldo * fracao

                        posicoes.append({
                            "ts": ts,
                            "direcao": sinal.direcao,
                            "entrada": p_entrada,
                            "stop": sinal.stop_loss,
                            "amplitude": amp,
                            "alvo_final": alvo_final,
                            "capital_alocado": capital_alocado,
                        })

    # Métricas
    total_trades = len(historico_trades)
    wins = [t for t in historico_trades if t["lucro_usd"] > 0]
    wr = (len(wins) / total_trades * 100) if total_trades > 0 else 0
    lucro_total = saldo - 1000.0
    retorno_pct = (lucro_total / 1000.0) * 100

    # Max Drawdown
    arr_eq = np.array(equity_curve)
    picos = np.maximum.accumulate(arr_eq)
    dds = (picos - arr_eq) / picos
    max_dd_pct = float(np.max(dds)) * 100 if len(dds) > 0 else 0.0

    return {
        "max_posicoes": f"Até {max_posicoes} Posição(ões)",
        "Retorno (%)": f"{retorno_pct:+.2f}%",
        "Lucro Líquido ($)": f"${lucro_total:+,.2f}",
        "Max Drawdown (%)": f"{max_dd_pct:.2f}%",
        "Total de Trades": total_trades,
        "Winrate (%)": f"{wr:.1f}%",
        "_pnl": lucro_total,
    }


def main():
    print("\n" + "=" * 90)
    print(" ⚔️ TESTE COMPARATIVO: 1 POSIÇÃO vs ATÉ 2 POSIÇÕES NO MESMO ATIVO (SOL 30 DIAS)")
    print("=" * 90)
    print("Exchange        : KuCoin Futures (USDT-M)")
    print("Par Testado     : SOL/USDT:USDT (Exclusivo)")
    print("Alavancagem     : 5x")
    print("Período         : 30 Dias (~8.640 velas de 5m)")
    print("-" * 90)

    df = fetch_kucoin_futures_5m_dias("SOL/USDT:USDT", dias=30)

    resultados = []
    print("\nSimulando Cenário A: Máximo 1 Posição por Ativo...")
    res1 = simular_posicoes_por_ativo(df, max_posicoes=1, alavancagem=5.0)
    resultados.append(res1)

    print("Simulando Cenário B: Até 2 Posições por Ativo (Piramidação na Tendência)...")
    res2 = simular_posicoes_por_ativo(df, max_posicoes=2, alavancagem=5.0)
    resultados.append(res2)

    df_res = pd.DataFrame(resultados).sort_values(by="_pnl", ascending=False)
    print("\n" + "=" * 90)
    print(" 🏆 RESULTADO CONSOLIDADO: 1 POSIÇÃO vs ATÉ 2 POSIÇÕES POR ATIVO (30 DIAS)")
    print("=" * 90)
    print(df_res.drop(columns=["_pnl"]).to_string(index=False))
    print("=" * 90 + "\n")


if __name__ == "__main__":
    main()
