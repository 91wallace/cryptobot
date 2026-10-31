"""Orquestrador principal do bot Apex Dinamics.

  * Carrega configuração do .env
  * Loop: candles fechados -> gestão da posição -> avaliação de sinal -> risco -> ordens
  * Persistência em SQLite (ordens, sinais e estado da posição p/ retomada após reinício)
  * Logs em console e em logs/bot.log
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
for p in [str(BASE_DIR), str(BASE_DIR / "core"), str(BASE_DIR / "strategies")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from exchange import Exchange
from risk import GerenciadorRisco, ParametrosRisco
from strategy import Direcao, EstrategiaFimathe, Sinal
from core.laya_telemetry import default_telemetry_collector, TradeOutcome

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"

log = logging.getLogger("fimathe")


# ---------------------------------------------------------------------- #
# Configuração / logs
# ---------------------------------------------------------------------- #
def configurar_logs() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    raiz = logging.getLogger()
    raiz.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    arquivo = RotatingFileHandler(LOG_DIR / "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    arquivo.setFormatter(fmt)
    raiz.handlers[:] = [console, arquivo]


@dataclass
class Config:
    symbol: str
    quote: str
    timeframe: str
    candle_limit: int
    loop_interval: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            symbol=os.getenv("SYMBOL", "BTC/USDT"),
            quote=os.getenv("QUOTE_CURRENCY", "USDT"),
            timeframe=os.getenv("TIMEFRAME", "1h"),
            candle_limit=int(os.getenv("CANDLE_LIMIT", "300")),
            loop_interval=int(os.getenv("LOOP_INTERVAL_SEC", "60")),
        )


# ---------------------------------------------------------------------- #
# Persistência
# ---------------------------------------------------------------------- #
class Persistencia:
    def __init__(self, caminho: Path) -> None:
        caminho.parent.mkdir(exist_ok=True)
        self.conn = sqlite3.connect(caminho)
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ordens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT, side TEXT, tipo TEXT, papel TEXT,
                amount REAL, price REAL,
                exchange_id TEXT, client_oid TEXT, status TEXT,
                paper INTEGER, bruto TEXT,
                strategy TEXT DEFAULT 'apex_dinamics'
            );
            CREATE TABLE IF NOT EXISTS sinais (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT, direcao TEXT, entrada REAL, stop REAL, alvos TEXT,
                aceito INTEGER, motivo TEXT,
                strategy TEXT DEFAULT 'apex_dinamics'
            );
            CREATE TABLE IF NOT EXISTS estado (
                chave TEXT PRIMARY KEY,
                valor TEXT
            );
            """
        )
        for tab in ("ordens", "sinais"):
            cols = [r[1] for r in self.conn.execute(f"PRAGMA table_info({tab});").fetchall()]
            if "strategy" not in cols:
                self.conn.execute(f"ALTER TABLE {tab} ADD COLUMN strategy TEXT DEFAULT 'apex_dinamics';")
        self.conn.commit()

    @staticmethod
    def _agora() -> str:
        return datetime.now(timezone.utc).isoformat()

    def registrar_ordem(self, ordem: dict, papel: str, strategy: str = "apex_dinamics") -> None:
        self.conn.execute(
            "INSERT INTO ordens (ts, symbol, side, tipo, papel, amount, price, exchange_id,"
            " client_oid, status, paper, bruto, strategy) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self._agora(), ordem.get("symbol"), ordem.get("side"), ordem.get("type"), papel,
                ordem.get("amount"), ordem.get("average") or ordem.get("price"),
                ordem.get("id"), ordem.get("clientOrderId"), ordem.get("status"),
                int(bool(ordem.get("paper"))), json.dumps(ordem, default=str), strategy,
            ),
        )
        self.conn.commit()

    def registrar_sinal(self, symbol: str, sinal: Sinal, aceito: bool, motivo: str, strategy: str = "apex_dinamics") -> None:
        self.conn.execute(
            "INSERT INTO sinais (ts, symbol, direcao, entrada, stop, alvos, aceito, motivo, strategy)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                self._agora(), symbol, sinal.direcao.value, sinal.preco_entrada,
                sinal.stop_loss, json.dumps(sinal.alvos), int(aceito), motivo, strategy,
            ),
        )
        self.conn.commit()

    def salvar_estado(self, chave: str, valor: dict | None) -> None:
        self.conn.execute(
            "INSERT INTO estado (chave, valor) VALUES (?, ?)"
            " ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
            (chave, json.dumps(valor) if valor is not None else None),
        )
        self.conn.commit()

    def carregar_estado(self, chave: str) -> dict | None:
        linha = self.conn.execute("SELECT valor FROM estado WHERE chave = ?", (chave,)).fetchone()
        return json.loads(linha[0]) if linha and linha[0] else None


