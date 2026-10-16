"""Backtest Fimathe Futures com Trailing Stop por Níveis + CIRCUIT BREAKER TEMPORAL
Regras de Suspensão por Par (Circuit Breaker):
  1. Se sofrer 2 STOPS consecutivos em até 6 horas: suspende operações no par por 6 horas.
  2. Se fizer o padrão de alternância (1 Gain / 1 Stop / 1 Gain / 1 Stop) em até 4 horas: suspende por 6 horas.
  3. Se fizer o padrão (1 Stop / 1 Gain / 1 Stop) em até 4 horas: suspende por 6 horas.
Executado nos 10 pares com maior volatilidade no mercado futuro da Binance em velas de 5m (72h).
"""
import concurrent.futures
import json
import urllib.request
import pandas as pd
import numpy as np

from strategy import Direcao, EstrategiaFimathe
from backtest import TradeResultado
from fimathe import calcular_atr
from circuit_breaker import CircuitBreakerPar


def fetch_binance_futures_5m(symbol: str, limit: int = 864) -> pd.DataFrame:
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=5m&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    
    records = []
    for item in data:
        records.append({
            "timestamp": pd.to_datetime(item[0], unit="ms", utc=True),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
        })
    df = pd.DataFrame(records).set_index("timestamp")
    df["atr_14"] = calcular_atr(df, 14)
    return df


