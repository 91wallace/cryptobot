"""Motor matemático central da estratégia Fimathe.

Implementa:
  1. Grade geométrica clássica (Canais de Referência, Zonas Neutras, Linha de 50% e Expansões).
  2. Métricas de volatilidade (ATR - Average True Range).
  3. Calibração estatística da amplitude e folga adaptativa de stop.
  4. Mapeamento de níveis proporcionais/Fibonacci intra e inter-canal.
"""
from __future__ import annotations

import math
from typing import Sequence
import pandas as pd
import numpy as np


def calcular_atr(df: pd.DataFrame, periodo: int = 14) -> pd.Series:
    """Calcula o Average True Range (ATR) para medição de volatilidade."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=periodo, min_periods=1).mean()
    return atr


def calcular_grade_fimathe(
    preco_fundo: float,
    preco_topo: float,
    qtd_niveis: int = 3,
    proporcoes: Sequence[float] | None = None,
) -> dict:
    """Calcula a amplitude, linha de 50%, níveis de retração interna e expansões sucessivas.

    Args:
        preco_fundo: Preço de suporte do canal de referência.
        preco_topo: Preço de resistência do canal de referência.
        qtd_niveis: Quantidade de expansões projetadas acima e abaixo.
        proporcoes: Lista de multiplicadores para cada nível (padrão: 1.0 para cada nível).
    """
    amplitude = preco_topo - preco_fundo
    meio_canal = preco_fundo + (amplitude * 0.5)

    # Níveis de retração internos da caixinha (Fibonacci / fractais)
    retracoes_internas = {
        "23.6": preco_fundo + (amplitude * 0.236),
        "38.2": preco_fundo + (amplitude * 0.382),
        "50.0": meio_canal,
        "61.8": preco_fundo + (amplitude * 0.618),
        "76.4": preco_fundo + (amplitude * 0.764),
    }

    canais_alta = []
    canais_baixa = []

    # Se proporções personalizadas não foram passadas, usa passos unitários (1, 2, 3...)
    if proporcoes is None:
        mults = [float(i) for i in range(1, qtd_niveis + 1)]
    else:
        mults = list(proporcoes)[:qtd_niveis]
        while len(mults) < qtd_niveis:
            mults.append(mults[-1] + 1.0)

    # Expansões iterativas
    base_alta = preco_topo
    for i, mult in enumerate(mults, start=1):
        delta = amplitude * (mult if proporcoes else 1.0)
        fundo_alta = base_alta
        topo_alta = base_alta + (amplitude if not proporcoes else delta)
        canais_alta.append({
            "nivel": i,
            "fundo": fundo_alta,
            "topo": topo_alta,
            "meio": (fundo_alta + topo_alta) / 2,
        })
        base_alta = topo_alta

    base_baixa = preco_fundo
    for i, mult in enumerate(mults, start=1):
        fundo_baixa = base_baixa - (amplitude if not proporcoes else (amplitude * mult))
        topo_baixa = base_baixa
        canais_baixa.append({
            "nivel": i,
            "fundo": fundo_baixa,
            "topo": topo_baixa,
            "meio": (fundo_baixa + topo_baixa) / 2,
        })
        base_baixa = fundo_baixa

    return {
        "amplitude": amplitude,
        "linha_50": meio_canal,
        "canal_referencia": (preco_fundo, preco_topo),
        "retracoes_internas": retracoes_internas,
        "expansoes_alta": canais_alta,
        "expansoes_baixa": canais_baixa,
    }


def calibrar_amplitude_volatilidade(
    amplitude_original: float,
    atr_atual: float,
    fator_min_atr: float = 0.8,
    fator_max_atr: float = 3.5,
) -> tuple[float, bool]:
    """Valida ou ajusta a amplitude do canal contra a volatilidade real (ATR).

    Retorna:
        (amplitude_ajustada, dentro_da_faixa_otima)
    """
    if atr_atual <= 0:
        return amplitude_original, True

    ratio = amplitude_original / atr_atual
    valido = fator_min_atr <= ratio <= fator_max_atr
    return amplitude_original, valido


def calcular_metricas_rompimento(df: pd.DataFrame, periodo_vol: int = 20, periodo_ker: int = 10) -> dict[str, float]:
    """Calcula variáveis quantitativas para classificar a qualidade de um rompimento:
      - rvol: Volume relativo contra média móvel.
      - bar_strength: Razão corpo da barra / amplitude total da barra (convicção).
      - ker: Kaufman Efficiency Ratio (direcionalidade vs ruído).
      - atr_ratio: Razão entre ATR atual e ATR médio histórico.
    """
    if len(df) < max(periodo_vol, periodo_ker) + 2:
        return {"rvol": 1.0, "bar_strength": 0.5, "ker": 0.5, "atr_ratio": 1.0}

    ult = df.iloc[-1]
    vol_atual = float(ult["volume"])
    vol_medio = float(df["volume"].rolling(periodo_vol).mean().iloc[-2]) if periodo_vol > 0 else vol_atual
    rvol = (vol_atual / vol_medio) if vol_medio > 0 else 1.0

    high_low = float(ult["high"] - ult["low"])
    corpo = abs(float(ult["close"] - ult["open"]))
    bar_strength = (corpo / high_low) if high_low > 0 else 0.5

    # Kaufman Efficiency Ratio (KER)
    precos = df["close"].tail(periodo_ker)
    deslocamento = abs(float(precos.iloc[-1] - precos.iloc[0]))
    caminho = float(precos.diff().abs().sum())
    ker = (deslocamento / caminho) if caminho > 0 else 0.5

    atr_s = calcular_atr(df, 14)
    atr_atual = float(atr_s.iloc[-1]) if not atr_s.empty else 1.0
    atr_medio = float(atr_s.tail(30).mean()) if len(atr_s) >= 30 else atr_atual
    atr_ratio = (atr_atual / atr_medio) if atr_medio > 0 else 1.0

    return {
        "rvol": float(rvol),
        "bar_strength": float(bar_strength),
        "ker": float(ker),
        "atr_ratio": float(atr_ratio),
    }


def selecionar_melhor_modo_stop(metricas: dict[str, float]) -> tuple[str | None, str]:
    """Classificador estatístico de regime de mercado:
    Seleciona dinamicamente a melhor estratégia de entrada e stop,
    ou aborta a operação se for identificado provável falso rompimento.

    Retorna:
        (modo_escolhido, motivo_estatistico)
    """
    rvol = metricas.get("rvol", 1.0)
    bar_strength = metricas.get("bar_strength", 0.5)
    ker = metricas.get("ker", 0.5)
    atr_ratio = metricas.get("atr_ratio", 1.0)

    # 1. Filtro de Falso Rompimento: volume anêmico e barra sem convicção
    if rvol < 0.80 and bar_strength < 0.40:
        return None, f"falso rompimento provavel (RVOL={rvol:.2f}, ForcaBarra={bar_strength:.2f})"

    # 2. Rompimento Institucional / Explosivo: volume alto + barra forte
    # Stop curto (rompimento) é o ideal pois o preço tende a não recuar
    if rvol >= 1.40 and bar_strength >= 0.65:
        return "rompimento", f"momentum explosivo (RVOL={rvol:.2f}, ForcaBarra={bar_strength:.2f})"

    # 3. Mercado Ruidoso ou com Choque de Volatilidade:
    # Stop amplo 1:1 (fora_caixinha) para evitar violinadas
    if ker < 0.35 or atr_ratio > 1.35:
        return "fora_caixinha", f"alta volatilidade/ruido (KER={ker:.2f}, RazaoATR={atr_ratio:.2f})"

    # 4. Tendência Saudável Regular:
    # Linha de 50% para suportar reteste normal
    return "linha_50", f"tendencia equilibrada (RVOL={rvol:.2f}, KER={ker:.2f})"


if __name__ == "__main__":
    # Teste rápido
    grade = calcular_grade_fimathe(preco_fundo=100.0, preco_topo=110.0, qtd_niveis=2)
    print(f"Amplitude: {grade['amplitude']}")
    print(f"Linha de 50%: {grade['linha_50']}")
    print("Retrações internas:", grade["retracoes_internas"])
    print("Expansões de alta:")
    for a in grade["expansoes_alta"]:
        print(f"  Nível {a['nivel']}: [{a['fundo']:.2f} -> {a['topo']:.2f}] (50%: {a['meio']:.2f})")
