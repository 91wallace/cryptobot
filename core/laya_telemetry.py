"""Módulo de Telemetria e Retroalimentação Contínua de Trades para o Laya System 1.

Registra a trajetória completa de cada trade acionado por notícias:
- MFE (Maximum Favorable Excursion) e MAE (Maximum Adverse Excursion)
- Motivo de saída (TRAILING_STOP, TAKE_PROFIT, STOP_LOSS, REVERSAL_RISK_PROFIT_PROTECTION)
- Recompensa matemática (PnL% vs Drawdown)
- Alimentação da memória ativa e catálogo de restrições do System 1
- Exportação de datasets formatados para Fine-Tuning do ModernBERT (Laya Core)
"""

import os
import sys
import time
import json
import uuid
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Union, Tuple

log = logging.getLogger("LayaTradeTelemetry")

# Diretório padrão para persistência de dados de feedback
BASE_DATA_DIR = Path(os.getenv("CRYPTOBOT_DATA_DIR", "/home/opc/cryptobot/data"))
DEFAULT_FEEDBACK_FILE = BASE_DATA_DIR / "laya_trade_feedback.jsonl"
DEFAULT_TRAINING_FILE = BASE_DATA_DIR / "laya_training_dataset.jsonl"


@dataclass
class ActiveTradeContext:
    """Contexto em aberto de um trade associado a sinal do Laya."""
    trade_id: str
    symbol: str
    direction: str  # "COMPRA" ou "VENDA"
    entry_price: float
    entry_time: float
    headline: str
    predicted_impact: str  # BULLISH_CATALYST ou BEARISH_SHOCK
    confidence: float
    initial_reversal_risk: float
    stop_loss_initial: float
    take_profit_initial: float
    highest_price: float
    lowest_price: float
    trailing_tightened: bool = False
    exhaustion_alerts_count: int = 0
    extra_metadata: Dict[str, Any] = field(default_factory=dict)
    # Metadados de identificação da estratégia, ativo e local de operação
    strategy_id: str = "STRAT_UNKNOWN"
    strategy_name: str = ""
    asset: str = ""
    exchange: str = "kucoin"
    market_type: str = "futures"
    venue: str = "kucoin_futures"
    # Corretora & Modo de Execução
    broker: str = "KuCoin"
    broker_mode: str = "paper"
    # Regime de Mercado & Volatilidade
    timeframe: str = "5m"
    market_regime: str = "TENDENCIA"
    volatility_atr_pct: float = 0.0
    btc_correlation_regime: str = "NEUTRAL"
    # Microestrutura & Volume
    volume_surge_ratio: float = 1.0
    slippage_pct: float = 0.0
    order_type: str = "MARKET_TAKER"
    # Dinâmica e Timing
    mfe_time: float = 0.0
    mae_time: float = 0.0
    # Notícia & NLP
    news_latency_seconds: float = 0.0
    news_source: str = "CryptoPanic"
    specific_vs_market_wide: str = "SPECIFIC_ASSET"
    # Risco & Alavancagem
    leverage: float = 5.0
    planned_rr_ratio: float = 0.0


