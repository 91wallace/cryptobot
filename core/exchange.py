"""Módulo de conexão com a KuCoin (via ccxt).

Responsável por:
  * criar o cliente (spot ou futures);
  * baixar histórico de candles como DataFrame;
  * consultar saldo / preço;
  * enviar ordens de forma segura (precisão, limites mínimos, clientOid
    para rastreabilidade).

Modos de operação (variável MODO):
  * paper: preços reais da KuCoin + execução no simulador nativo (paper.py).
           Substitui o sandbox, que a KuCoin não oferece via ccxt.
  * live:  ordens reais na KuCoin.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import ccxt
import pandas as pd

from paper import SimuladorPaper

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

# Erros transitórios nos quais é seguro repetir chamadas de LEITURA.
ERROS_TRANSITORIOS = (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout)


class Exchange:
    def __init__(
        self,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        market_type: str = "spot",
        modo: str = "paper",
        moeda_quote: str = "USDT",
        paper_capital: float = 1000.0,
        paper_taxa_pct: float = 0.1,
        paper_slippage_pct: float = 0.05,
        margin_mode: str = "auto",
    ) -> None:
        if market_type not in ("spot", "futures"):
            raise ValueError(f"MARKET_TYPE inválido: {market_type!r} (use 'spot' ou 'futures')")
        if modo not in ("paper", "live"):
            raise ValueError(f"MODO inválido: {modo!r} (use 'paper' ou 'live')")
        if modo == "live" and not (api_key and secret and password):
            raise RuntimeError("MODO=live exige KUCOIN_API_KEY, KUCOIN_API_SECRET e KUCOIN_API_PASSPHRASE.")

        classe = ccxt.kucoinfutures if market_type == "futures" else ccxt.kucoin
        self.client = classe(
            {
                "apiKey": api_key,
                "secret": secret,
                "password": password,
                "enableRateLimit": True,
            }
        )
        self.market_type = market_type
        self.modo = modo
        self.margin_mode = margin_mode.strip().lower() if margin_mode else "auto"
        self._margin_modes_por_simbolo: dict[str, str] = {}
        self._mercados_carregados = False

        self.paper: SimuladorPaper | None = None
        if modo == "paper":
            self.paper = SimuladorPaper(
                caminho_estado=BASE_DIR / "data" / f"paper_{market_type}.json",
                capital_inicial=paper_capital,
                moeda_quote=moeda_quote,
                taxa_pct=paper_taxa_pct,
                slippage_pct=paper_slippage_pct,
                permite_short=(market_type == "futures"),
            )

        log.info("Exchange inicializada | mercado=%s modo=%s margin_mode=%s", market_type, modo, self.margin_mode)

    @classmethod
    def from_env(cls) -> "Exchange":
        return cls(
            api_key=os.getenv("KUCOIN_API_KEY", ""),
            secret=os.getenv("KUCOIN_API_SECRET", ""),
            password=os.getenv("KUCOIN_API_PASSPHRASE", ""),
            market_type=os.getenv("MARKET_TYPE", "spot").strip().lower(),
            modo=os.getenv("MODO", "paper").strip().lower(),
            moeda_quote=os.getenv("QUOTE_CURRENCY", "USDT"),
            paper_capital=float(os.getenv("PAPER_CAPITAL", "1000")),
            paper_taxa_pct=float(os.getenv("PAPER_TAXA_PCT", "0.1")),
            paper_slippage_pct=float(os.getenv("PAPER_SLIPPAGE_PCT", "0.05")),
            margin_mode=os.getenv("MARGIN_MODE", "auto"),
        )

    @property
    def is_futures(self) -> bool:
        return self.market_type == "futures"

    @property
    def modo_paper(self) -> bool:
        return self.paper is not None

    # ------------------------------------------------------------------ #
    # Utilidades internas
    # ------------------------------------------------------------------ #
    def _retry(self, fn: Callable[..., Any], *args: Any, tentativas: int = 3,
               espera: float = 2.0, **kwargs: Any) -> Any:
        """Repete chamadas de leitura em caso de falha de rede (backoff exponencial)."""
        for tentativa in range(1, tentativas + 1):
            try:
                return fn(*args, **kwargs)
            except ERROS_TRANSITORIOS as exc:
                if tentativa == tentativas:
                    raise
                atraso = espera * (2 ** (tentativa - 1))
                log.warning("Falha transitória em %s (%s). Nova tentativa em %.1fs...",
                            getattr(fn, "__name__", fn), exc, atraso)
                time.sleep(atraso)

    def carregar_mercados(self) -> None:
        if not self._mercados_carregados:
            self._retry(self.client.load_markets)
            self._mercados_carregados = True

    def mercado(self, symbol: str) -> dict:
        self.carregar_mercados()
        return self.client.market(symbol)

    def _tamanho_contrato(self, symbol: str) -> float:
        mkt = self.mercado(symbol)
        return float(mkt.get("contractSize") or 1.0) if mkt.get("contract") else 1.0

    def buscar_candles(self, symbol: str, timeframe: str, limite: int = 300) -> pd.DataFrame:
        """Retorna OHLCV indexado por timestamp UTC com paginação automática."""
        if limite <= 200:
            bruto = self._retry(self.client.fetch_ohlcv, symbol, timeframe, limit=limite)
        else:
            # Paginação para quando a corretora limita lotes a 200
            bruto = []
            duracao_seg = self.client.parse_timeframe(timeframe)
            agora_ms = int(time.time() * 1000)
            inicio_ms = agora_ms - (limite * duracao_seg * 1000)
            since = inicio_ms

            while len(bruto) < limite:
                qtd_lote = min(200, limite - len(bruto))
                lote = self._retry(self.client.fetch_ohlcv, symbol, timeframe, since=since, limit=qtd_lote)
                if not lote:
                    break
                bruto.extend(lote)
                since = lote[-1][0] + (duracao_seg * 1000)
                if len(lote) < qtd_lote:
                    break

        df = pd.DataFrame(bruto, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = df.drop_duplicates(subset=["timestamp"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.set_index("timestamp").astype(float)

    def preco_atual(self, symbol: str) -> float:
        ticker = self._retry(self.client.fetch_ticker, symbol)
        return float(ticker["last"])

    def saldo_livre(self, moeda: str) -> float:
        if self.paper:
            return self.paper.saldo_livre(moeda)
        saldo = self._retry(self.client.fetch_balance)
        return float(saldo.get("free", {}).get(moeda) or 0.0)

    def sincronizar(self, symbol: str, preco: float) -> list[dict]:
        """No modo paper, dispara as ordens stop atingidas pelo preço atual.
        No modo live não faz nada (a própria KuCoin executa os stops)."""
        if self.paper:
            return self.paper.processar_preco(symbol, preco)
        return []

    def definir_alavancagem(self, symbol: str, alavancagem: int) -> None:
        if not self.is_futures or self.paper:
            return  # no paper a alavancagem vai nos params da ordem
        try:
            self.client.set_leverage(alavancagem, symbol)
        except ccxt.NotSupported:
            log.debug("set_leverage não suportado; alavancagem enviada nos params da ordem.")

    def obter_margin_mode(self, symbol: str) -> str | None:
        """Retorna o modo de margem ('cross' ou 'isolated') para ordens futures na KuCoin."""
        if not self.is_futures:
            return None
        if self.margin_mode in ("cross", "isolated"):
            return self.margin_mode
        if self.paper:
            return "cross"
        if symbol not in self._margin_modes_por_simbolo:
            try:
                self.carregar_mercados()
                info = self._retry(self.client.fetch_margin_mode, symbol)
                modo_detectado = str(info.get("marginMode") or "").strip().lower()
                if modo_detectado in ("cross", "isolated"):
                    self._margin_modes_por_simbolo[symbol] = modo_detectado
                    log.info("Modo de margem detectado na KuCoin para %s: %s", symbol, modo_detectado)
            except Exception as exc:
                log.warning("Não foi possível consultar marginMode de %s (%s); mantendo padrão.", symbol, exc)
        return self._margin_modes_por_simbolo.get(symbol)

    # ------------------------------------------------------------------ #
    # Ordens
    # ------------------------------------------------------------------ #
    def quantidade_para_amount(self, symbol: str, qtd_base: float) -> float:
        """Converte quantidade na moeda base para o 'amount' da exchange
        (em futures a KuCoin trabalha com número de contratos)."""
        return qtd_base / self._tamanho_contrato(symbol)

    def _normalizar(self, symbol: str, amount: float, price: float | None) -> tuple[float, float | None]:
        mkt = self.mercado(symbol)
        amount_norm = float(self.client.amount_to_precision(symbol, amount))
        minimo = (mkt.get("limits", {}).get("amount", {}) or {}).get("min")
        if amount_norm <= 0 or (minimo and amount_norm < float(minimo)):
            raise ValueError(f"Quantidade {amount} abaixo do mínimo permitido ({minimo}) para {symbol}")
        price_norm = float(self.client.price_to_precision(symbol, price)) if price is not None else None
        return amount_norm, price_norm

    def criar_ordem(
        self,
        symbol: str,
        side: str,
        amount: float,
        tipo: str = "market",
        price: float | None = None,
        params: dict | None = None,
    ) -> dict:
        """Envia uma ordem. NÃO há retry automático: em caso de erro de rede a
        ordem pode ter sido aceita, então o chamador deve conferir pelo clientOid."""
        amount, price = self._normalizar(symbol, amount, price)
        client_oid = f"fmt-{uuid.uuid4().hex[:24]}"
        params = {"clientOid": client_oid, **(params or {})}
        if self.is_futures and "marginMode" not in params:
            mm = self.obter_margin_mode(symbol)
            if mm:
                params["marginMode"] = mm

        log.info("Ordem -> [%s] %s %s %s amount=%s price=%s params=%s",
                 self.modo, tipo, side, symbol, amount, price, params)

        if self.paper:
            if tipo != "market":
                raise ValueError("Simulador paper suporta apenas ordens a mercado e stops")
            ordem = self.paper.criar_ordem(
                symbol, side, amount, self._tamanho_contrato(symbol),
                self.preco_atual(symbol), params, client_oid,
            )
            if ordem["status"] == "rejected":
                raise ValueError(f"Ordem rejeitada pelo simulador: {ordem.get('motivo')}")
            return ordem

        try:
            ordem = self.client.create_order(symbol, tipo, side, amount, price, params)
        except ccxt.InvalidOrder as exc:
            # Se o usuário trocou o modo de margem na KuCoin com o bot rodando (erro 330005),
            # invalida o cache, recarrega o marginMode real da conta e tenta uma única vez.
            if self.is_futures and "330005" in str(exc) and self.margin_mode == "auto":
                self._margin_modes_por_simbolo.pop(symbol, None)
                novo_mm = self.obter_margin_mode(symbol)
                if novo_mm and novo_mm != params.get("marginMode"):
                    params["marginMode"] = novo_mm
                    params["clientOid"] = f"fmt-{uuid.uuid4().hex[:24]}"
                    log.warning("Conflito 330005 detectado; reenviando com marginMode=%s", novo_mm)
                    ordem = self.client.create_order(symbol, tipo, side, amount, price, params)
                else:
                    raise
            else:
                raise
        ordem.setdefault("clientOrderId", params["clientOid"])
        return ordem

    def criar_stop_loss(self, symbol: str, side_posicao: str, amount: float, preco_stop: float) -> dict:
        """Coloca stop loss (ordem a mercado disparada em preco_stop)."""
        side_saida = "sell" if side_posicao == "buy" else "buy"
        params: dict[str, Any] = {"stopLossPrice": float(self.client.price_to_precision(symbol, preco_stop))}
        if self.is_futures:
            params["reduceOnly"] = True
        return self.criar_ordem(symbol, side_saida, amount, "market", None, params)

    def fechar_posicao(self, symbol: str, side_posicao: str, amount: float) -> dict:
        side_saida = "sell" if side_posicao == "buy" else "buy"
        params = {"reduceOnly": True} if self.is_futures else {}
        return self.criar_ordem(symbol, side_saida, amount, "market", None, params)

    def cancelar_ordem(self, order_id: str, symbol: str, params: dict | None = None) -> None:
        if self.paper:
            self.paper.cancelar_ordem(order_id)
            return
        try:
            self.client.cancel_order(order_id, symbol, params or {})
        except ccxt.OrderNotFound:
            log.warning("Ordem %s não encontrada ao cancelar (já executada/cancelada?).", order_id)

    def buscar_ordem(self, order_id: str, symbol: str, params: dict | None = None) -> dict:
        if self.paper:
            return dict(self.paper.buscar_ordem(order_id))
        return self._retry(self.client.fetch_order, order_id, symbol, params or {})
