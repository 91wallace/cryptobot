"""Otimização e Seleção de Paridade para KuCoin Futures a 5x de Alavancagem.
Testa:
  - Alavancagem: 5x
  - Variação de Amplitude Mínima: 0.30%, 0.40%, 0.50%
  - Variação de Folga Stop: 0.10, 0.15
  - Pares de maior volume e técnica: NEAR/USDT:USDT, SOL/USDT:USDT, BTC/USDT:USDT, ETH/USDT:USDT, ZEC/USDT:USDT
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

import ccxt
from strategy import Direcao, EstrategiaFimathe
from circuit_breaker import CircuitBreakerPar
from fimathe import calcular_atr
from backtests.apex_dinamics.backtest_kucoin_7dias_top4_10x import fetch_kucoin_futures_5m_7dias


def simular_par_individual(df_p: pd.DataFrame, sym: str, amp_min: float, folga_stop: float, alavancagem: float = 5.0):
    est = EstrategiaFimathe(
        janela_pivo=3,
        qtd_niveis=4,
        folga_stop=folga_stop,
        amplitude_min_pct=amp_min,
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
    trades = []
    pos = None

    for idx in range(janela_min, len(df_p)):
        ts = df_p.index[idx]
        candle = df_p.iloc[idx]
        high_c = float(candle["high"])
        low_c = float(candle["low"])

        # 1. Gerenciar posição
        if pos is not None:
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

                lucro_usd = saldo * (pnl_pct_liq / 100)
                saldo += lucro_usd

                cb.registrar_trade(ts, pnl_pct_liq, motivo_saida=motivo)
                trades.append({"lucro_usd": lucro_usd, "pnl_pct": pnl_pct_liq, "motivo": motivo})
                pos = None

        # 2. Entrada
        if pos is None:
            suspenso, _ = cb.esta_suspenso(ts)
            if suspenso:
                continue

            sub_hist = df_p.iloc[:idx]
            sinal = est.avaliar(sub_hist)
            if sinal is not None:
                compra = sinal.direcao == Direcao.COMPRA
                c_close = float(candle["close"])
                p_entrada = c_close * (1 + slippage if compra else 1 - slippage)
                amp = sinal.grade["amplitude"]
                alvo_final = sinal.alvos[-1] if len(sinal.alvos) >= 2 else (p_entrada + (amp * 3) if compra else p_entrada - (amp * 3))

                pos = {
                    "direcao": sinal.direcao,
                    "entrada": p_entrada,
                    "stop": sinal.stop_loss,
                    "amplitude": amp,
                    "alvo_final": alvo_final,
                }

    total_t = len(trades)
    wins = [t for t in trades if t["lucro_usd"] > 0]
    wr = (len(wins) / total_t * 100) if total_t > 0 else 0
    lucro_total = saldo - 1000.0
    retorno_pct = (lucro_total / 1000.0) * 100
    return {
        "par": sym,
        "amp_min": amp_min,
        "folga_stop": folga_stop,
        "trades": total_t,
        "winrate": wr,
        "lucro_total": lucro_total,
        "retorno_pct": retorno_pct,
    }


def main():
    pares = [
        ("NEARUSDT", "NEAR/USDT:USDT"),
        ("SOLUSDT", "SOL/USDT:USDT"),
        ("BTCUSDT", "BTC/USDT:USDT"),
        ("ETHUSDT", "ETH/USDT:USDT"),
    ]

    print("Carregando dados da KuCoin Futures (7 Dias / 5m)...")
    dfs = {}
    for name, sym_kc in pares:
        try:
            df = fetch_kucoin_futures_5m_7dias(sym_kc, target_candles=2016)
            dfs[name] = df
            print(f"  [OK] {name}: {len(df)} candles")
        except Exception as e:
            print(f"  [ERRO] {name}: {e}")

    print("\nExecutando Otimização de Calibração KuCoin (Alavancagem 5x)...")
    resultados = []
    for name, df in dfs.items():
        for amp in [0.20, 0.35, 0.45]:
            for folga in [0.10, 0.15]:
                res = simular_par_individual(df, name, amp_min=amp, folga_stop=folga, alavancagem=5.0)
                resultados.append(res)

    df_res = pd.DataFrame(resultados).sort_values(by="lucro_total", reverse=True)
    print("\n" + "=" * 80)
    print("🏆 RESULTADOS DO GRID SEARCH - KUCOIN FUTURES (5X)")
    print("=" * 80)
    print(df_res.to_string(index=False))


if __name__ == "__main__":
    main()