@dataclass
class TradeOutcome:
    """Registro consolidado pós-fechamento do trade para treino e telemetria."""
    trade_id: str
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    entry_time: float
    exit_time: float
    duration_seconds: float
    headline: str
    predicted_impact: str
    confidence: float
    initial_reversal_risk: float
    highest_price: float
    lowest_price: float
    mfe_pct: float              # Lucro máximo atingido no percurso (%)
    mae_pct: float              # Pior rebaixamento sofrido no percurso (%)
    realized_pnl_pct: float     # Retorno realizado (%)
    realized_pnl_usdt: float    # Lucro/perda absoluto em USDT
    exit_reason: str            # TRAILING_STOP, TAKE_PROFIT, STOP_LOSS, REVERSAL_RISK_PROFIT_PROTECTION, EMERGENCY_HALT
    trailing_tightened: bool
    exhaustion_alerts_count: int
    reward_score: float         # Recompensa do trade: PnL% - (1.2 * |MAE%|)
    success: bool               # True se retorno positivo e alinhado ao sinal
    laya_feedback_label: str    # Rótulo de classificação de aprendizado
    timestamp_iso: str = ""
    # Metadados de identificação da estratégia, ativo e local de operação
    strategy_id: str = "STRAT_UNKNOWN"
    strategy_name: str = ""
    asset: str = ""
    exchange: str = "kucoin"
    market_type: str = "futures"
    venue: str = "kucoin_futures"
    # Corretora & Modo de Execução
    broker: str = "KuCoin"
    broker_mode: str = "paper"
    # Regime de Mercado & Volatilidade
    timeframe: str = "5m"
    market_regime: str = "TENDENCIA"
    volatility_atr_pct: float = 0.0
    btc_correlation_regime: str = "NEUTRAL"
    # Microestrutura & Volume
    volume_surge_ratio: float = 1.0
    slippage_pct: float = 0.0
    order_type: str = "MARKET_TAKER"
    # Dinâmica do Percurso & Timing
    time_to_mfe_seconds: float = 0.0
    time_to_mae_seconds: float = 0.0
    mfe_mae_ratio: float = 0.0
    session_utc: str = "NEW_YORK"
    hour_of_day: int = 0
    # Notícia & NLP
    news_latency_seconds: float = 0.0
    news_source: str = "CryptoPanic"
    specific_vs_market_wide: str = "SPECIFIC_ASSET"
    # Risco & Alavancagem
    leverage: float = 5.0
    planned_rr_ratio: float = 0.0
    realized_rr_ratio: float = 0.0

    def __post_init__(self):
        if not self.timestamp_iso:
            self.timestamp_iso = datetime.now(timezone.utc).isoformat()
        if not self.asset and self.symbol:
            self.asset = LayaTradeFeedbackCollector.extract_base_asset(self.symbol)
        if not self.exchange:
            self.exchange = os.getenv("EXCHANGE_ID", "kucoin").strip().lower()
        if not self.market_type:
            self.market_type = "futures" if ":" in self.symbol or os.getenv("MARKET_TYPE", "").lower() == "futures" else os.getenv("MARKET_TYPE", "futures").lower()
        if not self.venue:
            self.venue = f"{self.exchange}_{self.market_type}"
        if not self.broker:
            self.broker = "KuCoin" if "kucoin" in self.exchange.lower() else self.exchange.capitalize()
        if not self.broker_mode:
            self.broker_mode = os.getenv("MODO", "paper").lower()
        if not self.session_utc or self.session_utc == "NEW_YORK":
            entry_t = self.entry_time or time.time()
            try:
                entry_dt = datetime.fromtimestamp(entry_t, tz=timezone.utc)
                self.hour_of_day = entry_dt.hour
                weekday = entry_dt.weekday()
                if weekday >= 5:
                    self.session_utc = "WEEKEND"
                elif 0 <= self.hour_of_day < 8:
                    self.session_utc = "ASIA"
                elif 8 <= self.hour_of_day < 14:
                    self.session_utc = "LONDON"
                else:
                    self.session_utc = "NEW_YORK"
            except Exception:
                self.session_utc = "NEW_YORK"
                self.hour_of_day = 0


_system1_engine_instance = None


def get_system1_engine():
    """Carrega System1ContinuousEngine dinamicamente sem colisão com módulos locais."""
    global _system1_engine_instance
    if _system1_engine_instance is not None:
        return _system1_engine_instance
    try:
        import importlib.util
        s1_path = Path("/home/opc/system1-reflexive")
        if not s1_path.exists():
            s1_path = Path("/home/opc/.system1-reflexive")
        if not s1_path.exists():
            return None
        engine_file = s1_path / "engine.py"
        if not engine_file.exists():
            return None
        if str(s1_path) not in sys.path:
            sys.path.insert(0, str(s1_path))
        spec = importlib.util.spec_from_file_location("system1_reflexive_engine", str(engine_file))
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _system1_engine_instance = mod.System1ContinuousEngine()
            return _system1_engine_instance
    except Exception as e:
        log.warning(f"Erro ao carregar System1ContinuousEngine: {e}")
    return None