# ---------------------------------------------------------------------- #
# Bot
# ---------------------------------------------------------------------- #
ESTRATEGIAS_VALIDAS = ("lite-3.8",)
LITE38_FATOR_DEGRAU = 1.00
CB_JANELA_HORAS = float(os.getenv("CB_JANELA_HORAS", "24.0"))
CB_PAUSA_HORAS = float(os.getenv("CB_PAUSA_HORAS", "6.0"))
CB_MAX_LOSSES = int(os.getenv("CB_MAX_LOSSES", "1"))


@dataclass
class Posicao:
    symbol: str
    side: str            # "buy" | "sell"
    amount: float        # unidade da exchange (moeda base no spot, contratos no futures)
    quantidade: float    # moeda base
    entrada: float
    stop: float
    alvo: float
    stop_order_id: str | None
    aberta_em: str
    amplitude: float = 0.0
    extremo: float | None = None
    strategy: str = "apex_dinamics"
    strategy_id: str = "STRAT_APEX_DINAMICS_V1"
    # Política de saída P90: trailing por gatilho + espaçamento fixo
    trailing_ativo: bool = False
    trig: float = 0.0      # avanço absoluto (no preço) que ativa o trailing
    espaco: float = 0.0    # espaçamento absoluto do stop a partir do extremo


