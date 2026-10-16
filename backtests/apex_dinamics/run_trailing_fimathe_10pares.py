"""Backtest Fimathe Futures com Trailing Stop Progressivo (Subindo o Stop por Níveis)
Regra solicitada:
  - Posições para os dois lados (COMPRA e VENDA / Long e Short).
  - 1 Nível de rompimento.
  - Quando alcançar o 1º nível: o Stop se desloca para 50% do 1º nível.
  - Quando alcançar a metade do 2º nível: o Stop se desloca para o 1º nível.
  - Quando alcançar o 2º nível: o Stop se desloca para 50% do 2º nível.
  - Quando alcançar a metade do 3º nível: o Stop se desloca para o 2º nível... e assim sucessivamente.
  - Executado nos 10 pares com maior volatilidade recente no mercado futuro da Binance em velas de 5m (72h).
"""
import concurrent.futures
import json
import urllib.request
import pandas as pd
import numpy as np

from strategy import Direcao, EstrategiaFimathe
from backtest import TradeResultado
from fimathe import calcular_atr


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


class BacktestFimatheTrailingStop:
    def __init__(self, estrategia: EstrategiaFimathe, taxa_pct: float = 0.04, slippage_pct: float = 0.01):
        self.estrategia = estrategia
        self.taxa = taxa_pct / 100
        self.slippage = slippage_pct / 100

    def executar(self, df: pd.DataFrame) -> dict:
        trades = []
        posicao = None
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
                
                # --- ATUALIZAÇÃO DO TRAILING STOP PROGRESSIVO ---
                # Níveis de avanço à frente da entrada:
                # Nível 1: p_entrada + 1*amp (compra) ou p_entrada - 1*amp (venda)
                # 50% do Nível 1: p_entrada + 0.5*amp
                # Nível 2: p_entrada + 2*amp
                # Metade do Nível 2: p_entrada + 1.5*amp
                # Nível 3: p_entrada + 3*amp, etc.
                
                maior_favoravel = high_atual if compra else low_atual
                
                # Verifica quantos passos de 0.5 amplitude o preço já percorreu
                if compra:
                    avanco = maior_favoravel - p_entrada
                else:
                    avanco = p_entrada - maior_favoravel

                passos = int(avanco // (0.5 * amplitude)) if amplitude > 0 else 0
                
                # Regra:
                # passo >= 2 (alcançou o 1º nível completo 1.0x): stop vai para 50% do 1º nível (+0.5x)
                # passo >= 3 (alcançou metade do 2º nível 1.5x): stop vai para o 1º nível (+1.0x)
                # passo >= 4 (alcançou o 2º nível 2.0x): stop vai para 50% do 2º nível (+1.5x)
                # passo >= 5 (alcançou metade do 3º nível 2.5x): stop vai para o 2º nível (+2.0x)
                # Fórmula geral para passo >= 2: novo_stop_mult = (passos - 1) * 0.5
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

                # Verificação de encerramento pelo Stop (Stop Loss inicial ou Stop Móvel / Trailing)
                atingiu_stop = (low_atual <= posicao["stop"]) if compra else (high_atual >= posicao["stop"])
                
                # Alvo final máximo de projeção (expansão 4 ou superior)
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
                    posicao = None
                continue

            # 2. Avaliação de novo sinal nos candles fechados (opera COMPRA e VENDA)
            df_hist = df.iloc[:i]
            sinal = self.estrategia.avaliar(df_hist)
            if sinal is None:
                continue

            compra = sinal.direcao == Direcao.COMPRA
            preco_entrada = close_atual * (1 + self.slippage if compra else 1 - self.slippage)
            amplitude = sinal.grade["amplitude"]
            
            # Alvo final largo (nível 4) para deixar o trailing stop conduzir a operação
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

        # Métricas estatísticas
        if not trades:
            return {
                "total_trades": 0, "wins": 0, "losses": 0,
                "win_rate_pct": 0.0, "retorno_total_pct": 0.0,
                "profit_factor": 0.0, "max_drawdown_pct": 0.0,
                "trades": [],
            }

        pnls = [t.pnl_pct for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        wr = (len(wins) / len(trades)) * 100
        ret_total = sum(pnls)
        lucro_bruto = sum(wins)
        preju_bruto = abs(sum(losses))
        pf = (lucro_bruto / preju_bruto) if preju_bruto > 0 else float("inf")

        # Drawdown acumulado
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
        }


def main():
    # 10 Pares mais voláteis com alta liquidez no Binance Futures
    pares = [
        "ALGOUSDT", "TAOUSDT", "NEARUSDT", "SOLUSDT", "ZECUSDT",
        "XRPUSDT", "ETHUSDT", "BTCUSDT", "AVAXUSDT", "LINKUSDT"
    ]

    print("=" * 88)
    print("   BACKTEST FIMATHE FUTURES: 1 NÍVEL + TRAILING STOP PROGRESSIVO DE NÍVEIS")
    print("=" * 88)
    print(f"Pares avaliados (10 mais voláteis/líquidos): {', '.join(pares)}")
    print("Período: 72 horas em velas de 5m (864 candles por par)")
    print("Mecânica do Stop:")
    print("  - Inicial: Fora da caixinha")
    print("  - Ao atingir 1º Nível -> Stop desloca para 50% do 1º Nível")
    print("  - Ao atingir metade do 2º Nível -> Stop desloca para o 1º Nível")
    print("  - Condução contínua protegendo lucro até ser estopado ou atingir alvo final.")
    print("Baixando dados da Binance Futures em paralelo...\n")

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

    # Configuração com 1 Nível de Rompimento
    est = EstrategiaFimathe(
        janela_pivo=3,
        qtd_niveis=4,
        folga_stop=0.10,
        amplitude_min_pct=0.20,
        usar_filtro_50=False,
        modo_stop="fora_caixinha",
        atr_filtro_ativo=False,
        romper_no_canal=True,  # 1 NÍVEL DE ROMPIMENTO
        ker_filtro_ativo=True,  # FILTRO ANTI-WHIPSAW / ANTI-SERROTE (Elimina o padrão do ZEC)
        ker_minimo=0.25,
        ker_periodo=12,
    )
    bt = BacktestFimatheTrailingStop(est, taxa_pct=0.04, slippage_pct=0.01)

    resultados = []
    todos_trades = []

    for p in pares:
        if p not in dfs:
            continue
        res = bt.executar(dfs[p])
        resultados.append({"symbol": p, **res})
        for t in res["trades"]:
            todos_trades.append((p, t))

    print("\n" + "=" * 88)
    print(f"{'PAR':<10} | {'TRADES':<7} | {'W/L':<8} | {'WINRATE':<9} | {'PROFIT LÍQ':<12} | {'PROFIT FACTOR':<14} | {'MAX DD':<8}")
    print("-" * 88)

    for r in resultados:
        wl = f"{r['wins']}W/{r['losses']}L"
        pf_str = f"{r['profit_factor']:.2f}" if r["profit_factor"] != float("inf") else "inf"
        print(f"{r['symbol']:<10} | {r['total_trades']:<7} | {wl:<8} | {r['win_rate_pct']:>7.1f}% | {r['retorno_total_pct']:>+10.2f}% | {pf_str:>13} | {r['max_drawdown_pct']:>6.2f}%")

    print("-" * 88)
    total_trades = sum(r["total_trades"] for r in resultados)
    total_wins = sum(r["wins"] for r in resultados)
    total_losses = sum(r["losses"] for r in resultados)
    pnl_total = sum(r["retorno_total_pct"] for r in resultados)
    wr_total = (total_wins / total_trades * 100) if total_trades > 0 else 0

    print(f"{'TOTAL':<10} | {total_trades:<7} | {total_wins}W/{total_losses}L | {wr_total:>7.1f}% | {pnl_total:>+10.2f}% | {'---':<13} | {'---':<8}")
    print("=" * 88)

    # Exemplo dos melhores trades com trailing stop protegendo lucro
    top_trailing = [t for t in todos_trades if t[1].motivo_saida == "trailing_stop" and t[1].pnl_pct > 0]
    top_trailing.sort(key=lambda x: x[1].pnl_pct, reverse=True)

    print("\n--- DESTAQUES DE TRADES CONDUZIDOS PELO TRAILING STOP PROGRESSIVO ---")
    for sym, t in top_trailing[:10]:
        dur_min = t.duracao_candles * 5
        print(f" • [{sym}] {t.direcao.upper():<4} Entrada: {t.preco_entrada:<9.4f} -> Stop Móvel: {t.preco_saida:<9.4f} | Duração: {dur_min}m | PnL Líquido: {t.pnl_pct:+.2f}%")


if __name__ == "__main__":
    main()
