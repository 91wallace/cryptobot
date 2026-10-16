"""Motor de Estratégia Fimathe para Mercado Consolidado (Mean Reversion / Contrarian).

Baseia-se na mesma geometria e cálculo de caixas do fimathe.py,
porém opera na PONTA INVERSA para capturar a rejeição de extremos em consolidação:
  - Quando o preço atinge ou tenta romper a região superior da caixinha (resistência / zona alta),
    em vez de comprar o rompimento, a estratégia VENDE (Short / Reversão à Média),
    buscando como alvo o centro da consolidação (Linha de 50%) ou o suporte inferior,
    com Stop Loss curto logo acima da caixinha rompida.
  - Quando o preço atinge o suporte inferior (zona baixa), em vez de vender,
    a estratégia COMPRA (Long), buscando o centro ou a resistência oposta.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import pandas as pd
import numpy as np

from fimathe import (
    calcular_grade_fimathe,
    calcular_atr,
    calcular_metricas_rompimento,
)
from strategy import Direcao, Sinal, detectar_pivos, identificar_canal_referencia


class EstrategiaFimatheConsolidado:
    def __init__(
        self,
        janela_pivo: int = 3,
        qtd_niveis: int = 3,
        folga_stop: float = 0.10,
        amplitude_min_pct: float = 0.10,
        alvo_modo: str = "linha_50",  # "linha_50" ou "canal_oposto"
    ) -> None:
        """
        janela_pivo:       período do fractal para delimitar o canal de consolidação.
        qtd_niveis:        níveis de projeção geométrica.
        folga_stop:        folga percentual acima/abaixo do extremo para o Stop Loss.
        amplitude_min_pct: amplitude mínima do canal (%) para garantir lucro após taxas.
        alvo_modo:         "linha_50" (alvo no centro do canal) ou "canal_oposto" (alvo no extremo oposto).
        """
        self.janela_pivo = janela_pivo
        self.qtd_niveis = qtd_niveis
        self.folga_stop = folga_stop
        self.amplitude_min_pct = amplitude_min_pct
        self.alvo_modo = alvo_modo

    def avaliar(self, df: pd.DataFrame) -> Sinal | None:
        """Avalia candles e gera sinal INVERSO caso o preço teste os extremos da consolidação."""
        if len(df) < 4 * self.janela_pivo + 2:
            return None

        pivos = detectar_pivos(df, self.janela_pivo)
        canal = identificar_canal_referencia(pivos)
        if canal is None:
            return None

        fundo, topo = canal
        amplitude = topo - fundo
        if (amplitude / topo) * 100 < self.amplitude_min_pct:
            return None

        grade = calcular_grade_fimathe(fundo, topo, self.qtd_niveis)
        linha_50 = grade["linha_50"]
        folga = self.folga_stop * amplitude

        fechamento = float(df["close"].iloc[-1])
        anterior = float(df["close"].iloc[-2])
        maxima_atual = float(df["high"].iloc[-1])
        minima_atual = float(df["low"].iloc[-1])

        # Zona superior (Resistência): Preço furou ou testou o topo, mas o candle FECHOU EM BAIXA (Rejeição / Pavio Superior)
        furou_topo = maxima_atual >= topo
        fechou_abaixo_topo = fechamento <= topo
        candle_baixa = fechamento < float(df["open"].iloc[-1])
        
        if furou_topo and fechou_abaixo_topo and candle_baixa:
            alvo_principal = linha_50 if self.alvo_modo == "linha_50" else fundo
            stop_loss = maxima_atual + folga
            
            if stop_loss > fechamento and alvo_principal < fechamento:
                return Sinal(
                    direcao=Direcao.VENDA,
                    preco_entrada=fechamento,
                    stop_loss=stop_loss,
                    alvos=[alvo_principal, fundo],
                    motivo=f"Rejeicao/Falso Rompimento Topo ({topo:.2f}) -> Short",
                    grade=grade,
                )

        # Zona inferior (Suporte): Preço furou ou testou o fundo, mas o candle FECHOU EM ALTA (Rejeição / Martelo)
        furou_fundo = minima_atual <= fundo
        fechou_acima_fundo = fechamento >= fundo
        candle_alta = fechamento > float(df["open"].iloc[-1])
        
        if furou_fundo and fechou_acima_fundo and candle_alta:
            alvo_principal = linha_50 if self.alvo_modo == "linha_50" else topo
            stop_loss = minima_atual - folga
            
            if stop_loss < fechamento and alvo_principal > fechamento:
                return Sinal(
                    direcao=Direcao.COMPRA,
                    preco_entrada=fechamento,
                    stop_loss=stop_loss,
                    alvos=[alvo_principal, topo],
                    motivo=f"Rejeicao/Falso Rompimento Fundo ({fundo:.2f}) -> Long",
                    grade=grade,
                )

        return None