class Bot:
    def __init__(self) -> None:
        self.cfg = Config.from_env()
        self.exchange = Exchange.from_env()
        self.perfil = "lite-3.8"

        # Estratégia Oficial Campeã: Rompimento Trailingstop Lite-3.8
        self.estrategia = EstrategiaFimathe(
            janela_pivo=int(os.getenv("JANELA_PIVO", "4")),
            qtd_niveis=int(os.getenv("QTD_NIVEIS", "4")),
            folga_stop=float(os.getenv("FOLGA_STOP", "0.10")),
            amplitude_min_pct=float(os.getenv("AMPLITUDE_MIN_PCT", "0.40")),
            modo_stop=os.getenv("STOP_MODO", "fora_caixinha").strip().lower(),
            atr_filtro_ativo=os.getenv("ATR_FILTRO_ATIVO", "false").strip().lower() in ("1", "true", "yes", "sim"),
            atr_periodo=int(os.getenv("ATR_PERIODO", "14")),
            fator_min_atr=float(os.getenv("FATOR_MIN_ATR", "0.8")),
            fator_max_atr=float(os.getenv("FATOR_MAX_ATR", "3.5")),
            romper_no_canal=True,
            ker_filtro_ativo=os.getenv("KER_FILTRO_ATIVO", "false").strip().lower() in ("1", "true", "yes", "sim"),
            ker_minimo=float(os.getenv("KER_MINIMO", "0.25")),
            ker_periodo=int(os.getenv("KER_PERIODO", "12")),
        )
        alav_max = int(os.getenv("ALAVANCAGEM_MAX", "5"))
        self.fracao_banca = float(os.getenv("FRACAO_BANCA", "0.333"))
        self.apex = os.getenv("APEX", "false").strip().lower() in ("1", "true", "yes", "sim")
        self.risco = GerenciadorRisco(
            ParametrosRisco(
                risco_por_trade_pct=float(os.getenv("RISCO_POR_TRADE_PCT", "1.0")),
                alavancagem_max=alav_max,
                max_posicoes_abertas=int(os.getenv("MAX_POSICOES_ABERTAS", "1")),
                perda_diaria_max_pct=float(os.getenv("PERDA_DIARIA_MAX_PCT", "3.0")),
                rr_minimo=float(os.getenv("RR_MINIMO", "1.0")),
            )
        )
        self.db = Persistencia(DATA_DIR / "bot.db")
        self.cb: dict = self.db.carregar_estado("cb") or {"suspenso_ate": 0.0, "fechamentos": []}
        self.laya_veto_ativo = os.getenv("LAYA_VETO_ATIVO", "true").strip().lower() in ("1", "true", "yes", "sim")
        self.laya_bm25_min = float(os.getenv("LAYA_BM25_MIN", "12.0"))
        self.telemetria = default_telemetry_collector

        # Execução e política de saída (validação walk-forward + matriz net de taxas):
        #  TIPO_ORDEM=LIMIT_RETESTE  -> ordem limit maker no nível rompido (RT 0.08% vs 0.12% MARKET)
        #  POLITICA_SAIDA=P90        -> stop % fixo + trailing por gatilho/espaçamento
        self.tipo_ordem = os.getenv("TIPO_ORDEM", "MARKET").strip().upper()
        self.prazo_reteste = int(os.getenv("PRAZO_RETESTE_CANDLES", "4"))
        self.politica_saida = os.getenv("POLITICA_SAIDA", "LIVE").strip().upper()
        self.p90_stop_pct = float(os.getenv("P90_STOP_PCT", "2.052"))
        self.p90_trigger_pct = float(os.getenv("P90_TRIGGER_PCT", "0.859"))
        self.p90_espaco_mult = float(os.getenv("P90_ESPACO_MULT", "1.0"))

        estado_pos = self.db.carregar_estado("posicao")
        self.posicao: Posicao | None = Posicao(**estado_pos) if estado_pos else None
        self.pendente = self.db.carregar_estado("pendente")  # ordem limit de reteste pendente (dict)
        estado_dia = self.db.carregar_estado("dia") or {}
        self.dia: str = estado_dia.get("dia", self._hoje())
        self.pnl_dia: float = float(estado_dia.get("pnl", 0.0))
        self.ultimo_candle = None

        if self.posicao:
            log.info("Posição restaurada do banco: %s", self.posicao)

    # ---------------- utilidades ---------------- #
    @staticmethod
    def _hoje() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _capital(self) -> float:
        # No modo paper o saldo vem do simulador nativo; no live, da KuCoin.
        return self.exchange.saldo_livre(self.cfg.quote)

    def _salvar_posicao(self) -> None:
        self.db.salvar_estado("posicao", asdict(self.posicao) if self.posicao else None)

    def _salvar_pendente(self) -> None:
        self.db.salvar_estado("pendente", self.pendente if self.pendente else None)

    # ---------------- Política P90 (validação walk-forward + matriz de taxas) ---------------- #
    def _stop_entrada(self, sinal: Sinal, entrada: float) -> float:
        """Stop efetivo da entrada: P90 usa distância % fixa; LIVE usa o stop estrutural do sinal."""
        if self.politica_saida == "P90":
            compra = sinal.direcao is Direcao.COMPRA
            dist = entrada * self.p90_stop_pct / 100.0
            return entrada - dist if compra else entrada + dist
        return sinal.stop_loss

    def _trailing_pesos(self, sinal: Sinal, entrada: float) -> tuple[float, float]:
        """(trig, espaco) do trailing P90; (0, 0) na política LIVE (degraus da estratégia)."""
        if self.politica_saida != "P90":
            return 0.0, 0.0
        dist = entrada * self.p90_stop_pct / 100.0
        return entrada * self.p90_trigger_pct / 100.0, dist * self.p90_espaco_mult

    def _timeframe_segundos(self, tf: str) -> int:
        mult = {"m": 60, "h": 3600, "d": 86400}
        try:
            return int(int(tf[:-1]) * mult.get(tf[-1].lower(), 60))
        except (ValueError, KeyError):
            return 300

    def _salvar_dia(self) -> None:
        self.db.salvar_estado("dia", {"dia": self.dia, "pnl": self.pnl_dia})

    def _virar_dia(self) -> None:
        hoje = self._hoje()
        if hoje != self.dia:
            log.info("Novo dia (%s). PnL do dia anterior: %.4f", hoje, self.pnl_dia)
            self.dia, self.pnl_dia = hoje, 0.0
            self._salvar_dia()

    # ---------------- ciclo ---------------- #
    def ciclo(self) -> None:
        self._virar_dia()

        if self.pendente:
            self._gerenciar_pendente()
            return

        if self.posicao:
            self._gerenciar_posicao()
            return

        if time.time() < self.cb.get("suspenso_ate", 0.0):
            return  # circuit breaker ativo: sem novas entradas até o fim da pausa

        df = self.exchange.buscar_candles(self.cfg.symbol, self.cfg.timeframe, self.cfg.candle_limit)
        df = df.iloc[:-1]  # descarta o candle em formação: decide apenas em candles fechados
        if df.empty or df.index[-1] == self.ultimo_candle:
            return  # nada novo
        self.ultimo_candle = df.index[-1]

        sinal = self.estrategia.avaliar(df)
        if sinal is None:
            log.debug("Sem sinal em %s", self.ultimo_candle)
            return
        self._processar_sinal(sinal)

    def _processar_sinal(self, sinal: Sinal) -> None:
        log.info("Sinal: %s", sinal)

        if sinal.direcao is Direcao.VENDA and not self.exchange.is_futures:
            self.db.registrar_sinal(self.cfg.symbol, sinal, False, "venda a descoberto indisponível no spot")
            log.info("Sinal de venda ignorado (mercado spot).")
            return

        capital = self._capital()
        ok, motivo = self.risco.validar_sinal(sinal, capital, int(self.posicao is not None), self.pnl_dia)
        if not ok:
            self.db.registrar_sinal(self.cfg.symbol, sinal, False, motivo)
            log.info("Sinal rejeitado pelo risco: %s", motivo)
            return

        # Filtro de Veto Laya System 1 (Graveyard Anti-Erro)
        if self.laya_veto_ativo and self.telemetria is not None:
            prompt_auditoria = f"Entrada {sinal.direcao.value.upper()} em {self.cfg.symbol} via {sinal.motivo}"
            vetado, motivo_veto = self.telemetria.is_action_vetoed(prompt_auditoria, min_bm25=self.laya_bm25_min)
            if vetado:
                motivo_rejeicao = f"Veto Laya System 1: {motivo_veto}"
                self.db.registrar_sinal(self.cfg.symbol, sinal, False, motivo_rejeicao)
                log.warning("🛡️ [LAYA VETO ANTI-ERRO] %s | Entrada bloqueada.", motivo_rejeicao)
                return

        if self.apex or self.fracao_banca > 0:  # Usa fração da banca (ex: 1.0 para 100% juros compostos)
            qtd = capital * self.fracao_banca * self.risco.params.alavancagem_max / sinal.preco_entrada
        else:
            qtd = self.risco.calcular_quantidade(capital, sinal.preco_entrada, sinal.stop_loss)
        if not self.exchange.is_futures:
            qtd = min(qtd, capital * 0.99 / sinal.preco_entrada)  # spot: sem alavancagem (reserva p/ taxas)
        amount = self.exchange.quantidade_para_amount(self.cfg.symbol, qtd)

        params: dict = {}
        if self.exchange.is_futures:
            alav = (self.risco.params.alavancagem_max if self.apex
                    else self.risco.alavancagem_necessaria(qtd, sinal.preco_entrada, capital))
            self.exchange.definir_alavancagem(self.cfg.symbol, alav)
            params["leverage"] = alav

        if self.tipo_ordem == "LIMIT_RETESTE":
            self._colocar_entrada_reteste(sinal, qtd, amount, params)
            return

        try:
            entrada = self.exchange.criar_ordem(self.cfg.symbol, sinal.direcao.value, amount, "market", None, params)
        except ValueError as exc:
            self.db.registrar_sinal(self.cfg.symbol, sinal, False, str(exc))
            log.warning("Ordem não enviada: %s", exc)
            return

        self.db.registrar_sinal(self.cfg.symbol, sinal, True, sinal.motivo)
        self.db.registrar_ordem(entrada, "entrada")
        amount_exec = float(entrada.get("filled") or entrada.get("amount") or amount)
        preco_exec = float(entrada.get("average") or entrada.get("price") or sinal.preco_entrada)

        stop_pos = self._stop_entrada(sinal, preco_exec)
        trig, espaco = self._trailing_pesos(sinal, preco_exec)
        stop_order = self.exchange.criar_stop_loss(self.cfg.symbol, sinal.direcao.value, amount_exec, stop_pos)
        self.db.registrar_ordem(stop_order, "stop_loss")

        amplitude = float((sinal.grade or {}).get("amplitude") or 0.0)
        if self.apex:  # Apex conduz até o alvo final (último nível), com trailing por degraus
            alvo = float(sinal.alvos[-1] if len(sinal.alvos) >= 2 else
                         (preco_exec + amplitude * 3 if sinal.direcao is Direcao.COMPRA else preco_exec - amplitude * 3))
        else:
            alvo = sinal.alvos[0]

        strat_id = getattr(sinal, "strategy_id", None) or getattr(self.estrategia, "strategy_id", "STRAT_APEX_DINAMICS_V1")
        strat_name = getattr(sinal, "strategy_name", None) or getattr(self.estrategia, "name", "apex_dinamics")

        self.posicao = Posicao(
            symbol=self.cfg.symbol,
            side=sinal.direcao.value,
            amount=amount_exec,
            quantidade=qtd,
            entrada=preco_exec,
            stop=stop_pos,
            alvo=alvo,
            stop_order_id=stop_order.get("id"),
            aberta_em=datetime.now(timezone.utc).isoformat(),
            amplitude=amplitude,
            extremo=preco_exec,
            strategy=strat_name,
            strategy_id=strat_id,
            trailing_ativo=False,
            trig=trig,
            espaco=espaco,
        )
        self._salvar_posicao()
        log.info("Posição aberta: %s", self.posicao)

    # ---------------- Entrada LIMIT_RETESTE (maker no nível rompido) ---------------- #
    def _colocar_entrada_reteste(self, sinal: Sinal, qtd: float, amount: float, params: dict) -> None:
        m = re.search(r"\(([\d.]+)\)", sinal.motivo or "")
        if not m:
            # Política validada aguarda o reteste; sem nível não há ordem maker.
            self.db.registrar_sinal(self.cfg.symbol, sinal, False, "LIMIT_RETESTE: nível do rompimento não identificado no motivo")
            log.warning("LIMIT_RETESTE: falha ao fazer parse do nível do rompimento no motivo: %s", sinal.motivo)
            return
        nivel = float(m.group(1))
        tf = self._timeframe_segundos(self.cfg.timeframe)

        pend: dict = {
            "symbol": self.cfg.symbol,
            "side": sinal.direcao.value,
            "nivel": nivel,
            "amount": amount,
            "quantidade": qtd,
            "params": params,
            "stop_struct": sinal.stop_loss,
            "alvos": list(sinal.alvos or []),
            "motivo": sinal.motivo,
            "amplitude": float((sinal.grade or {}).get("amplitude") or 0.0),
            "strategy": getattr(self.estrategia, "name", "apex_dinamics"),
            "strategy_id": getattr(self.estrategia, "strategy_id", "STRAT_APEX_DINAMICS_V1"),
            "ts_candle": int(time.time() // tf) * tf,  # início do candle atual (janela: fechados após ele)
            "visto_ate": None,
            "esperados": 0,
            "order_id": None,
        }

        if not self.exchange.paper:
            # Live: ordem limit maker real (postOnly) no nível rompido.
            try:
                entrada = self.exchange.criar_ordem(self.cfg.symbol, sinal.direcao.value, amount, "limit", nivel,
                                                    {**params, "postOnly": True})
            except ValueError as exc:
                self.db.registrar_sinal(self.cfg.symbol, sinal, False, f"LIMIT_RETESTE: ordem não enviada: {exc}")
                log.warning("LIMIT_RETESTE: ordem não enviada: %s", exc)
                return
            self.db.registrar_ordem(entrada, "entrada_reteste", pend["strategy"])
            pend["order_id"] = entrada.get("id")
            log.info("Ordem LIMIT_RETESTE colocada na exchange: id=%s @ %.8g", pend["order_id"], nivel)

        self.db.registrar_sinal(self.cfg.symbol, sinal, True,
                                "%s | ordem de reteste @ %.8g (prazo %d candles)" % (sinal.motivo, nivel, self.prazo_reteste))
        self.pendente = pend
        self._salvar_pendente()
        log.info("Aguardando reteste: %s %s @ %.8g (prazo %d candles)", sinal.direcao.value, self.cfg.symbol, nivel, self.prazo_reteste)

    def _gerenciar_pendente(self) -> None:
        pend = self.pendente
        assert pend is not None
        tf_seg = self._timeframe_segundos(self.cfg.timeframe)
        df = self.exchange.buscar_candles(pend["symbol"], self.cfg.timeframe, self.prazo_reteste + 3)
        df = df.iloc[:-1]  # apenas candles fechados
        if df.empty:
            return
        compra = pend["side"] == "buy"
        tocado = False
        for ts, row in df.iterrows():
            ts_seg = float(ts.timestamp())
            if pend.get("visto_ate") and ts_seg <= pend["visto_ate"]:
                continue
            if ts_seg < pend["ts_candle"]:
                continue
            pend["visto_ate"] = ts_seg
            pend["esperados"] += 1
            hi, lo = float(row["high"]), float(row["low"])
            if (compra and lo <= pend["nivel"]) or (not compra and hi >= pend["nivel"]):
                tocado = True
                break
            if pend["esperados"] >= self.prazo_reteste:
                break

        if tocado:
            self._preencher_reteste(pend)
            return
        if pend["esperados"] >= self.prazo_reteste:
            log.info("Reteste não veio em %d candles; cancelando ordem de entrada.", self.prazo_reteste)
            if pend.get("order_id"):
                try:
                    self.exchange.cancelar_ordem(pend["order_id"], pend["symbol"])
                except Exception as exc:
                    log.warning("Falha ao cancelar a ordem de reteste %s: %s", pend["order_id"], exc)
                else:
                    self.db.registrar_ordem({"id": pend["order_id"], "symbol": pend["symbol"], "status": "cancelled"},
                                            "entrada_reteste_cancelada", pend["strategy"])
            self.pendente = None
            self._salvar_pendente()

    def _preencher_reteste(self, pend: dict) -> None:
        """Fill confirmado: cria stop + posição a partir do nível de reteste."""
        preco_exec = float(pend["nivel"])
        if pend.get("order_id"):
            try:
                ordem = self.exchange.buscar_ordem(pend["order_id"], pend["symbol"])
                preco_exec = float(ordem.get("average") or ordem.get("price") or preco_exec)
            except Exception as exc:
                log.warning("Fill do reteste sem confirmação da exchange (%s); usando o nível.", exc)
        compra = pend["side"] == "buy"

        # Stop/trailing: P90 por entrada efetiva; LIVE usa o stop estrutural do sinal.
        if self.politica_saida == "P90":
            dist = preco_exec * self.p90_stop_pct / 100.0
            stop = preco_exec - dist if compra else preco_exec + dist
            trig = preco_exec * self.p90_trigger_pct / 100.0
            espaco = dist * self.p90_espaco_mult
        else:
            stop = pend["stop_struct"]
            trig, espaco = 0.0, 0.0

        stop_order = self.exchange.criar_stop_loss(pend["symbol"], pend["side"], pend["amount"], stop)
        self.db.registrar_ordem(stop_order, "stop_loss", pend["strategy"])
        alvos = pend.get("alvos") or []
        alvo = float(alvos[0]) if alvos else preco_exec

        self.posicao = Posicao(
            symbol=pend["symbol"],
            side=pend["side"],
            amount=float(pend["amount"]),
            quantidade=float(pend["quantidade"]),
            entrada=preco_exec,
            stop=stop,
            alvo=alvo,
            stop_order_id=stop_order.get("id"),
            aberta_em=datetime.now(timezone.utc).isoformat(),
            amplitude=float(pend.get("amplitude") or 0.0),
            extremo=preco_exec,
            strategy=pend["strategy"],
            strategy_id=pend["strategy_id"],
            trailing_ativo=False,
            trig=trig,
            espaco=espaco,
        )
        self.pendente = None
        self._salvar_pendente()
        self._salvar_posicao()
        log.info("Reteste preenchido: %s @ %.8g | stop %.8g | alvo %.8g | trig %.8g espaco %.8g",
                 pend["side"], preco_exec, stop, alvo, trig, espaco)

    def _consultar_stop(self, p: Posicao, params: dict) -> dict | None:
        if not p.stop_order_id:
            return None
        try:
            return self.exchange.buscar_ordem(p.stop_order_id, p.symbol, params)
        except Exception as exc:  # falha de consulta não pode travar a gestão
            log.warning("Não foi possível consultar o stop %s: %s", p.stop_order_id, exc)
            return None

    def _gerenciar_posicao(self) -> None:
        p = self.posicao
        assert p is not None
        preco = self.exchange.preco_atual(p.symbol)
        self.exchange.sincronizar(p.symbol, preco)  # paper: dispara stops atingidos
        params_stop = {"trigger": True}

        # 1) Stop já executado (pela KuCoin no live ou pelo simulador no paper)?
        ordem_stop = self._consultar_stop(p, params_stop)
        if ordem_stop and ordem_stop.get("status") == "closed":
            self.db.registrar_ordem(ordem_stop, "stop_executado")
            lucro_travado = (p.stop > p.entrada) if p.side == "buy" else (p.stop < p.entrada)
            self._encerrar(p, float(ordem_stop.get("average") or p.stop),
                           "trailing_stop" if lucro_travado else "stop")
            return

        self._atualizar_trailing(p, preco)

        compra = p.side == "buy"
        atingiu_stop = preco <= p.stop if compra else preco >= p.stop
        atingiu_alvo = preco >= p.alvo if compra else preco <= p.alvo
        if not (atingiu_stop or atingiu_alvo):
            return

        # 2) Alvo atingido, ou stop cruzado sem execução confirmada: zera a mercado.
        papel = "alvo" if atingiu_alvo else "stop_manual"
        log.info("%s em %.8g. Encerrando posição a mercado.",
                 "Alvo atingido" if atingiu_alvo else "Stop cruzado sem confirmação", preco)
        if p.stop_order_id:
            self.exchange.cancelar_ordem(p.stop_order_id, p.symbol, params_stop)
        saida = self.exchange.fechar_posicao(p.symbol, p.side, p.amount)
        self.db.registrar_ordem(saida, papel)
        self._encerrar(p, float(saida.get("average") or preco), papel)

    def _encerrar(self, p: Posicao, preco_saida: float, motivo: str) -> None:
        pnl = (preco_saida - p.entrada) * p.quantidade * (1 if p.side == "buy" else -1)
        retorno_pct = ((preco_saida - p.entrada) / p.entrada) * (1 if p.side == "buy" else -1) if p.entrada > 0 else 0.0
        self.pnl_dia += pnl
        self._salvar_dia()
        log.info("Posição encerrada (%s) @ %.8g | PnL bruto: %.4f | PnL do dia: %.4f | saldo: %.4f",
                 motivo, preco_saida, pnl, self.pnl_dia, self._capital())

        # Telemetria e Aprendizado Contínuo com Laya System 1
        if self.telemetria is not None:
            try:
                strat_id = getattr(p, "strategy_id", None) or getattr(self.estrategia, "strategy_id", "STRAT_APEX_DINAMICS_V1")
                strat_name = getattr(p, "strategy", None) or getattr(self.estrategia, "name", "apex_dinamics")
                base_asset = self.telemetria.extract_base_asset(p.symbol)
                exchange_id = getattr(getattr(self.exchange, "client", None), "id", "kucoin")
                market_type = getattr(self.exchange, "market_type", "futures")
                venue = f"{exchange_id}_{market_type}"

                duracao = 0.0
                if p.aberta_em:
                    try:
                        t_abertura = datetime.fromisoformat(p.aberta_em).timestamp()
                        duracao = max(0.0, time.time() - t_abertura)
                    except Exception:
                        duracao = 0.0

                success = pnl > 0
                feedback_label = "STANDARD_TRADE_COMPLETED"
                if motivo == "alvo":
                    feedback_label = "CLEAN_CATALYST_TARGET_HIT"
                elif "trailing" in motivo:
                    feedback_label = "SUPER_RUNNER_TRAILING_SUCCESS"
                elif "stop" in motivo:
                    feedback_label = "FALSE_CATALYST_STOPPED_OUT"

                extremo = getattr(p, "extremo", p.entrada) or p.entrada
                highest = max(p.entrada, preco_saida, extremo)
                lowest = min(p.entrada, preco_saida)

                outcome = TradeOutcome(
                    trade_id=f"trade-{int(time.time()*1000)}",
                    symbol=p.symbol,
                    direction="COMPRA" if p.side.lower() == "buy" else "VENDA",
                    entry_price=p.entrada,
                    exit_price=preco_saida,
                    entry_time=time.time() - duracao,
                    exit_time=time.time(),
                    duration_seconds=round(duracao, 1),
                    headline=f"Operação {p.side.upper()} em {p.symbol} encerrada via {motivo}",
                    predicted_impact="BULLISH_CATALYST" if p.side.lower() == "buy" else "BEARISH_SHOCK",
                    confidence=0.85,
                    initial_reversal_risk=0.20 if success else 0.85,
                    highest_price=highest,
                    lowest_price=lowest,
                    mfe_pct=round(max(0.0, retorno_pct), 5),
                    mae_pct=round(min(0.0, retorno_pct), 5),
                    realized_pnl_pct=round(retorno_pct, 5),
                    realized_pnl_usdt=round(pnl, 2),
                    exit_reason=motivo.upper(),
                    trailing_tightened=False,
                    exhaustion_alerts_count=0,
                    reward_score=round(retorno_pct - (1.2 * abs(min(0.0, retorno_pct))), 5),
                    success=success,
                    laya_feedback_label=feedback_label,
                    strategy_id=strat_id,
                    strategy_name=strat_name,
                    asset=base_asset,
                    exchange=exchange_id,
                    market_type=market_type,
                    venue=venue,
                    broker="KuCoin",
                    broker_mode=getattr(self.exchange, "modo", "paper"),
                    timeframe=self.cfg.timeframe,
                    market_regime="TENDENCIA",
                    volatility_atr_pct=round(getattr(p, "amplitude", 0.0) / p.entrada, 4) if p.entrada > 0 else 0.0,
                    btc_correlation_regime="NEUTRAL",
                    volume_surge_ratio=1.0,
                    slippage_pct=0.0,
                    order_type="MARKET_TAKER",
                    news_source="Technical/Fimathe",
                    specific_vs_market_wide="SPECIFIC_ASSET",
                    leverage=float(getattr(self.risco.p, "alavancagem_max", 5.0)),
                    planned_rr_ratio=round(abs(p.alvo - p.entrada) / abs(p.stop - p.entrada), 2) if abs(p.stop - p.entrada) > 0 else 1.0,
                    realized_rr_ratio=round(retorno_pct / (abs(p.entrada - p.stop) / p.entrada), 2) if (p.entrada > 0 and abs(p.entrada - p.stop) > 0) else 0.0,
                )
                self.telemetria.record_trade_outcome(outcome)
            except Exception as e:
                log.debug("Erro ao gravar telemetria Laya: %s", e)

        self.posicao = None
        self._salvar_posicao()
        self._registrar_circuit_breaker(pnl > 0 or motivo == "alvo")

    # ---------------- Trailing Stop Progressivo & Circuit Breaker Oficial ---------------- #
    def _atualizar_trailing(self, p: Posicao, preco: float) -> None:
        """Conduz o stop dinâmico delegando para a estratégia ou cálculo por degraus."""
        if p.trig > 0 or p.trailing_ativo:  # política P90: gatilho + espaçamento
            self._trailing_p90(p, preco)
            return
        novo = None
        if hasattr(self.estrategia, "calcular_novo_stop"):
            novo = self.estrategia.calcular_novo_stop(p, preco, LITE38_FATOR_DEGRAU)

        if novo is None and getattr(p, "amplitude", 0.0) > 0:
            compra = p.side == "buy"
            base = p.extremo or p.entrada
            p.extremo = max(base, preco) if compra else min(base, preco)

            degrau = p.amplitude * LITE38_FATOR_DEGRAU
            avanco = (p.extremo - p.entrada) if compra else (p.entrada - p.extremo)
            passos = int(avanco // degrau) if degrau > 0 else 0
            if passos >= 2:
                mult = (passos - 1) * LITE38_FATOR_DEGRAU
                cand = p.entrada + mult * p.amplitude if compra else p.entrada - mult * p.amplitude
                if (cand > p.stop) if compra else (cand < p.stop):
                    novo = cand

        if novo is not None:
            try:
                # Cria o novo stop ANTES de cancelar o antigo: a posição nunca fica sem proteção.
                nova = self.exchange.criar_stop_loss(p.symbol, p.side, p.amount, novo)
            except Exception as exc:
                log.warning("Falha ao mover o stop para %.8g (mantendo o atual): %s", novo, exc)
            else:
                if p.stop_order_id:
                    self.exchange.cancelar_ordem(p.stop_order_id, p.symbol, {"trigger": True})
                strat_name = getattr(p, "strategy", "apex_dinamics")
                self.db.registrar_ordem(nova, "stop_trailing", strat_name)
                log.info("[%s] Trailing stop: %.8g -> %.8g", strat_name, p.stop, novo)
                p.stop, p.stop_order_id = novo, nova.get("id")
        self._salvar_posicao()

    def _trailing_p90(self, p: Posicao, preco: float) -> None:
        """Política P90: trailing ativa no avanço de `trig`; espaçamento fixo a partir do extremo."""
        compra = p.side == "buy"
        avanco = (preco - p.entrada) if compra else (p.entrada - preco)
        base = p.extremo if p.extremo is not None else p.entrada
        p.extremo = max(base, preco) if compra else min(base, preco)
        if p.trig > 0 and avanco >= p.trig:
            p.trailing_ativo = True
        novo = None
        if p.trailing_ativo and p.espaco > 0:
            cand = p.extremo - p.espaco if compra else p.extremo + p.espaco
            if (cand > p.stop) if compra else (cand < p.stop):
                novo = cand
        if novo is not None:
            try:
                # Cria o novo stop ANTES de cancelar o antigo: a posição nunca fica sem proteção.
                nova = self.exchange.criar_stop_loss(p.symbol, p.side, p.amount, novo)
            except Exception as exc:
                log.warning("Falha ao mover o stop para %.8g (mantendo o atual): %s", novo, exc)
            else:
                if p.stop_order_id:
                    self.exchange.cancelar_ordem(p.stop_order_id, p.symbol, {"trigger": True})
                self.db.registrar_ordem(nova, "stop_trailing", getattr(p, "strategy", "apex_dinamics"))
                log.info("[P90] Trailing stop: %.8g -> %.8g", p.stop, novo)
                p.stop, p.stop_order_id = novo, nova.get("id")
        self._salvar_posicao()

    def _registrar_circuit_breaker(self, gain: bool) -> None:
        agora = time.time()
        fechamentos = self.cb.get("fechamentos", [])
        fechamentos.append([agora, "GAIN" if gain else "LOSS"])
        fechamentos = [f for f in fechamentos if f[0] >= agora - CB_JANELA_HORAS * 3600]
        self.cb["fechamentos"] = fechamentos
        recentes = [r for _, r in fechamentos]
        if len(recentes) >= CB_MAX_LOSSES and all(r == "LOSS" for r in recentes[-CB_MAX_LOSSES:]):
            self.cb["suspenso_ate"] = agora + CB_PAUSA_HORAS * 3600
            log.warning("CIRCUIT BREAKER: %d loss(es) na janela de %.0fh. Novas entradas pausadas por %.0fh.",
                        CB_MAX_LOSSES, CB_JANELA_HORAS, CB_PAUSA_HORAS)
        self.db.salvar_estado("cb", self.cb)

    # ---------------- loop ---------------- #
    def executar(self) -> None:
        log.info("Bot Apex Dinamics iniciado | estrategia=%s | %s %s | modo=%s | stop=%s | tipo_ordem=%s | saida=%s | KER=%s",
                 self.perfil, self.cfg.symbol, self.cfg.timeframe, self.exchange.modo, self.estrategia.modo_stop,
                 self.tipo_ordem, self.politica_saida,
                 ("ON(%.2f)" % self.estrategia.ker_minimo) if self.estrategia.ker_filtro_ativo else "OFF")
        while True:
            try:
                self.ciclo()
            except KeyboardInterrupt:
                raise
            except Exception:  # mantém o loop vivo; erro fica registrado
                log.exception("Erro no ciclo")
            time.sleep(self.cfg.loop_interval)


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    configurar_logs()
    try:
        Bot().executar()
    except KeyboardInterrupt:
        log.info("Bot encerrado pelo usuário.")


if __name__ == "__main__":
    main()
