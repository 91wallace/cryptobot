"""Interface base e registro para estratégias de trading (BaseStrategy).

Padroniza o contrato de estratégias no CryptoBot para suporte multi-estratégia.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Type
import pandas as pd


class Direcao(str, Enum):
    COMPRA = "buy"
    VENDA = "sell"


@dataclass
class Sinal:
    direcao: Direcao
    preco_entrada: float
    stop_loss: float
    alvos: list[float]
    motivo: str
    grade: dict = field(repr=False, default_factory=dict)
    strategy_id: str = "STRAT_BASE"
    strategy_name: str = "base"


class BaseStrategy(ABC):
    """Contrato base que toda estratégia deve implementar."""

    strategy_id: ClassVar[str] = "STRAT_BASE"
    name: ClassVar[str] = "base"
    display_name: ClassVar[str] = "Base Strategy"
    default_symbol: ClassVar[str] = "SOL/USDT:USDT"
    default_timeframe: ClassVar[str] = "5m"

    @abstractmethod
    def avaliar(self, df: pd.DataFrame) -> Sinal | None:
        """Avalia candles OHLCV fechados e retorna um Sinal ou None."""
        pass

    def calcular_novo_stop(self, posicao: Any, preco_atual: float) -> float | None:
        """Calcula o novo nível de trailing stop para a posição aberta.
        
        Retorna o novo preço de stop se ele deve ser atualizado, ou None caso contrário.
        """
        return None


class StrategyRegistry:
    """Registro e Factory dinâmico de estratégias."""

    _registry: ClassVar[dict[str, Type[BaseStrategy]]] = {}

    @classmethod
    def register(cls, name: str | None = None):
        """Decorator para registrar uma classe de estratégia."""
        def decorator(subclass: Type[BaseStrategy]):
            key = (name or subclass.name).strip().lower()
            cls._registry[key] = subclass
            return subclass
        return decorator

    @classmethod
    def get(cls, name: str, **kwargs) -> BaseStrategy:
        """Instancia a estratégia pelo identificador."""
        key = name.strip().lower()
        if key not in cls._registry:
            raise ValueError(
                f"Estratégia '{name}' não encontrada. Disponíveis: {list(cls._registry.keys())}"
            )
        return cls._registry[key](**kwargs)

    @classmethod
    def list_available(cls) -> dict[str, str]:
        """Retorna dicionário {key: display_name} das estratégias registradas."""
        return {key: strat_cls.display_name for key, strat_cls in cls._registry.items()}

    @classmethod
    def get_strategy_id(cls, name: str) -> str:
        """Retorna o identificador imutável (strategy_id) de uma estratégia registrada."""
        key = name.strip().lower()
        strat_cls = cls._registry.get(key)
        if strat_cls and hasattr(strat_cls, "strategy_id"):
            return getattr(strat_cls, "strategy_id")
        return "STRAT_UNKNOWN"