class BacktestFimatheComCircuitBreaker:
    def __init__(
        self,
        estrategia: EstrategiaFimathe,
        taxa_pct: float = 0.04,
        slippage_pct: float = 0.01,
        usar_circuit_breaker: bool = True,
    ):
        self.estrategia = estrategia
        self.taxa = taxa_pct / 100
        self.slippage = slippage_pct / 100
        self.usar_circuit_breaker = usar_circuit_breaker

    def executar(self, df: pd.DataFrame) -> dict:
        trades = []
        posicao = None
        cb = CircuitBreakerPar(horas_suspensao=6.0)
        bloqueios_ocorridos = []
        
        janela_min = 4 * self.estrategia.janela_pivo + 2
        total_candles = len(df)

        for i in range(janela_min, total_candles):
            candle_atual = df.iloc[i]
            high_atual = float(candle_atual["high"])
            low_atual = float(candle_atual["low"])
            close_atual = float(candle_atual["close"])
            ts_atual = df.index[i]

            # 1. Gerenciamento da posição aberta com Trailing Stop Progressivo
            if posicao is not None:
                compra = posicao["direcao"] == Direcao.COMPRA
                amplitude = posicao["amplitude"]
                p_entrada = posicao["entrada"]
                
                maior_favoravel = high_atual if compra else low_atual
                avanco = (maior_favoravel - p_entrada) if compra else (p_entrada - maior_favoravel)
                passos = int(avanco // (0.5 * amplitude)) if amplitude > 0 else 0
                
                # Trailing stop por níveis
                if passos >= 2:
                    mult_stop = (passos - 1) * 0.5
                    if compra:
                        novo_stop = p_entrada + (mult_stop * amplitude)
                        if novo_stop > posicao["stop"]:
                            posicao["stop"] = novo_stop
                    else:
                        novo_stop = p_entrada - (mult_stop * amplitude)
                        if novo_stop < posicao["stop"]:
                            posicao["stop"] = novo_stop

                atingiu_stop = (low_atual <= posicao["stop"]) if compra else (high_atual >= posicao["stop"])
                alvo_final = posicao["alvo_final"]
                atingiu_alvo_final = (high_atual >= alvo_final) if compra else (low_atual <= alvo_final)

                if atingiu_stop or atingiu_alvo_final:
                    if atingiu_alvo_final and not atingiu_stop:
                        motivo = "alvo_final"
                        px_saida = alvo_final * (1 - self.slippage if compra else 1 + self.slippage)
                    else:
                        motivo = "trailing_stop" if (posicao["stop"] > p_entrada if compra else posicao["stop"] < p_entrada) else "stop_loss"
                        px_saida = posicao["stop"] * (1 - self.slippage if compra else 1 + self.slippage)

                    fator = 1.0 if compra else -1.0
                    pnl_bruto_pct = ((px_saida - p_entrada) / p_entrada) * 100 * fator
                    custos_pct = (self.taxa * 2) * 100
                    pnl_liq_pct = pnl_bruto_pct - custos_pct

                    duracao = i - posicao["indice_i"]
                    trades.append(TradeResultado(
                        indice_entrada=posicao["ts"],
                        indice_saida=ts_atual,
                        direcao=posicao["direcao"].value,
                        preco_entrada=p_entrada,
                        preco_saida=px_saida,
                        stop_loss=posicao["stop"],
                        alvo=alvo_final,
                        motivo_saida=motivo,
                        pnl_pct=pnl_liq_pct,
                        duracao_candles=duracao,
                    ))

                    # REGISTRA NO CIRCUIT BREAKER
                    if self.usar_circuit_breaker:
                        cb.registrar_trade(ts_atual, pnl_liq_pct, motivo_saida=motivo)
                        suspenso, motivo_cb = cb.esta_suspenso(ts_atual)
                        if suspenso and cb.suspenso_ate not in [b["ate"] for b in bloqueios_ocorridos]:
                            bloqueios_ocorridos.append({
                                "inicio": ts_atual,
                                "ate": cb.suspenso_ate,
                                "motivo": cb.motivo_ultimo_bloqueio,
                            })

                    posicao = None
                continue

            # 2. Avaliação de novo sinal (verifica se o par está suspenso pelo Circuit Breaker)
            if self.usar_circuit_breaker:
                suspenso, motivo_cb = cb.esta_suspenso(ts_atual)
                if suspenso:
                    continue  # PULA AVALIAÇÃO: PAR EM QUARENTENA TEMPORAL POR 6 HORAS

            df_hist = df.iloc[:i]
            sinal = self.estrategia.avaliar(df_hist)
            if sinal is None:
                continue

            compra = sinal.direcao == Direcao.COMPRA
            preco_entrada = close_atual * (1 + self.slippage if compra else 1 - self.slippage)
            amplitude = sinal.grade["amplitude"]
            alvo_final = sinal.alvos[-1] if len(sinal.alvos) >= 2 else (preco_entrada + (amplitude * 3) if compra else preco_entrada - (amplitude * 3))

            posicao = {
                "ts": ts_atual,
                "indice_i": i,
                "direcao": sinal.direcao,
                "entrada": preco_entrada,
                "stop": sinal.stop_loss,
                "amplitude": amplitude,
                "alvo_final": alvo_final,
            }

        # Estatísticas
        if not trades:
            return {
                "total_trades": 0, "wins": 0, "losses": 0,
                "win_rate_pct": 0.0, "retorno_total_pct": 0.0,
                "profit_factor": 0.0, "max_drawdown_pct": 0.0,
                "trades": [], "bloqueios": bloqueios_ocorridos,
            }

        pnls = [t.pnl_pct for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        wr = (len(wins) / len(trades)) * 100
        ret_total = sum(pnls)
        lucro_bruto = sum(wins)
        preju_bruto = abs(sum(losses))
        pf = (lucro_bruto / preju_bruto) if preju_bruto > 0 else float("inf")

        curva = np.cumsum(pnls)
        picos = np.maximum.accumulate(curva)
        drawdowns = picos - curva
        max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

        return {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": wr,
            "retorno_total_pct": ret_total,
            "profit_factor": pf,
            "max_drawdown_pct": max_dd,
            "trades": trades,
            "bloqueios": bloqueios_ocorridos,
        }


def main():
    pares = [
        "ALGOUSDT", "TAOUSDT", "NEARUSDT", "SOLUSDT", "ZECUSDT",
        "XRPUSDT", "ETHUSDT", "BTCUSDT", "AVAXUSDT", "LINKUSDT"
    ]

    print("=" * 92)
    print("    BACKTEST FIMATHE FUTURES COM CIRCUIT BREAKER TEMPORAL DE 6 HORAS")
    print("=" * 92)
    print("Regras do Circuit Breaker por Par:")
    print("  • 2 Stops consecutivos em até 6h -> Pausa por 6h")
    print("  • Padrão (Loss / Gain / Loss) em até 4h -> Pausa por 6h")
    print("  • Padrão (Gain / Loss / Gain / Loss) em até 4h -> Pausa por 6h")
    print(f"Pares avaliados: {', '.join(pares)} | Janela: 72 horas em velas de 5m\n")

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

    est = EstrategiaFimathe(
        janela_pivo=3,
        qtd_niveis=4,
        folga_stop=0.10,
        amplitude_min_pct=0.20,
        usar_filtro_50=False,
        modo_stop="fora_caixinha",
        atr_filtro_ativo=False,
        romper_no_canal=True,
        ker_filtro_ativo=False,  # Deixamos o Circuit Breaker atuar como filtro principal
    )

    bt_sem_cb = BacktestFimatheComCircuitBreaker(est, usar_circuit_breaker=False)
    bt_com_cb = BacktestFimatheComCircuitBreaker(est, usar_circuit_breaker=True)

    print("\n" + "=" * 92)
    print("                 COMPARATIVO DIRETO: SEM CIRCUIT BREAKER VS COM CIRCUIT BREAKER")
    print("=" * 92)
    print(f"{'PAR':<10} | {'SEM CB (W/L)':<15} | {'SEM CB PNL':<11} | {'COM CB (W/L)':<15} | {'COM CB PNL':<11} | {'BLOQUEIOS'}")
    print("-" * 92)

    total_sem_t, total_sem_w, total_sem_l, pnl_sem = 0, 0, 0, 0.0
    total_com_t, total_com_w, total_com_l, pnl_com = 0, 0, 0, 0.0
    todos_bloqueios = []

    for p in pares:
        if p not in dfs:
            continue
        r_sem = bt_sem_cb.executar(dfs[p])
        r_com = bt_com_cb.executar(dfs[p])

        total_sem_t += r_sem["total_trades"]
        total_sem_w += r_sem["wins"]
        total_sem_l += r_sem["losses"]
        pnl_sem += r_sem["retorno_total_pct"]

        total_com_t += r_com["total_trades"]
        total_com_w += r_com["wins"]
        total_com_l += r_com["losses"]
        pnl_com += r_com["retorno_total_pct"]

        s_sem = f"{r_sem['total_trades']}t ({r_sem['wins']}W/{r_sem['losses']}L)"
        s_com = f"{r_com['total_trades']}t ({r_com['wins']}W/{r_com['losses']}L)"
        qtd_b = len(r_com["bloqueios"])

        print(f"{p:<10} | {s_sem:<15} | {r_sem['retorno_total_pct']:>+9.2f}% | {s_com:<15} | {r_com['retorno_total_pct']:>+9.2f}% | {qtd_b} suspensões")
        for b in r_com["bloqueios"]:
            todos_bloqueios.append((p, b))

    print("-" * 92)
    wr_sem = (total_sem_w / total_sem_t * 100) if total_sem_t > 0 else 0
    wr_com = (total_com_w / total_com_t * 100) if total_com_t > 0 else 0
    str_sem = f"{total_sem_t}t ({total_sem_w}W/{total_sem_l}L)"
    str_com = f"{total_com_t}t ({total_com_w}W/{total_com_l}L)"
    print(f"{'CONSOLIDADO':<10} | {str_sem:<15} | {pnl_sem:>+9.2f}% | {str_com:<15} | {pnl_com:>+9.2f}% | {len(todos_bloqueios)} suspensões")
    print(f"{'WINRATE':<10} | {wr_sem:>13.1f}% | {'---':<11} | {wr_com:>13.1f}% | {'---':<11} | {'---'}")
    print("=" * 92)

    if todos_bloqueios:
        print("\n--- DETALHAMENTO DAS SUSPENSÕES ACIONADAS PELO CIRCUIT BREAKER ---")
        for sym, b in todos_bloqueios:
            print(f" • [{sym}] Suspenso de {b['inicio'].strftime('%d/%m %H:%M')} até {b['ate'].strftime('%d/%m %H:%M')} | Motivo: {b['motivo']}")


if __name__ == "__main__":
    main()
