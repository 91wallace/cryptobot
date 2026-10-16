"""Implementação orientada a eventos da Estratégia Apex Dinamics™ no Nautilus Trader.

Recursos:
  - Suporte a ordens a Mercado (Taker) e Ordens Limite no Reteste (Maker - 50% menos taxa)
  - Fimathe Nível 1 Puro (rompimento fora do canal de referência)
  - Condução por Trailing Stop progressivo por subníveis de Amplitude
  - Circuit Breaker Temporal Escalonado (pausa após sequências rápidas de loss)
"""
from decimal import Decimal
import pandas as pd
import numpy as np

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.orders import MarketOrder, LimitOrder
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.trading.strategy import Strategy

from strategies.strategy import EstrategiaFimathe, Direcao


class ApexDinamicsNautilusConfig(StrategyConfig):
    """Configuração da estratégia Apex Dinamics para o Nautilus Trader."""
    instrument_id: str
    bar_type: str
    alavancagem: int = 10
    fracao_banca_por_posicao: float = 0.333
    janela_pivo: int = 3
    amplitude_min_pct: float = 0.20
    folga_stop: float = 0.10
    fator_degrau_trailing: float = 0.50
    cb_janela_horas: float = 6.0
    cb_duracao_pausa_horas: float = 6.0
    cb_max_losses: int = 1
    tipo_ordem: str = "MARKET"  # "MARKET" ou "LIMIT_RETESTE"
    reteste_offset_amp: float = 0.05  # Nível de reteste (5% dentro da borda)
    limite_expiracao_velas: int = 3   # Cancela ordem limite se não retestar em 3 velas


