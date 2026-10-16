"""Módulo de Circuit Breaker Temporal (Proteção Anti-Choppiness por Par).
Regras implementadas:
  1. Se sofrer 2 STOPS consecutivos em até 6 horas: suspende operações no par por 6 horas.
  2. Se fizer padrão de alternância (1 Gain / 1 Stop / 1 Gain / 1 Stop) ou (1 Stop / 1 Gain / 1 Stop)
     dentro de 4 horas: suspende operações no par por 6 horas.
"""
from dataclasses import dataclass
from typing import List
import pandas as pd


@dataclass
class RegistroTrade:
    timestamp_saida: pd.Timestamp
    resultado: str  # "gain" ou "loss"


class CircuitBreakerPar:
    def __init__(self, horas_base: float = 6.0, horas_reincidencia: float = 24.0):
        self.horas_base = pd.Timedelta(hours=horas_base)
        self.horas_reincidencia = pd.Timedelta(hours=horas_reincidencia)
        self.historico: List[RegistroTrade] = []
        self.suspenso_ate: pd.Timestamp | None = None
        self.total_bloqueios = 0
        self.motivo_ultimo_bloqueio = ""
        self.ultimo_termino_suspensao: pd.Timestamp | None = None

    def registrar_trade(self, ts_saida: pd.Timestamp, pnl_pct: float, motivo_saida: str = ""):
        # Se saiu com stop móvel no lucro ou pnl positivo, é considerado GAIN
        resultado = "gain" if (pnl_pct > 0 or motivo_saida == "trailing_stop") else "loss"
        self.historico.append(RegistroTrade(timestamp_saida=ts_saida, resultado=resultado))
        self._verificar_gatilhos(ts_saida)

    def esta_suspenso(self, ts_atual: pd.Timestamp) -> tuple[bool, str]:
        if self.suspenso_ate is not None:
            if ts_atual < self.suspenso_ate:
                tempo_restante_m = int((self.suspenso_ate - ts_atual).total_seconds() // 60)
                return True, f"{self.motivo_ultimo_bloqueio} (restam {tempo_restante_m}m)"
            else:
                # O período de suspensão expirou: marca o término
                if self.ultimo_termino_suspensao is None or self.suspenso_ate > self.ultimo_termino_suspensao:
                    self.ultimo_termino_suspensao = self.suspenso_ate
                self.suspenso_ate = None

        return False, ""

    def _aplicar_bloqueio(self, ts_atual: pd.Timestamp, motivo: str):
        self.total_bloqueios += 1
        # Se já houve suspensão anterior e reincidiu, o tempo de bloqueio é estendido para 24h
        if self.total_bloqueios > 1:
            duracao = self.horas_reincidencia
            tipo = "REINCIDÊNCIA (24h)"
        else:
            duracao = self.horas_base
            tipo = "1º BLOQUEIO (6h)"

        self.suspenso_ate = ts_atual + duracao
        self.motivo_ultimo_bloqueio = f"[{tipo}] {motivo}"

    def _verificar_gatilhos(self, ts_atual: pd.Timestamp):
        if len(self.historico) < 2:
            return

        # REGRA 1: 2 stops consecutivos em até 6 horas
        ultimos_2 = self.historico[-2:]
        if ultimos_2[0].resultado == "loss" and ultimos_2[1].resultado == "loss":
            delta_tempo = ultimos_2[1].timestamp_saida - ultimos_2[0].timestamp_saida
            if delta_tempo <= pd.Timedelta(hours=6):
                self._aplicar_bloqueio(ts_atual, f"2 stops consecutivos em {delta_tempo.total_seconds()/3600:.1f}h")
                return

        # REGRA 2: Padrão (1 stop, 1 gain, 1 stop) em até 4 horas
        if len(self.historico) >= 3:
            ultimos_3 = self.historico[-3:]
            padrao_3 = [t.resultado for t in ultimos_3]
            if padrao_3 == ["loss", "gain", "loss"]:
                delta_tempo = ultimos_3[-1].timestamp_saida - ultimos_3[0].timestamp_saida
                if delta_tempo <= pd.Timedelta(hours=4):
                    self._aplicar_bloqueio(ts_atual, f"Padrão Loss/Gain/Loss em {delta_tempo.total_seconds()/3600:.1f}h")
                    return

        # REGRA 3: Padrão (1 gain, 1 stop, 1 gain, 1 stop) em até 4 horas
        if len(self.historico) >= 4:
            ultimos_4 = self.historico[-4:]
            padrao_4 = [t.resultado for t in ultimos_4]
            if padrao_4 == ["gain", "loss", "gain", "loss"]:
                delta_tempo = ultimos_4[-1].timestamp_saida - ultimos_4[0].timestamp_saida
                if delta_tempo <= pd.Timedelta(hours=4):
                    self._aplicar_bloqueio(ts_atual, f"Padrão Gain/Loss/Gain/Loss em {delta_tempo.total_seconds()/3600:.1f}h")
                    return
