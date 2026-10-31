"""Lógica da estratégia Fimathe.

Etapas:
  1. Detecta pivôs (topos e fundos) por fractais.
  2. Define o Canal de Referência (último fundo e último topo confirmados).
  3. Projeta a grade com `fimathe.calcular_grade_fimathe` (zonas neutras, alvos, linha de 50%).
  4. Gera sinal quando o preço rompe a Zona Neutra a favor da tendência,
     com stop configurável (fora da caixinha / linha de 50% / rompimento)
     e alvos nas expansões seguintes.

As regras abaixo são uma primeira versão parametrizável e devem ser
validadas em backtest antes de operar com capital real.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

import pandas as pd

from fimathe import (
    calcular_grade_fimathe,
    calcular_atr,
    calibrar_amplitude_volatilidade,
    calcular_metricas_rompimento,
    selecionar_melhor_modo_stop,
)

MODOS_STOP = ("fora_caixinha", "linha_50", "rompimento", "auto_adaptativo")

from strategies.base import BaseStrategy, StrategyRegistry, Direcao, Sinal


# ---------------------------------------------------------------------- #
# Pivôs / estrutura de mercado
# ---------------------------------------------------------------------- #
def detectar_pivos(df: pd.DataFrame, janela: int = 5) -> pd.DataFrame:
    """Marca topos/fundos: um candle é topo (fundo) se sua máxima (mínima) é a
    maior (menor) dentro de ±`janela` candles. Os últimos `janela` candles
    ficam sem classificação, pois ainda não foram confirmados."""
    tamanho = 2 * janela + 1
    maximas = df["high"].rolling(tamanho, center=True).max()
    minimas = df["low"].rolling(tamanho, center=True).min()
    out = df.copy()
    out["pivo_topo"] = df["high"].eq(maximas)
    out["pivo_fundo"] = df["low"].eq(minimas)
    return out


def identificar_canal_referencia(df_pivos: pd.DataFrame) -> tuple[float, float] | None:
    """Retorna (fundo, topo) do canal de referência a partir dos últimos pivôs."""
    topos = df_pivos.loc[df_pivos["pivo_topo"], "high"]
    fundos = df_pivos.loc[df_pivos["pivo_fundo"], "low"]
    if topos.empty or fundos.empty:
        return None
    fundo, topo = float(fundos.iloc[-1]), float(topos.iloc[-1])
    if fundo >= topo:
        return None
    return fundo, topo


def identificar_tendencia(df_pivos: pd.DataFrame) -> Direcao | None:
    """Topos e fundos ascendentes => alta; descendentes => baixa; senão lateral (None)."""
    topos = df_pivos.loc[df_pivos["pivo_topo"], "high"].tail(2)
    fundos = df_pivos.loc[df_pivos["pivo_fundo"], "low"].tail(2)
    if len(topos) < 2 or len(fundos) < 2:
        return None
    if topos.iloc[-1] > topos.iloc[-2] and fundos.iloc[-1] > fundos.iloc[-2]:
        return Direcao.COMPRA
    if topos.iloc[-1] < topos.iloc[-2] and fundos.iloc[-1] < fundos.iloc[-2]:
        return Direcao.VENDA
    return None


# ---------------------------------------------------------------------- #
# Estratégia
# ---------------------------------------------------------------------- #
class EstrategiaFimathe(BaseStrategy):
    strategy_id: ClassVar[str] = "STRAT_APEX_DINAMICS_V1"
    name: str = "apex_dinamics"
    display_name: str = "Apex Dinamics (Lite-3.8)"
    default_symbol: str = "SOL/USDT:USDT"
    default_timeframe: str = "5m"

    def __init__(
        self,
        janela_pivo: int = 3,
        qtd_niveis: int = 4,
        folga_stop: float = 0.10,
        amplitude_min_pct: float = 0.30,
        amplitude_max_pct: float = 1.0,
        usar_filtro_50: bool = False,
        modo_stop: str = "fora_caixinha",
        atr_filtro_ativo: bool = False,
        atr_periodo: int = 14,
        fator_min_atr: float = 0.8,
        fator_max_atr: float = 3.5,
        romper_no_canal: bool = True,
        ker_filtro_ativo: bool = False,
        ker_minimo: float = 0.25,
        ker_periodo: int = 12,
    ) -> None:
        """
        janela_pivo:       candles de cada lado para confirmar um pivô.
        qtd_niveis:        nº de expansões projetadas (mín. 2: zona neutra + alvo).
        folga_stop:        fração da amplitude somada além do nível de stop.
        amplitude_min_pct: amplitude mínima do canal (% do preço) para operar.
        usar_filtro_50:    exige que o candle anterior ao rompimento tenha
                           fechado acima/abaixo da linha de 50% da zona neutra.
        modo_stop:         onde fica o stop, em relação à zona neutra rompida:
                             "fora_caixinha" -> extremo oposto da zona (~1 amplitude, mais largo)
                             "linha_50"      -> linha de 50% da zona (~0.5 amplitude, padrão)
                             "rompimento"    -> logo atrás do nível rompido (mais curto)
        atr_filtro_ativo:  se True, valida se a amplitude da caixinha está condizente com o ATR.
        atr_periodo:       período para cálculo do ATR.
        fator_min_atr:     mínimo aceitável para amplitude / ATR.
        fator_max_atr:     máximo aceitável para amplitude / ATR.
        romper_no_canal:   se True, rompe logo no 1º nível (topo/fundo do canal de referência),
                           sem esperar romper a zona neutra completa (2º nível).
        ker_filtro_ativo:  se True, descarta rompimentos em mercados serrilhados/ruidosos (anti-choppy).
        ker_minimo:        eficiência mínima de deslocamento (0.0 a 1.0, padrão 0.25).
        ker_periodo:       janela de candles para medição da direcionalidade (padrão 12).
        """
        if qtd_niveis < 2:
            raise ValueError("qtd_niveis deve ser >= 2 (zona neutra + ao menos um alvo)")
        if modo_stop not in MODOS_STOP:
            raise ValueError(f"modo_stop inválido: {modo_stop!r} (opções: {', '.join(MODOS_STOP)})")
        self.janela_pivo = janela_pivo
        self.qtd_niveis = qtd_niveis
        self.folga_stop = folga_stop
        self.amplitude_min_pct = amplitude_min_pct
        self.amplitude_max_pct = amplitude_max_pct
        self.usar_filtro_50 = usar_filtro_50
        self.modo_stop = modo_stop
        self.atr_filtro_ativo = atr_filtro_ativo
        self.atr_periodo = atr_periodo
        self.fator_min_atr = fator_min_atr
        self.fator_max_atr = fator_max_atr
        self.romper_no_canal = romper_no_canal
        self.ker_filtro_ativo = ker_filtro_ativo
        self.ker_minimo = ker_minimo
        self.ker_periodo = ker_periodo

    def _calcular_stop(
        self,
        zona: dict,
        direcao: Direcao,
        amplitude: float,
        preco_entrada: float | None = None,
        alvo_1: float | None = None,
        modo_efetivo: str | None = None,
    ) -> float:
        meio = (zona["fundo"] + zona["topo"]) / 2
        compra = direcao is Direcao.COMPRA
        modo = modo_efetivo or self.modo_stop

        if modo == "fora_caixinha":
            # Garante relação de 1 para 1 em relação ao primeiro alvo para evitar bloqueio por R:R
            if preco_entrada is not None and alvo_1 is not None:
                distancia_alvo = abs(alvo_1 - preco_entrada)
                return preco_entrada - distancia_alvo if compra else preco_entrada + distancia_alvo
            nivel = zona["fundo"] if compra else zona["topo"]
        elif modo == "linha_50":
            nivel = meio
        else:  # "rompimento"
            nivel = zona["topo"] if compra else zona["fundo"]
        folga = self.folga_stop * amplitude
        return nivel - folga if compra else nivel + folga

    def avaliar(self, df: pd.DataFrame) -> Sinal | None:
        """Avalia candles FECHADOS e retorna um Sinal ou None."""
        if len(df) < 4 * self.janela_pivo + 2:
            return None

        pivos = detectar_pivos(df, self.janela_pivo)
        canal = identificar_canal_referencia(pivos)
        if canal is None:
            return None

        fundo, topo = canal
        amplitude = topo - fundo
        amplitude_pct = (amplitude / topo) * 100

        if amplitude_pct < self.amplitude_min_pct:
            return None  # caixinha estreita demais (ruído / custos)

        if self.atr_filtro_ativo:
            atr_s = calcular_atr(df, self.atr_periodo)
            atr_val = float(atr_s.iloc[-1]) if not atr_s.empty else 0.0
            _, valido = calibrar_amplitude_volatilidade(
                amplitude,
                atr_val,
                self.fator_min_atr,
                self.fator_max_atr,
            )
            if not valido:
                return None  # amplitude desproporcional à volatilidade do mercado

        # Filtro Anti-Ruído / Anti-Whipsaw (Kaufman Efficiency Ratio - KER)
        # Identifica e bloqueia o padrão do ZEC (mercado com zigue-zague violento sem deslocamento)
        if self.ker_filtro_ativo:
            if len(df) >= self.ker_periodo + 1:
                precos = df["close"].tail(self.ker_periodo)
                deslocamento = abs(float(precos.iloc[-1] - precos.iloc[0]))
                caminho_total = float(precos.diff().abs().sum())
                ker = (deslocamento / caminho_total) if caminho_total > 0 else 0.0
                if ker < self.ker_minimo:
                    return None  # Mercado em serrote/choppy: bloqueia a entrada

        tendencia = identificar_tendencia(pivos)
        if tendencia is None:
            return None

        grade = calcular_grade_fimathe(fundo, topo, self.qtd_niveis)
        amplitude = grade["amplitude"]
        fechamento = float(df["close"].iloc[-1])
        anterior = float(df["close"].iloc[-2])

        # Se for modo adaptativo, extrai as variáveis estatísticas do rompimento
        modo_efetivo = self.modo_stop
        motivo_extra = ""
        if self.modo_stop == "auto_adaptativo":
            metricas = calcular_metricas_rompimento(df)
            modo_escolhido, motivo_est = selecionar_melhor_modo_stop(metricas)
            if modo_escolhido is None:
                return None  # falso rompimento descartado pelo filtro quantitativo
            modo_efetivo = modo_escolhido
            motivo_extra = f" | {motivo_est}"

        if tendencia is Direcao.COMPRA:
            if self.romper_no_canal:
                # 1 NÍVEL DE ROMPIMENTO: rompe diretamente o topo do canal de referência
                nivel_gatilho = topo
                rompeu = anterior <= nivel_gatilho < fechamento
                zona_stop = {"fundo": fundo, "topo": topo}
                nome_romp = f"Rompimento do canal de referência de alta ({nivel_gatilho:.8g})"
            else:
                # 2 NÍVEIS DE ROMPIMENTO (Clássico): espera romper a Zona Neutra
                zona = grade["expansoes_alta"][0]
                nivel_gatilho = zona["topo"]
                meio_zona = (zona["fundo"] + zona["topo"]) / 2
                rompeu = anterior <= nivel_gatilho < fechamento
                if self.usar_filtro_50 and anterior < meio_zona:
                    rompeu = False
                zona_stop = zona
                nome_romp = f"Rompimento da zona neutra de alta ({nivel_gatilho:.8g})"

            if rompeu:
                alvos = [c["topo"] for c in grade["expansoes_alta"] if c["topo"] > fechamento]
                if not alvos:
                    alvos = [fechamento + (amplitude * i) for i in range(1, self.qtd_niveis)]
                alvo_1 = alvos[0] if alvos else (fechamento + amplitude)
                stop = self._calcular_stop(zona_stop, Direcao.COMPRA, amplitude, fechamento, alvo_1, modo_efetivo)
                return Sinal(
                    direcao=Direcao.COMPRA,
                    preco_entrada=fechamento,
                    stop_loss=stop,
                    alvos=alvos,
                    motivo=f"{nome_romp} | stop={modo_efetivo}{motivo_extra}",
                    grade=grade,
                    strategy_id=self.strategy_id,
                    strategy_name=self.name,
                )

        if tendencia is Direcao.VENDA:
            if self.romper_no_canal:
                # 1 NÍVEL DE ROMPIMENTO: rompe diretamente o fundo do canal de referência
                nivel_gatilho = fundo
                rompeu = anterior >= nivel_gatilho > fechamento
                zona_stop = {"fundo": fundo, "topo": topo}
                nome_romp = f"Rompimento do canal de referência de baixa ({nivel_gatilho:.8g})"
            else:
                # 2 NÍVEIS DE ROMPIMENTO (Clássico): espera romper a Zona Neutra
                zona = grade["expansoes_baixa"][0]
                nivel_gatilho = zona["fundo"]
                meio_zona = (zona["fundo"] + zona["topo"]) / 2
                rompeu = anterior >= nivel_gatilho > fechamento
                if self.usar_filtro_50 and anterior > meio_zona:
                    rompeu = False
                zona_stop = zona
                nome_romp = f"Rompimento da zona neutra de baixa ({nivel_gatilho:.8g})"

            if rompeu:
                alvos = [c["fundo"] for c in grade["expansoes_baixa"] if c["fundo"] < fechamento]
                if not alvos:
                    alvos = [fechamento - (amplitude * i) for i in range(1, self.qtd_niveis)]
                alvo_1 = alvos[0] if alvos else (fechamento - amplitude)
                stop = self._calcular_stop(zona_stop, Direcao.VENDA, amplitude, fechamento, alvo_1, modo_efetivo)
                return Sinal(
                    direcao=Direcao.VENDA,
                    preco_entrada=fechamento,
                    stop_loss=stop,
                    alvos=alvos,
                    motivo=f"{nome_romp} | stop={modo_efetivo}{motivo_extra}",
                    grade=grade,
                    strategy_id=self.strategy_id,
                    strategy_name=self.name,
                )

        return None

    def calcular_novo_stop(self, p: Any, preco: float, fator_degrau: float = 1.0) -> float | None:
        """Conduz o stop por subníveis dinâmicos (Apex Dinamics / Lite-3.8):
        a cada degrau de `fator_degrau * amplitude` a favor, o stop sobe um degrau atrás."""
        amplitude = getattr(p, "amplitude", 0.0)
        if amplitude <= 0:
            return None
        compra = p.side == "buy"
        base = getattr(p, "extremo", None) or p.entrada
        extremo = max(base, preco) if compra else min(base, preco)
        p.extremo = extremo

        degrau = amplitude * fator_degrau
        avanco = (p.extremo - p.entrada) if compra else (p.entrada - p.extremo)
        passos = int(avanco // degrau) if degrau > 0 else 0
        if passos >= 2:
            mult = (passos - 1) * fator_degrau
            novo = p.entrada + mult * amplitude if compra else p.entrada - mult * amplitude
            if (novo > p.stop) if compra else (novo < p.stop):
                return novo
        return None


# Identidade Oficial: Apex Dinamics
EstrategiaApexDinamics = EstrategiaFimathe
ApexDinamicsStrategy = EstrategiaFimathe

# Registro Oficial no StrategyRegistry
StrategyRegistry.register("apex_dinamics")(EstrategiaFimathe)
StrategyRegistry.register("fimathe")(EstrategiaFimathe)
StrategyRegistry.register("lite-3.8")(EstrategiaFimathe)