class ApexDinamicsNautilusStrategy(Strategy):
    """Estratégia institucional Apex Dinamics™ implementada como Nautilus Strategy."""

    def __init__(self, config: ApexDinamicsNautilusConfig) -> None:
        super().__init__(config)
        self.cfg = config
        self.instrument: Instrument | None = None
        self.bar_type = BarType.from_str(config.bar_type)

        # Motor matemático oficial da Fimathe
        self.est = EstrategiaFimathe(
            janela_pivo=config.janela_pivo,
            qtd_niveis=4,
            folga_stop=config.folga_stop,
            amplitude_min_pct=config.amplitude_min_pct,
            usar_filtro_50=False,
            modo_stop="fora_caixinha",
            atr_filtro_ativo=False,
            romper_no_canal=True,
            ker_filtro_ativo=False,
        )

        # Histórico de velas em memória
        self.historico_velas: list[dict] = []
        self.max_historico = 150

        # Estado da Posição Ativa
        self.posicao_ativa: str | None = None  # "COMPRA" | "VENDA" | None
        self.preco_entrada: float = 0.0
        self.amplitude_atual: float = 0.0
        self.stop_loss_atual: float = 0.0
        self.alvo_final: float = 0.0
        self.max_preco_atingido: float = 0.0
        self.min_preco_atingido: float = 0.0
        self.degraus_avancados: int = 0
        self.quantidade_posicao: Decimal = Decimal("0")

        # Gestão de Ordem Limite Pendente no Reteste
        self.ordem_pendente_ativa: bool = False
        self.direcao_pendente: str | None = None
        self.velas_esperando_reteste: int = 0
        self.stop_pendente: float = 0.0
        self.alvo_pendente: float = 0.0
        self.amp_pendente: float = 0.0

        # Circuit Breaker temporal
        self.historico_fechamentos: list[tuple[float, str]] = []
        self.suspenso_ate_ts: float = 0.0

    def on_start(self) -> None:
        """Inicializa subscriptions e instrumentos."""
        self.instrument = self.cache.instrument(InstrumentId.from_str(str(self.config.instrument_id)))
        if not self.instrument:
            self.log.error(f"Instrumento não encontrado: {self.config.instrument_id}")
            return

        self.subscribe_bars(self.bar_type)
        self.log.info(f"Apex Dinamics [{self.cfg.tipo_ordem}] iniciada para {self.instrument.id}")

    def on_order_filled(self, event: OrderFilled) -> None:
        """Chamado quando qualquer ordem é preenchida."""
        # Se uma ordem limite pendente foi executada no reteste, ativa a posição
        if self.ordem_pendente_ativa and self.posicao_ativa is None:
            self.posicao_ativa = self.direcao_pendente
            self.preco_entrada = float(event.last_px)
            self.amplitude_atual = self.amp_pendente
            self.stop_loss_atual = self.stop_pendente
            self.alvo_final = self.alvo_pendente
            self.max_preco_atingido = self.preco_entrada
            self.min_preco_atingido = self.preco_entrada
            self.degraus_avancados = 0
            self.ordem_pendente_ativa = False
            self.log.info(
                f"✅ [RETESTE PREENCHIDO - MAKER] {self.posicao_ativa} @ {self.preco_entrada:.4f} (Taxa Reduzida)"
            )

    def on_bar(self, bar: Bar) -> None:
        """Processa nova vela de 5 minutos."""
        preco_open = float(bar.open)
        preco_high = float(bar.high)
        preco_low = float(bar.low)
        preco_close = float(bar.close)
        preco_vol = float(bar.volume)
        ts_segundos = bar.ts_init / 1_000_000_000.0

        self.historico_velas.append({
            "open": preco_open,
            "high": preco_high,
            "low": preco_low,
            "close": preco_close,
            "volume": preco_vol,
        })
        if len(self.historico_velas) > self.max_historico:
            self.historico_velas.pop(0)

        # 1. Se estiver com posição aberta, conduz pelo Trailing Stop
        if self.posicao_ativa is not None:
            self._gerenciar_trailing_stop(preco_high, preco_low, preco_close, ts_segundos)
            return

        # 2. Se tiver ordem limite pendente no reteste, controla expiração
        if self.ordem_pendente_ativa:
            self.velas_esperando_reteste += 1
            if self.velas_esperando_reteste > self.cfg.limite_expiracao_velas:
                self.cancel_all_orders(self.instrument.id)
                self.ordem_pendente_ativa = False
                self.log.info("⏳ [EXPIRAÇÃO] Reteste não alcançado em tempo hábil. Ordem limite cancelada.")
            return

        # 3. Se não estiver posicionado, verifica Circuit Breaker
        if ts_segundos < self.suspenso_ate_ts:
            return

        janela_min = 4 * self.cfg.janela_pivo + 2
        if len(self.historico_velas) < janela_min:
            return

        self._avaliar_novo_sinal(preco_close, ts_segundos)

    def _avaliar_novo_sinal(self, preco_atual: float, ts_segundos: float) -> None:
        """Detecta estrutura de canais Fimathe e dispara entrada."""
        df = pd.DataFrame(self.historico_velas)
        sinal = self.est.avaliar(df)
        if sinal is None:
            return

        compra = sinal.direcao == Direcao.COMPRA
        direcao = "COMPRA" if compra else "VENDA"
        amp = float(sinal.grade["amplitude"])
        alvo = float(sinal.alvos[-1] if len(sinal.alvos) >= 2 else (preco_atual + amp * 3 if compra else preco_atual - amp * 3))
        stop_inicial = float(sinal.stop_loss)

        # Pega a borda rompida do canal de referência
        fundo, topo = sinal.grade["canal_referencia"]

        self._executar_disparo(direcao, preco_atual, amp, stop_inicial, alvo, topo, fundo, ts_segundos)

    def _executar_disparo(
        self,
        direcao: str,
        preco_atual: float,
        amplitude: float,
        stop_inicial: float,
        alvo_final: float,
        topo_canal: float,
        fundo_canal: float,
        ts_segundos: float,
    ) -> None:
        """Calcula lote e envia MarketOrder ou LimitOrder no reteste."""
        if not self.instrument:
            return

        saldo_conta = float(self.portfolio.account(self.instrument.venue).balance_total(self.instrument.quote_currency).as_double())
        capital_slot = saldo_conta * self.cfg.fracao_banca_por_posicao
        nocional = capital_slot * self.cfg.alavancagem
        qtd_flutuante = nocional / preco_atual

        qty = self.instrument.make_qty(Decimal(f"{qtd_flutuante:.{self.instrument.size_precision}f}"))
        if qty < self.instrument.min_quantity:
            qty = self.instrument.min_quantity

        side = OrderSide.BUY if direcao == "COMPRA" else OrderSide.SELL

        if self.cfg.tipo_ordem == "LIMIT_RETESTE":
            # Preço limite no nível da linha rompida com pequeno recuo
            if direcao == "COMPRA":
                px_limite = topo_canal + (amplitude * self.cfg.reteste_offset_amp)
                if px_limite >= preco_atual:
                    px_limite = preco_atual * 0.9995
            else:
                px_limite = fundo_canal - (amplitude * self.cfg.reteste_offset_amp)
                if px_limite <= preco_atual:
                    px_limite = preco_atual * 1.0005

            lim_px_str = f"{px_limite:.{self.instrument.price_precision}f}"
            order: LimitOrder = self.order_factory.limit(
                instrument_id=self.instrument.id,
                order_side=side,
                quantity=qty,
                price=self.instrument.make_price(Decimal(lim_px_str)),
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(order)
            self.ordem_pendente_ativa = True
            self.direcao_pendente = direcao
            self.velas_esperando_reteste = 0
            self.stop_pendente = stop_inicial
            self.alvo_pendente = alvo_final
            self.amp_pendente = amplitude
            self.quantidade_posicao = qty
            self.log.info(
                f"🎯 [ORDEM LIMITE NO RETESTE] {direcao} @ {lim_px_str} (Aguardando pullback p/ taxa Maker)"
            )
        else:
            # Entrada a Mercado (Taker)
            order: MarketOrder = self.order_factory.market(
                instrument_id=self.instrument.id,
                order_side=side,
                quantity=qty,
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(order)
            self.posicao_ativa = direcao
            self.preco_entrada = preco_atual
            self.amplitude_atual = amplitude
            self.stop_loss_atual = stop_inicial
            self.alvo_final = alvo_final
            self.max_preco_atingido = preco_atual
            self.min_preco_atingido = preco_atual
            self.degraus_avancados = 0
            self.quantidade_posicao = qty
            self.log.info(f"🚀 [ENTRADA MERCADO {direcao}] Preço: {preco_atual:.4f} | Stop: {stop_inicial:.4f}")

    def _gerenciar_trailing_stop(
        self, preco_high: float, preco_low: float, preco_close: float, ts_segundos: float
    ) -> None:
        """Condução técnica por subníveis Fimathe."""
        amp = self.amplitude_atual
        compra = self.posicao_ativa == "COMPRA"
        maior_fav = preco_high if compra else preco_low
        avanco = (maior_fav - self.preco_entrada) if compra else (self.preco_entrada - maior_fav)
        degrau_step = amp * self.cfg.fator_degrau_trailing

        passos = int(avanco // degrau_step) if degrau_step > 0 else 0
        if passos >= 2:
            mult_stop = (passos - 1) * self.cfg.fator_degrau_trailing
            if compra:
                n_stop = self.preco_entrada + (mult_stop * amp)
                if n_stop > self.stop_loss_atual:
                    self.stop_loss_atual = n_stop
            else:
                n_stop = self.preco_entrada - (mult_stop * amp)
                if n_stop < self.stop_loss_atual:
                    self.stop_loss_atual = n_stop

        atingiu_stop = (preco_low <= self.stop_loss_atual) if compra else (preco_high >= self.stop_loss_atual)
        atingiu_alvo = (preco_high >= self.alvo_final) if compra else (preco_low <= self.alvo_final)

        if atingiu_alvo or atingiu_stop:
            if atingiu_alvo and not atingiu_stop:
                motivo = "ALVO_FINAL"
                px_out = self.alvo_final
            else:
                lucro_travado = (self.stop_loss_atual > self.preco_entrada) if compra else (self.stop_loss_atual < self.preco_entrada)
                motivo = "TRAILING_STOP_GAIN" if lucro_travado else "STOP_LOSS"
                px_out = self.stop_loss_atual

            self._fechar_posicao(px_out, motivo, ts_segundos)

    def _fechar_posicao(self, preco_saida: float, motivo: str, ts_segundos: float) -> None:
        """Executa fechamento da posição a mercado e atualiza métricas do Circuit Breaker."""
        if not self.instrument or not self.posicao_ativa:
            return

        self.close_all_positions(self.instrument.id)

        resultado_tipo = "GAIN" if ("GAIN" in motivo or "ALVO" in motivo) else "LOSS"
        self.historico_fechamentos.append((ts_segundos, resultado_tipo))
        self._verificar_circuit_breaker(ts_segundos)

        self.log.info(
            f"🏁 [SAÍDA {self.posicao_ativa}] Preço: {preco_saida:.4f} | Motivo: {motivo} | Status CB: {resultado_tipo}"
        )

        self.posicao_ativa = None
        self.degraus_avancados = 0

    def _verificar_circuit_breaker(self, agora_ts: float) -> None:
        limite_janela = agora_ts - (self.cfg.cb_janela_horas * 3600)
        recentes = [r for t, r in self.historico_fechamentos if t >= limite_janela]
        max_l = self.cfg.cb_max_losses

        if len(recentes) >= max_l and all(r == "LOSS" for r in recentes[-max_l:]):
            self.suspenso_ate_ts = agora_ts + (self.cfg.cb_duracao_pausa_horas * 3600)
            self.log.warning(
                f"🛑 [CIRCUIT BREAKER] {max_l} Loss(es) detectado(s). Pausando por {self.cfg.cb_duracao_pausa_horas}h!"
            )
