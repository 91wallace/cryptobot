"""Gerenciamento de risco.

  * Dimensiona a posição pelo risco fixo por trade e pela distância até o
    stop estrutural (stop fora da caixinha).
  * Limita a exposição pela alavancagem máxima.
  * Bloqueia novas entradas por nº de posições, perda diária ou R:R ruim.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from strategy import Direcao, Sinal


@dataclass
class ParametrosRisco:
    risco_por_trade_pct: float = 1.0   # % do capital perdido se o stop for atingido
    alavancagem_max: int = 3           # exposição máxima = capital * alavancagem_max
    max_posicoes_abertas: int = 1
    perda_diaria_max_pct: float = 3.0  # trava o bot no dia ao atingir esta perda
    rr_minimo: float = 1.0             # risco:retorno mínimo até o 1º alvo


class GerenciadorRisco:
    def __init__(self, params: ParametrosRisco | None = None) -> None:
        self.params = params or ParametrosRisco()

    def calcular_quantidade(self, capital: float, entrada: float, stop: float) -> float:
        """Quantidade na moeda base respeitando risco por trade e alavancagem máxima."""
        distancia = abs(entrada - stop)
        if distancia <= 0 or capital <= 0 or entrada <= 0:
            return 0.0
        risco_valor = capital * self.params.risco_por_trade_pct / 100
        qtd_por_risco = risco_valor / distancia
        qtd_max_exposicao = capital * self.params.alavancagem_max / entrada
        return min(qtd_por_risco, qtd_max_exposicao)

    def alavancagem_necessaria(self, quantidade: float, entrada: float, capital: float) -> int:
        if capital <= 0:
            return 1
        alav = math.ceil(quantidade * entrada / capital)
        return max(1, min(alav, self.params.alavancagem_max))

    def validar_sinal(
        self,
        sinal: Sinal,
        capital: float,
        posicoes_abertas: int,
        pnl_dia: float,
    ) -> tuple[bool, str]:
        p = self.params

        if posicoes_abertas >= p.max_posicoes_abertas:
            return False, "limite de posições abertas atingido"

        if capital > 0 and pnl_dia <= -(capital * p.perda_diaria_max_pct / 100):
            return False, "perda diária máxima atingida"

        compra = sinal.direcao is Direcao.COMPRA
        if compra and sinal.stop_loss >= sinal.preco_entrada:
            return False, "stop acima da entrada em compra"
        if not compra and sinal.stop_loss <= sinal.preco_entrada:
            return False, "stop abaixo da entrada em venda"

        if not sinal.alvos:
            return False, "sinal sem alvos"
        risco = abs(sinal.preco_entrada - sinal.stop_loss)
        retorno = abs(sinal.alvos[0] - sinal.preco_entrada)
        # Tolerância de 10% (fator 1.1) para evitar que operações válidas sejam
        # descartadas por margens minúsculas de arredondamento/precisão.
        tolerancia_rr = 0.90  # aceita se retorno/risco for pelo menos 90% do mínimo exigido (ex: 1.0 -> 0.9)
        rr_efetivo = retorno / risco if risco > 0 else 0.0
        if risco <= 0 or rr_efetivo < (p.rr_minimo * tolerancia_rr):
            return False, f"R:R insuficiente ({rr_efetivo:.2f})"

        return True, "ok"
