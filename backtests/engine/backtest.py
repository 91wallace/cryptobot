"""Módulo de Backtesting e Análise Quantitativa Fimathe.

Permite simular o desempenho histórico da estratégia Fimathe com:
  - Dados históricos reais baixados da KuCoin.
  - Métricas estatísticas de performance (Total Trades, Win Rate, Profit Factor,
    Max Drawdown, Payoff Ratio, Retorno Total).
  - Comparação de modos de stop (fora_caixinha vs linha_50 vs rompimento).
  - Análise de impacto do filtro de volatilidade ATR.
  - Exportação dos trades detalhados para visualização ou análise no MATLAB.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from exchange import Exchange
from fimathe import calcular_atr
from risk import GerenciadorRisco, ParametrosRisco
from strategy import Direcao, EstrategiaFimathe

BASE_DIR = Path(__file__).resolve().parent


@dataclass
class TradeResultado:
    indice_entrada: Any
    indice_saida: Any
    direcao: str
    preco_entrada: float
    preco_saida: float
    stop_loss: float
    alvo: float
    motivo_saida: str  # "alvo" ou "stop"
    pnl_pct: float
    duracao_candles: int


class BacktestFimathe:
    def __init__(
        self,
        estrategia: EstrategiaFimathe,
        risco: GerenciadorRisco | None = None,
        taxa_pct: float = 0.1,
        slippage_pct: float = 0.05,
    ) -> None:
        self.estrategia = estrategia
        self.risco = risco or GerenciadorRisco(ParametrosRisco())
        self.taxa = taxa_pct / 100
        self.slippage = slippage_pct / 100

    def executar(self, df: pd.DataFrame) -> dict[str, Any]:
        """Executa backtest candle a candle em uma série OHLCV."""
        trades: list[TradeResultado] = []
        posicao: dict[str, Any] | None = None

        janela_min = 4 * self.estrategia.janela_pivo + 2
        total_candles = len(df)

        for i in range(janela_min, total_candles):
            candle_atual = df.iloc[i]
            high_atual = float(candle_atual["high"])
            low_atual = float(candle_atual["low"])
            close_atual = float(candle_atual["close"])
            ts_atual = df.index[i]

            # 1. Se estiver posicionado, verifica stop ou alvo intra-candle
            if posicao is not None:
                compra = posicao["direcao"] == Direcao.COMPRA
                atingiu_alvo = high_atual >= posicao["alvo"] if compra else low_atual <= posicao["alvo"]
                atingiu_stop = low_atual <= posicao["stop"] if compra else high_atual >= posicao["stop"]

                if atingiu_alvo or atingiu_stop:
                    # Se ambos ocorrerem no mesmo candle, assume stop por conservadorismo
                    if atingiu_stop and atingiu_alvo:
                        motivo = "stop"
                        preco_saida = posicao["stop"] * (1 - self.slippage if compra else 1 + self.slippage)
                    elif atingiu_alvo:
                        motivo = "alvo"
                        preco_saida = posicao["alvo"] * (1 - self.slippage if compra else 1 + self.slippage)
                    else:
                        motivo = "stop"
                        preco_saida = posicao["stop"] * (1 - self.slippage if compra else 1 + self.slippage)

                    # PnL descontando custos
                    fator = 1.0 if compra else -1.0
                    pnl_bruto_pct = ((preco_saida - posicao["entrada"]) / posicao["entrada"]) * 100 * fator
                    custos_pct = (self.taxa * 2) * 100
                    pnl_liq_pct = pnl_bruto_pct - custos_pct

                    duracao = i - posicao["indice_i"]
                    trades.append(
                        TradeResultado(
                            indice_entrada=posicao["ts"],
                            indice_saida=ts_atual,
                            direcao=posicao["direcao"].value,
                            preco_entrada=posicao["entrada"],
                            preco_saida=preco_saida,
                            stop_loss=posicao["stop"],
                            alvo=posicao["alvo"],
                            motivo_saida=motivo,
                            pnl_pct=pnl_liq_pct,
                            duracao_candles=duracao,
                        )
                    )
                    posicao = None
                continue

            # 2. Avalia novo sinal apenas com histórico de candles fechados
            df_historico = df.iloc[:i]
            sinal = self.estrategia.avaliar(df_historico)
            if sinal is None:
                continue

            # Validação pelo módulo de risco
            valido, motivo = self.risco.validar_sinal(sinal, capital=1000.0, posicoes_abertas=0, pnl_dia=0.0)
            if not valido:
                continue

            compra = sinal.direcao == Direcao.COMPRA
            preco_entrada = close_atual * (1 + self.slippage if compra else 1 - self.slippage)

            posicao = {
                "ts": ts_atual,
                "indice_i": i,
                "direcao": sinal.direcao,
                "entrada": preco_entrada,
                "stop": sinal.stop_loss,
                "alvo": sinal.alvos[0],
            }

        return self._calcular_metricas(trades)

    def _calcular_metricas(self, trades: list[TradeResultado]) -> dict[str, Any]:
        if not trades:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "retorno_total_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "media_ganho_pct": 0.0,
                "media_perda_pct": 0.0,
                "payoff": 0.0,
                "trades": [],
            }

        pnls = [t.pnl_pct for t in trades]
        ganhos = [p for p in pnls if p > 0]
        perdas = [abs(p) for p in pnls if p <= 0]

        total_trades = len(trades)
        wins = len(ganhos)
        win_rate = (wins / total_trades) * 100

        soma_ganhos = sum(ganhos)
        soma_perdas = sum(perdas)
        profit_factor = (soma_ganhos / soma_perdas) if soma_perdas > 0 else float("inf")

        # Curva de capital e Drawdown
        capital_curva = [100.0]
        for p in pnls:
            capital_curva.append(capital_curva[-1] * (1 + p / 100))

        pico = capital_curva[0]
        drawdowns = []
        for v in capital_curva:
            if v > pico:
                pico = v
            dd = ((pico - v) / pico) * 100
            drawdowns.append(dd)

        max_dd = max(drawdowns) if drawdowns else 0.0
        retorno_total = capital_curva[-1] - 100.0

        return {
            "total_trades": total_trades,
            "wins": wins,
            "losses": len(perdas),
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor,
            "retorno_total_pct": retorno_total,
            "max_drawdown_pct": max_dd,
            "media_ganho_pct": np.mean(ganhos) if ganhos else 0.0,
            "media_perda_pct": np.mean(perdas) if perdas else 0.0,
            "payoff": (np.mean(ganhos) / np.mean(perdas)) if ganhos and perdas else 0.0,
            "trades": trades,
        }


def formatar_relatorio(res: dict[str, Any], nome_teste: str) -> str:
    return (
        f"\n======================================================\n"
        f" RELATÓRIO DE BACKTEST: {nome_teste}\n"
        f"======================================================\n"
        f" Total de Trades:     {res['total_trades']}\n"
        f" Taxa de Acerto:      {res['win_rate_pct']:.2f}% ({res.get('wins', 0)}W / {res.get('losses', 0)}L)\n"
        f" Profit Factor:       {res['profit_factor']:.2f}\n"
        f" Retorno Total:       {res['retorno_total_pct']:+.2f}%\n"
        f" Max Drawdown:        {res['max_drawdown_pct']:.2f}%\n"
        f" Payoff (Ganho/Perda): {res.get('payoff', 0.0):.2f}\n"
        f"======================================================"
    )


if __name__ == "__main__":
    load_dotenv(BASE_DIR / ".env")
    parser = argparse.ArgumentParser(description="Backtest Fimathe")
    parser.add_argument("--symbol", default="BTC/USDT", help="Par KuCoin (ex: BTC/USDT ou BTC/USDT:USDT)")
    parser.add_argument("--timeframe", default="1h", help="Timeframe (ex: 15m, 1h, 4h)")
    parser.add_argument("--limit", type=int, default=500, help="Quantidade de candles")
    parser.add_argument("--market_type", default="spot", choices=["spot", "futures"], help="Mercado (spot ou futures)")
    parser.add_argument("--export_csv", default="", help="Caminho para exportar trades detalhados")
    args = parser.parse_args()

    exchange = Exchange(market_type=args.market_type, modo="paper")
    print(f"Baixando {args.limit} candles de {args.symbol} ({args.timeframe}) no mercado {args.market_type}...")
    dados_ohlcv = exchange.buscar_candles(args.symbol, args.timeframe, limite=args.limit)

    print("\nExecutando comparativo dos modos de stop...")
    for modo in ["auto_adaptativo", "linha_50", "rompimento", "fora_caixinha"]:
        est = EstrategiaFimathe(
            janela_pivo=5,
            qtd_niveis=3,
            folga_stop=0.1,
            amplitude_min_pct=0.2,
            usar_filtro_50=True,
            modo_stop=modo,
            atr_filtro_ativo=False,
        )
        bt = BacktestFimathe(est)
        resultado = bt.executar(dados_ohlcv)
        print(formatar_relatorio(resultado, f"Modo Stop: {modo}"))

        if args.export_csv and modo == "linha_50" and resultado["trades"]:
            df_trades = pd.DataFrame([t.__dict__ for t in resultado["trades"]])
            caminho = Path(args.export_csv)
            caminho.parent.mkdir(parents=True, exist_ok=True)
            df_trades.to_csv(caminho, index=False)
            print(f"Trades exportados para: {caminho}")
