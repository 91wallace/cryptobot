"""Módulos de estratégias quantitativas e técnicas (Apex Dinamics, Fimathe)."""
from .strategy import (
    Direcao,
    Sinal,
    EstrategiaFimathe,
    detectar_pivos,
    identificar_canal_referencia,
    identificar_tendencia,
)
from .fimathe import (
    calcular_grade_fimathe,
    calcular_atr,
    calibrar_amplitude_volatilidade,
    calcular_metricas_rompimento,
    selecionar_melhor_modo_stop,
)
from .fimathe_consolidado import EstrategiaFimatheConsolidado

__all__ = [
    "Direcao",
    "Sinal",
    "EstrategiaFimathe",
    "detectar_pivos",
    "identificar_canal_referencia",
    "identificar_tendencia",
    "calcular_grade_fimathe",
    "calcular_atr",
    "calibrar_amplitude_volatilidade",
    "calcular_metricas_rompimento",
    "selecionar_melhor_modo_stop",
    "EstrategiaFimatheConsolidado",
]