class LayaTradeFeedbackCollector:
    """Coletor e integrador de aprendizado contínuo do Laya System 1."""

    def __init__(
        self,
        feedback_file: Union[str, Path] = DEFAULT_FEEDBACK_FILE,
        training_file: Union[str, Path] = DEFAULT_TRAINING_FILE,
        enable_system1_memory: bool = True,
        system1_endpoint: str = "http://127.0.0.1:8000",
    ) -> None:
        self.feedback_file = Path(feedback_file)
        self.training_file = Path(training_file)
        self.enable_system1_memory = enable_system1_memory
        self.system1_endpoint = system1_endpoint.rstrip("/")
        self.active_trades: Dict[str, ActiveTradeContext] = {}

        # Garante diretório pai
        self.feedback_file.parent.mkdir(parents=True, exist_ok=True)
        self.training_file.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def extract_base_asset(symbol: str) -> str:
        """Extrai o ativo base a partir do par (ex.: 'SOL/USDT:USDT' -> 'SOL', 'BTC/USDT' -> 'BTC', 'PEPEUSDT' -> 'PEPE')."""
        if not symbol:
            return ""
        s = str(symbol).split(":")[0].strip()
        if "/" in s:
            return s.split("/")[0].strip().upper()
        if "-" in s:
            s = s.split("-")[0].strip()
        for quote in ("USDT", "USDC", "USD", "BUSD", "DAI"):
            if s.upper().endswith(quote) and len(s) > len(quote):
                return s[:-len(quote)].upper()
        return s.strip().upper()

    @staticmethod
    def resolve_venue(
        symbol: str = "",
        exchange: Optional[str] = None,
        market_type: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """Determina exchange, market_type e venue unificado onde o ativo estava sendo operado."""
        ex = (exchange or os.getenv("EXCHANGE_ID", "kucoin")).strip().lower()
        mt = market_type
        if not mt:
            if ":" in symbol or os.getenv("MARKET_TYPE", "").lower() == "futures":
                mt = "futures"
            else:
                mt = os.getenv("MARKET_TYPE", "futures").strip().lower()
        else:
            mt = mt.strip().lower()
        venue = f"{ex}_{mt}"
        return ex, mt, venue

    def start_trade(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        headline: str = "",
        predicted_impact: str = "BULLISH_CATALYST",
        confidence: float = 0.85,
        reversal_risk_score: float = 0.0,
        stop_loss_initial: float = 0.0,
        take_profit_initial: float = 0.0,
        trade_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        strategy_name: Optional[str] = None,
        asset: Optional[str] = None,
        exchange: Optional[str] = None,
        market_type: Optional[str] = None,
        venue: Optional[str] = None,
        broker: Optional[str] = None,
        broker_mode: Optional[str] = None,
        timeframe: Optional[str] = None,
        market_regime: Optional[str] = None,
        volatility_atr_pct: Optional[float] = None,
        btc_correlation_regime: Optional[str] = None,
        volume_surge_ratio: Optional[float] = None,
        slippage_pct: Optional[float] = None,
        order_type: Optional[str] = None,
        news_latency_seconds: Optional[float] = None,
        news_source: Optional[str] = None,
        specific_vs_market_wide: Optional[str] = None,
        leverage: Optional[float] = None,
        planned_rr_ratio: Optional[float] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> ActiveTradeContext:
        """Registra a abertura de uma nova posição gerada por sinal com telemetria completa."""
        t_id = trade_id or f"trade_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        now = time.time()

        resolved_asset = asset or self.extract_base_asset(symbol)
        resolved_ex, resolved_mt, resolved_venue = self.resolve_venue(symbol, exchange, market_type)
        if venue:
            resolved_venue = venue

        strat_id = strategy_id or "STRAT_UNKNOWN"
        strat_name = strategy_name or ""
        resolved_broker = broker or ("KuCoin" if "kucoin" in resolved_ex.lower() else resolved_ex.capitalize())
        resolved_mode = broker_mode or os.getenv("MODO", "paper").lower()
        resolved_tf = timeframe or os.getenv("TIMEFRAME", "5m")
        resolved_regime = market_regime or "TENDENCIA"
        resolved_atr = volatility_atr_pct or 0.0
        resolved_btc = btc_correlation_regime or "NEUTRAL"
        resolved_vol = volume_surge_ratio if volume_surge_ratio is not None else 1.0
        resolved_slip = slippage_pct or 0.0
        resolved_order = order_type or "MARKET_TAKER"
        resolved_latency = news_latency_seconds or 0.0
        resolved_source = news_source or "CryptoPanic"
        resolved_scope = specific_vs_market_wide or "SPECIFIC_ASSET"
        resolved_lev = leverage if leverage is not None else float(os.getenv("ALAVANCAGEM_MAX", "5.0"))

        # Cálculo de R:R planejado inicial
        prr = planned_rr_ratio or 0.0
        if prr <= 0 and stop_loss_initial > 0 and take_profit_initial > 0 and entry_price > 0:
            risk_dist = abs(entry_price - stop_loss_initial)
            reward_dist = abs(take_profit_initial - entry_price)
            if risk_dist > 0:
                prr = round(reward_dist / risk_dist, 2)

        ctx = ActiveTradeContext(
            trade_id=t_id,
            symbol=symbol,
            direction=direction.upper(),
            entry_price=entry_price,
            entry_time=now,
            headline=headline or "Sem manchete (Manual/Sinal)",
            predicted_impact=predicted_impact,
            confidence=confidence,
            initial_reversal_risk=reversal_risk_score,
            stop_loss_initial=stop_loss_initial,
            take_profit_initial=take_profit_initial,
            highest_price=entry_price,
            lowest_price=entry_price,
            trailing_tightened=False,
            exhaustion_alerts_count=0,
            extra_metadata=extra_metadata or {},
            strategy_id=strat_id,
            strategy_name=strat_name,
            asset=resolved_asset,
            exchange=resolved_ex,
            market_type=resolved_mt,
            venue=resolved_venue,
            broker=resolved_broker,
            broker_mode=resolved_mode,
            timeframe=resolved_tf,
            market_regime=resolved_regime,
            volatility_atr_pct=resolved_atr,
            btc_correlation_regime=resolved_btc,
            volume_surge_ratio=resolved_vol,
            slippage_pct=resolved_slip,
            order_type=resolved_order,
            mfe_time=now,
            mae_time=now,
            news_latency_seconds=resolved_latency,
            news_source=resolved_source,
            specific_vs_market_wide=resolved_scope,
            leverage=resolved_lev,
            planned_rr_ratio=prr,
        )

        self.active_trades[t_id] = ctx
        log.info(
            f"📥 [Laya Telemetria] Iniciado rastreamento {t_id} [{strat_id}] na corretora {resolved_broker} ({resolved_mode.upper()}) | "
            f"{direction} {symbol} em {resolved_venue} @ {entry_price:.2f} | R:R={prr:.1f} | Lat={resolved_latency:.1f}s"
        )
        return ctx

    def update_trade_price(
        self,
        trade_id: str,
        high: float,
        low: float,
        close: float,
    ) -> None:
        """Atualiza a excursão máxima favorável (MFE), adversa (MAE) e timestamps de timing."""
        ctx = self.active_trades.get(trade_id)
        if not ctx:
            return

        now = time.time()
        if ctx.direction == "COMPRA":
            if high > ctx.highest_price:
                ctx.highest_price = high
                ctx.mfe_time = now
            if low < ctx.lowest_price or ctx.lowest_price <= 0:
                ctx.lowest_price = low
                ctx.mae_time = now
        else:  # VENDA
            if low < ctx.lowest_price or ctx.lowest_price <= 0:
                ctx.lowest_price = low
                ctx.mfe_time = now
            if high > ctx.highest_price:
                ctx.highest_price = high
                ctx.mae_time = now

    def record_exhaustion_alert(
        self,
        trade_id: str,
        stage: int,
        reversal_risk_score: float,
    ) -> None:
        """Registra que o gatilho de exaustão neural foi ativado durante o trade."""
        ctx = self.active_trades.get(trade_id)
        if not ctx:
            return

        ctx.exhaustion_alerts_count += 1
        if stage == 1:
            ctx.trailing_tightened = True
        log.info(
            f"⚠️ [Laya Telemetria] Trade {trade_id} disparou Alerta de Exaustão Nível {stage} "
            f"(Risco={reversal_risk_score:.2f})"
        )

    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
        realized_pnl_usdt: float = 0.0,
        exit_time: Optional[float] = None,
    ) -> Optional[TradeOutcome]:
        """Consolida o fechamento do trade, calcula métricas e alimenta o System 1."""
        ctx = self.active_trades.pop(trade_id, None)
        if not ctx:
            log.warning(f"Trade {trade_id} não encontrado no registro de ativos.")
            return None

        now = exit_time or time.time()
        duration = max(0.0, now - ctx.entry_time)

        # Cálculo de retorno e excursões percentuais
        if ctx.direction == "COMPRA":
            pnl_pct = (exit_price - ctx.entry_price) / ctx.entry_price if ctx.entry_price > 0 else 0.0
            mfe_pct = (ctx.highest_price - ctx.entry_price) / ctx.entry_price if ctx.entry_price > 0 else 0.0
            mae_pct = (ctx.lowest_price - ctx.entry_price) / ctx.entry_price if ctx.entry_price > 0 else 0.0
        else:
            pnl_pct = (ctx.entry_price - exit_price) / ctx.entry_price if ctx.entry_price > 0 else 0.0
            mfe_pct = (ctx.entry_price - ctx.lowest_price) / ctx.entry_price if ctx.entry_price > 0 else 0.0
            mae_pct = (ctx.entry_price - ctx.highest_price) / ctx.entry_price if ctx.entry_price > 0 else 0.0

        # Penalização por rebaixamento adverso na função de recompensa
        adverse_penalty = abs(min(0.0, mae_pct))
        reward_score = round(pnl_pct - (1.2 * adverse_penalty), 5)
        success = (pnl_pct > 0.002)

        # Rótulo de classificação para aprendizado contínuo
        if exit_reason == "REVERSAL_RISK_PROFIT_PROTECTION":
            feedback_label = "EXHAUSTION_EXIT_PROFIT_LOCKED"
        elif exit_reason == "TRAILING_STOP" and pnl_pct > 0.02:
            feedback_label = "SUPER_RUNNER_TRAILING_SUCCESS"
        elif exit_reason == "TAKE_PROFIT":
            feedback_label = "CLEAN_CATALYST_TARGET_HIT"
        elif exit_reason == "STOP_LOSS":
            feedback_label = "FALSE_CATALYST_STOPPED_OUT"
        elif exit_reason == "EMERGENCY_HALT":
            feedback_label = "CIRCUIT_BREAKER_PROTECTION"
        else:
            feedback_label = "STANDARD_TRADE_COMPLETED"

        # Métricas de timing (Time to MFE / MAE)
        mfe_t = ctx.mfe_time if ctx.mfe_time > 0 else ctx.entry_time
        mae_t = ctx.mae_time if ctx.mae_time > 0 else ctx.entry_time
        time_to_mfe = max(0.0, round(mfe_t - ctx.entry_time, 1))
        time_to_mae = max(0.0, round(mae_t - ctx.entry_time, 1))

        # Razão MFE / |MAE|
        abs_mae = abs(min(0.0, mae_pct))
        mfe_mae_ratio = round(mfe_pct / abs_mae, 2) if abs_mae > 1e-5 else round(mfe_pct / 0.001, 2)

        # Relação Risco / Retorno Planejada e Realizada
        planned_risk = 0.0
        if ctx.stop_loss_initial > 0 and ctx.entry_price > 0:
            planned_risk = abs(ctx.entry_price - ctx.stop_loss_initial) / ctx.entry_price
        planned_reward = 0.0
        if ctx.take_profit_initial > 0 and ctx.entry_price > 0:
            planned_reward = abs(ctx.take_profit_initial - ctx.entry_price) / ctx.entry_price

        planned_rr = ctx.planned_rr_ratio
        if planned_rr <= 0 and planned_risk > 0 and planned_reward > 0:
            planned_rr = round(planned_reward / planned_risk, 2)

        realized_rr = round(pnl_pct / planned_risk, 2) if planned_risk > 0 else round(pnl_pct / 0.01, 2)

        # Sessão de mercado UTC
        entry_dt = datetime.fromtimestamp(ctx.entry_time, tz=timezone.utc)
        hour_of_day = entry_dt.hour
        weekday = entry_dt.weekday()
        if weekday >= 5:
            session_utc = "WEEKEND"
        elif 0 <= hour_of_day < 8:
            session_utc = "ASIA"
        elif 8 <= hour_of_day < 14:
            session_utc = "LONDON"
        else:
            session_utc = "NEW_YORK"

        outcome = TradeOutcome(
            trade_id=ctx.trade_id,
            symbol=ctx.symbol,
            direction=ctx.direction,
            entry_price=ctx.entry_price,
            exit_price=exit_price,
            entry_time=ctx.entry_time,
            exit_time=now,
            duration_seconds=round(duration, 1),
            headline=ctx.headline,
            predicted_impact=ctx.predicted_impact,
            confidence=ctx.confidence,
            initial_reversal_risk=ctx.initial_reversal_risk,
            highest_price=ctx.highest_price,
            lowest_price=ctx.lowest_price,
            mfe_pct=round(mfe_pct, 5),
            mae_pct=round(mae_pct, 5),
            realized_pnl_pct=round(pnl_pct, 5),
            realized_pnl_usdt=round(realized_pnl_usdt, 2),
            exit_reason=exit_reason,
            trailing_tightened=ctx.trailing_tightened,
            exhaustion_alerts_count=ctx.exhaustion_alerts_count,
            reward_score=reward_score,
            success=success,
            laya_feedback_label=feedback_label,
            strategy_id=ctx.strategy_id,
            strategy_name=ctx.strategy_name,
            asset=ctx.asset,
            exchange=ctx.exchange,
            market_type=ctx.market_type,
            venue=ctx.venue,
            broker=ctx.broker,
            broker_mode=ctx.broker_mode,
            timeframe=ctx.timeframe,
            market_regime=ctx.market_regime,
            volatility_atr_pct=ctx.volatility_atr_pct,
            btc_correlation_regime=ctx.btc_correlation_regime,
            volume_surge_ratio=ctx.volume_surge_ratio,
            slippage_pct=ctx.slippage_pct,
            order_type=ctx.order_type,
            time_to_mfe_seconds=time_to_mfe,
            time_to_mae_seconds=time_to_mae,
            mfe_mae_ratio=mfe_mae_ratio,
            session_utc=session_utc,
            hour_of_day=hour_of_day,
            news_latency_seconds=ctx.news_latency_seconds,
            news_source=ctx.news_source,
            specific_vs_market_wide=ctx.specific_vs_market_wide,
            leverage=ctx.leverage,
            planned_rr_ratio=planned_rr,
            realized_rr_ratio=realized_rr,
        )

        # 1. Persiste telemetria local em JSONL
        self._append_to_file(self.feedback_file, asdict(outcome))

        # 2. Gera exemplo de treino no formato ModernBERT
        self._append_training_example(outcome)

        # 3. Alimenta a memória e guardrails do System 1
        if self.enable_system1_memory:
            self._feed_system1(outcome)

        log.info(
            f"🏁 [Laya Telemetria] Concluído {trade_id} [{ctx.strategy_id}] na corretora {ctx.broker} ({ctx.venue}): "
            f"PnL={pnl_pct*100:+.2f}% (${realized_pnl_usdt:+.2f}) | "
            f"Saída: {exit_reason} | MFE: +{mfe_pct*100:.2f}% ({time_to_mfe}s) | R:R Real={realized_rr:.2f}"
        )
        return outcome

    def record_trade_outcome(self, outcome: TradeOutcome) -> None:
        """Registra diretamente um TradeOutcome completado (ex.: orquestrador main.py)."""
        if not outcome.asset and outcome.symbol:
            outcome.asset = self.extract_base_asset(outcome.symbol)
        if not outcome.exchange:
            outcome.exchange = os.getenv("EXCHANGE_ID", "kucoin").strip().lower()
        if not outcome.market_type:
            outcome.market_type = "futures" if ":" in outcome.symbol or os.getenv("MARKET_TYPE", "").lower() == "futures" else os.getenv("MARKET_TYPE", "futures").strip().lower()
        if not outcome.venue:
            outcome.venue = f"{outcome.exchange}_{outcome.market_type}"
        if not outcome.broker:
            outcome.broker = "KuCoin" if "kucoin" in outcome.exchange.lower() else outcome.exchange.capitalize()
        if not outcome.broker_mode:
            outcome.broker_mode = os.getenv("MODO", "paper").lower()
        if not outcome.timeframe:
            outcome.timeframe = os.getenv("TIMEFRAME", "5m")
        if not outcome.session_utc or outcome.session_utc == "NEW_YORK":
            entry_t = outcome.entry_time or time.time()
            try:
                entry_dt = datetime.fromtimestamp(entry_t, tz=timezone.utc)
                outcome.hour_of_day = entry_dt.hour
                weekday = entry_dt.weekday()
                if weekday >= 5:
                    outcome.session_utc = "WEEKEND"
                elif 0 <= outcome.hour_of_day < 8:
                    outcome.session_utc = "ASIA"
                elif 8 <= outcome.hour_of_day < 14:
                    outcome.session_utc = "LONDON"
                else:
                    outcome.session_utc = "NEW_YORK"
            except Exception:
                pass

        # 1. Persiste telemetria local em JSONL
        self._append_to_file(self.feedback_file, asdict(outcome))

        # 2. Gera exemplo de treino no formato ModernBERT
        self._append_training_example(outcome)

        # 3. Alimenta a memória e guardrails do System 1
        if self.enable_system1_memory:
            self._feed_system1(outcome)

        log.info(
            f"🏁 [Laya Telemetria] Trade registrado {outcome.trade_id} [{outcome.strategy_id}] na corretora {outcome.broker} ({outcome.venue}): "
            f"PnL={outcome.realized_pnl_pct*100:+.2f}% (${outcome.realized_pnl_usdt:+.2f}) | Saída: {outcome.exit_reason}"
        )

    def _append_to_file(self, file_path: Path, record: Dict[str, Any]) -> None:
        """Garante escrita atômica/append thread-safe."""
        try:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            log.error(f"Erro ao salvar registro de telemetria em {file_path}: {e}")

    def _append_training_example(self, outcome: TradeOutcome) -> None:
        """Gera entrada normalizada de treino para Laya ModernBERT / SFT."""
        # Se o trade foi um falso rompimento que deu loss, a classe real era NEUTRAL_NOISE
        target_impact = outcome.predicted_impact
        target_rev_risk = outcome.initial_reversal_risk

        if not outcome.success and outcome.realized_pnl_pct <= -0.015:
            # Calibração negativa: notícia não gerou impulso real
            target_impact = "NEUTRAL_NOISE"
            target_confidence = 0.50
        else:
            target_confidence = max(0.85, outcome.confidence)

        # Se o gatilho de exaustão salvou lucro no topo, reforça score alto de reversão
        if outcome.exit_reason == "REVERSAL_RISK_PROFIT_PROTECTION":
            target_rev_risk = 0.90

        training_item = {
            "state": {
                "body": outcome.headline,
                "strategy_id": outcome.strategy_id,
                "strategy_name": outcome.strategy_name,
                "asset": outcome.asset,
                "symbol": outcome.symbol,
                "broker": outcome.broker,
                "broker_mode": outcome.broker_mode,
                "exchange": outcome.exchange,
                "market_type": outcome.market_type,
                "venue": outcome.venue,
                "timeframe": outcome.timeframe,
                "market_regime": outcome.market_regime,
                "volatility_atr_pct": outcome.volatility_atr_pct,
                "btc_correlation_regime": outcome.btc_correlation_regime,
                "volume_surge_ratio": outcome.volume_surge_ratio,
                "session_utc": outcome.session_utc,
                "news_source": outcome.news_source,
                "specific_vs_market_wide": outcome.specific_vs_market_wide,
            },
            "answers": {
                "impact_direction": {
                    "choice": target_impact,
                    "confidence": target_confidence,
                },
                "reversal_risk": {
                    "score": round(target_rev_risk, 2),
                },
            },
            "meta": {
                "trade_id": outcome.trade_id,
                "strategy_id": outcome.strategy_id,
                "strategy_name": outcome.strategy_name,
                "asset": outcome.asset,
                "symbol": outcome.symbol,
                "broker": outcome.broker,
                "broker_mode": outcome.broker_mode,
                "exchange": outcome.exchange,
                "market_type": outcome.market_type,
                "venue": outcome.venue,
                "timeframe": outcome.timeframe,
                "market_regime": outcome.market_regime,
                "volatility_atr_pct": outcome.volatility_atr_pct,
                "btc_correlation_regime": outcome.btc_correlation_regime,
                "volume_surge_ratio": outcome.volume_surge_ratio,
                "order_type": outcome.order_type,
                "slippage_pct": outcome.slippage_pct,
                "leverage": outcome.leverage,
                "planned_rr_ratio": outcome.planned_rr_ratio,
                "realized_rr_ratio": outcome.realized_rr_ratio,
                "news_latency_seconds": outcome.news_latency_seconds,
                "news_source": outcome.news_source,
                "specific_vs_market_wide": outcome.specific_vs_market_wide,
                "time_to_mfe_seconds": outcome.time_to_mfe_seconds,
                "time_to_mae_seconds": outcome.time_to_mae_seconds,
                "mfe_mae_ratio": outcome.mfe_mae_ratio,
                "session_utc": outcome.session_utc,
                "hour_of_day": outcome.hour_of_day,
                "realized_pnl_pct": outcome.realized_pnl_pct,
                "mfe_pct": outcome.mfe_pct,
                "mae_pct": outcome.mae_pct,
                "exit_reason": outcome.exit_reason,
                "reward_score": outcome.reward_score,
                "success": outcome.success,
            }
        }
        self._append_to_file(self.training_file, training_item)

    def _feed_system1(self, outcome: TradeOutcome) -> None:
        """Comunica o feedback do trade para o motor reflexivo do System 1."""
        try:
            s1 = get_system1_engine()
            if not s1:
                return

            if outcome.success:
                # 1. Registra Playbook vencedor para notícias com excelente resultado (>= 0.5% no preço do ativo)
                if outcome.realized_pnl_pct >= 0.005:
                    s1.record_playbook(
                        title=f"Playbook Cripto Vencedor [{outcome.strategy_id}]: {outcome.predicted_impact} em {outcome.asset} ({outcome.venue})",
                        description=f"Estratégia {outcome.strategy_id} ({outcome.strategy_name}) operando {outcome.asset} em {outcome.venue} ({outcome.symbol}) provocou forte expansão ({outcome.realized_pnl_pct*100:+.2f}%): '{outcome.headline}'",
                        solution_steps=[
                            f"Identificar catalisador com confiança >= {outcome.confidence:.2f}",
                            f"Executar sinal da estratégia {outcome.strategy_id} no ativo {outcome.asset} via {outcome.venue}",
                            f"Ativar Trailing Stop e monitorar alertas de exaustão Laya no pico",
                        ],
                        category="FEATURE_IMPLEMENTATION",
                        keywords=[outcome.strategy_id, outcome.strategy_name, outcome.asset, outcome.symbol.split("/")[0], "BREAKOUT", outcome.predicted_impact, outcome.venue]
                    )

                # 2. Reforça palavras-chave na memória reflexiva
                s1.record_feedback(
                    text=outcome.headline,
                    actual_label="FEATURE_IMPLEMENTATION",
                    success=True,
                    feedback_note=f"Trade {outcome.symbol} [{outcome.strategy_id}] em {outcome.venue} ({outcome.asset}) com lucro de {outcome.realized_pnl_pct*100:+.2f}% (Saída: {outcome.exit_reason})"
                )

                # 3. Consolida a decisão neural aprendida pelo Laya
                s1.learn_laya_decision(
                    prompt=outcome.headline,
                    category="FEATURE_IMPLEMENTATION",
                    confidence=max(0.85, outcome.confidence)
                )
            else:
                # 4. Registra falha e cria regra de veto no Graveyard do System 1
                if outcome.realized_pnl_pct < -0.01:
                    s1.record_failure(
                        approach=f"[{outcome.strategy_id}] Entrada em '{outcome.headline[:50]}' ({outcome.symbol} @ {outcome.venue})",
                        failure_reason=f"Stop Loss atingido ({outcome.realized_pnl_pct*100:.2f}%) por falta de continuidade na estratégia {outcome.strategy_id}",
                        root_cause=f"Falso catalisador em região de rejeição técnica (MAE: {outcome.mae_pct*100:.2f}%) em {outcome.venue}",
                        veto_rule=f"[{outcome.strategy_id}] Exigir confirmação de rompimento em notícias do tipo '{outcome.headline[:35]}' antes de abrir mercado em {outcome.asset} ({outcome.venue})",
                        category="CODE_BUGFIX"
                    )
                    s1.add_constraint(
                        f"VETO CRIPTO [{outcome.strategy_id}]: Exigir confirmação técnica antes de entrar em '{outcome.headline[:40]}' para {outcome.asset}"
                    )

            log.info(f"🧠 [Laya Telemetria] Padrões de trade [{outcome.strategy_id}] consolidados na memória do System 1.")
        except Exception as e:
            # Fallback tolerante a falhas (não interrompe o fluxo de negociação)
            log.debug(f"[Laya Telemetria] Integração direta System 1 indisponível ou em modo desacoplado: {e}")

    def is_action_vetoed(self, headline: str, min_bm25: float = 15.0) -> Tuple[bool, Optional[str]]:
        """Verifica se o Laya System 1 possui veto ativo no Graveyard para esta notícia."""
        if not self.enable_system1_memory or not headline:
            return False, None
        try:
            s1 = get_system1_engine()
            if not s1:
                return False, None
            res = s1.validate_action(headline)
            if res.get("vetoed"):
                for mv in res.get("matching_vetos", []):
                    if mv.get("bm25_score", 0.0) >= min_bm25:
                        reason = mv.get("veto_rule") or mv.get("failure_reason") or "Falso rompimento prévio"
                        log.info(f"🛡️ [Laya Veto Ativo] Entrada bloqueada: '{headline[:40]}' -> Regra: {reason}")
                        return True, reason
            return False, None
        except Exception as e:
            log.debug(f"Erro ao consultar veto no System 1: {e}")
            return False, None

    def export_dataset_summary(self) -> Dict[str, Any]:
        """Retorna resumo estatístico dos trades coletados para treino."""
        if not self.feedback_file.exists():
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "total_pnl_pct": 0.0,
                "trades_por_estrategia": {},
                "trades_por_ativo": {},
                "trades_por_venue": {},
            }

        trades = []
        with open(self.feedback_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    trades.append(json.loads(line))

        if not trades:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "total_pnl_pct": 0.0,
                "trades_por_estrategia": {},
                "trades_por_ativo": {},
                "trades_por_venue": {},
            }

        wins = [t for t in trades if t.get("realized_pnl_pct", 0) > 0]
        total_pnl = sum(t.get("realized_pnl_pct", 0) for t in trades)

        trades_por_estrategia: Dict[str, int] = {}
        trades_por_ativo: Dict[str, int] = {}
        trades_por_corretora: Dict[str, int] = {}
        trades_por_venue: Dict[str, int] = {}
        tempos_mfe: List[float] = []
        ratios_mfe_mae: List[float] = []

        for t in trades:
            sid = t.get("strategy_id") or "STRAT_UNKNOWN"
            trades_por_estrategia[sid] = trades_por_estrategia.get(sid, 0) + 1

            ast = t.get("asset") or self.extract_base_asset(t.get("symbol", ""))
            if ast:
                trades_por_ativo[ast] = trades_por_ativo.get(ast, 0) + 1

            brok = t.get("broker") or "KuCoin"
            trades_por_corretora[brok] = trades_por_corretora.get(brok, 0) + 1

            ven = t.get("venue") or f"{t.get('exchange', 'kucoin')}_{t.get('market_type', 'futures')}"
            if ven:
                trades_por_venue[ven] = trades_por_venue.get(ven, 0) + 1

            if "time_to_mfe_seconds" in t:
                tempos_mfe.append(float(t["time_to_mfe_seconds"]))
            if "mfe_mae_ratio" in t:
                ratios_mfe_mae.append(float(t["mfe_mae_ratio"]))

        media_mfe = round(sum(tempos_mfe) / len(tempos_mfe), 1) if tempos_mfe else 0.0
        media_ratio = round(sum(ratios_mfe_mae) / len(ratios_mfe_mae), 2) if ratios_mfe_mae else 0.0

        return {
            "total_trades": len(trades),
            "vitorias": len(wins),
            "derrotas": len(trades) - len(wins),
            "win_rate": round(len(wins) / len(trades) * 100.0, 1),
            "total_pnl_pct": round(total_pnl * 100.0, 2),
            "trades_por_estrategia": trades_por_estrategia,
            "trades_por_ativo": trades_por_ativo,
            "trades_por_corretora": trades_por_corretora,
            "trades_por_venue": trades_por_venue,
            "media_time_to_mfe_seconds": media_mfe,
            "media_mfe_mae_ratio": media_ratio,
            "feedback_path": str(self.feedback_file),
            "training_path": str(self.training_file),
        }


# Instância singleton global para fácil reuso
default_telemetry_collector = LayaTradeFeedbackCollector()
