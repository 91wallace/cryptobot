"""Backtest Fiel ao Nautilus Trader: 1 Posição vs Até 2 Posições por Ativo no SOL/USDT.
Usa exatamente a mesma lógica do Nautilus:
  - Trailing Stop: avança a cada 1.00x degrau; quando avança >= 2 degraus, stop vai para (passos - 1) * amp
  - Fechamento de posições: Circuit Breaker suspende por 6h quando ocorrem 2 losses consecutivos em janela de 6h
  - 1 Posição: 50% de alocação da conta por trade (como no Nautilus)
  - 2 Posições: 25% de alocação por trade (permite piramidar na tendência)
"""
import sys
import os
import pandas as pd
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "strategies")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "core")))

from strategy import Direcao, EstrategiaFimathe
from backtests.apex_dinamics.backtest_lite38_14dias import fetch_kucoin_futures_5m_dias


def simular_nautilus_multi_pos(df_p: pd.DataFrame, max_posicoes: int = 1, alavancagem: float = 5.0):
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

    saldo = 1000.0
    taxa = 0.06 / 100
    janela_min = 4 * est.janela_pivo + 2
    posicoes = []
    historico_trades = []
    historico_fechamentos = []  # [(ts, 'GAIN'/'LOSS')]
    suspenso_ate_ts = 0.0
    equity_curve = [saldo]

    for idx in range(janela_min, len(df_p)):
        ts = df_p.index[idx]
        ts_sec = ts.timestamp()
        candle = df_p.iloc[idx]
        high_c = float(candle["high"])
        low_c = float(candle["low"])

        # 1. Gerenciar posições abertas
        posicoes_restantes = []
        for pos in posicoes:
            compra = pos["direcao"] == Direcao.COMPRA
            p_ent = pos["entrada"]
            amp = pos["amplitude"]

            # Degraus idênticos ao Nautilus Trader
            maior_fav = high_c if compra else low_c
            avanco = (maior_fav - p_ent) if compra else (p_ent - maior_fav)
            degrau_step = amp * 1.00
            passos = int(avanco // degrau_step) if degrau_step > 0 else 0

            if passos >= 2:
                mult_stop = (passos - 1) * 1.00
                if compra:
                    n_stop = p_ent + (mult_stop * amp)
                    if n_stop > pos["stop"]: pos["stop"] = n_stop
                else:
                    n_stop = p_ent - (mult_stop * amp)
                    if n_stop < pos["stop"]: pos["stop"] = n_stop

            atingiu_stop = (low_c <= pos["stop"]) if compra else (high_c >= pos["stop"])
            atingiu_alvo = (high_c >= pos["alvo_final"]) if compra else (low_c <= pos["alvo_final"])

            if atingiu_alvo or atingiu_stop:
                if atingiu_alvo and not atingiu_stop:
                    motivo = "ALVO_FINAL"
                    px_out = pos["alvo_final"]
                else:
                    lucro_travado = (pos["stop"] > p_ent) if compra else (pos["stop"] < p_ent)
                    motivo = "TRAILING_STOP_GAIN" if lucro_travado else "STOP_LOSS"
                    px_out = pos["stop"]

                fator = 1.0 if compra else -1.0
                retorno_pct = ((px_out - p_ent) / p_ent) * 100 * fator
                custos_pct = (taxa * 2) * 100
                pnl_pct_liq = (retorno_pct - custos_pct) * alavancagem

                lucro_usd = pos["capital_alocado"] * (pnl_pct_liq / 100)
                saldo += lucro_usd

                tipo_res = "GAIN" if ("GAIN" in motivo or "ALVO" in motivo) else "LOSS"
                historico_fechamentos.append((ts_sec, tipo_res))

                # Circuit breaker do Nautilus: 2 losses consecutivos em 6h suspendem por 6h
                limite_janela = ts_sec - (6.0 * 3600)
                recentes = [r for t, r in historico_fechamentos if t >= limite_janela]
                if len(recentes) >= 2 and recentes[-1] == "LOSS" and recentes[-2] == "LOSS":
                    suspenso_ate_ts = ts_sec + (6.0 * 3600)

                historico_trades.append({
                    "lucro_usd": lucro_usd,
                    "motivo": motivo,
                })
            else:
                posicoes_restantes.append(pos)

        posicoes = posicoes_restantes
        equity_curve.append(saldo)

        # 2. Novas entradas se houver vaga
        if len(posicoes) < max_posicoes:
            if ts_sec >= suspenso_ate_ts:
                sub_hist = df_p.iloc[:idx]
                sinal = est.avaliar(sub_hist)
                if sinal is not None:
                    pode_entrar = True
                    if len(posicoes) == 1:
                        if posicoes[0]["direcao"] != sinal.direcao:
                            pode_entrar = False  # Só entra se for a favor da mesma tendência

                    if pode_entrar:
                        compra = sinal.direcao == Direcao.COMPRA
                        p_entrada = float(candle["close"])
                        amp = float(sinal.grade["amplitude"])
                        alvo_final = sinal.alvos[-1] if len(sinal.alvos) >= 2 else (p_entrada + 3 * amp if compra else p_entrada - 3 * amp)
                        capital_alocado = saldo * (0.50 / max_posicoes)

                        posicoes.append({
                            "direcao": sinal.direcao,
                            "entrada": p_entrada,
                            "stop": float(sinal.stop_loss),
                            "amplitude": amp,
                            "alvo_final": alvo_final,
                            "capital_alocado": capital_alocado,
                        })

    total_trades = len(historico_trades)
    wins = [t for t in historico_trades if t["lucro_usd"] > 0]
    wr = (len(wins) / total_trades * 100) if total_trades > 0 else 0
    lucro_total = saldo - 1000.0
    retorno_pct = (lucro_total / 1000.0) * 100

    arr_eq = np.array(equity_curve)
    picos = np.maximum.accumulate(arr_eq)
    dds = (picos - arr_eq) / picos
    max_dd_pct = float(np.max(dds)) * 100 if len(dds) > 0 else 0.0

    return {
        "Modo": f"Máx {max_posicoes} Posição(ões)",
        "Retorno (%)": f"{retorno_pct:+.2f}%",
        "Lucro Líquido ($)": f"${lucro_total:+,.2f}",
        "Max Drawdown (%)": f"{max_dd_pct:.2f}%",
        "Total de Trades": total_trades,
        "Winrate (%)": f"{wr:.1f}%",
        "_pnl": lucro_total,
    }


def main():
    print("\n" + "=" * 90)
    print(" 🚀 COMPARATIVO 1 POSIÇÃO vs ATÉ 2 POSIÇÕES NO SOL (30 DIAS NA KUCOIN 5X)")
    print("=" * 90)
    df = fetch_kucoin_futures_5m_dias("SOL/USDT:USDT", dias=30)

    res1 = simular_nautilus_multi_pos(df, max_posicoes=1, alavancagem=5.0)
    res2 = simular_nautilus_multi_pos(df, max_posicoes=2, alavancagem=5.0)

    df_res = pd.DataFrame([res1, res2]).sort_values(by="_pnl", ascending=False)
    print("\n" + "=" * 90)
    print(" 🏆 RESULTADO CONSOLIDADO: 1 POSIÇÃO vs ATÉ 2 POSIÇÕES POR ATIVO (30 DIAS)")
    print("=" * 90)
    print(df_res.drop(columns=["_pnl"]).to_string(index=False))
    print("=" * 90 + "\n")


if __name__ == "__main__":
    main()
