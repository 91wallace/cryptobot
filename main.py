"""Orquestrador principal do bot Fimathe.

  * Carrega configuração do .env
  * Loop: candles fechados -> gestão da posição -> avaliação de sinal -> risco -> ordens
  * Persistência em SQLite (ordens, sinais e estado da posição p/ retomada após reinício)
  * Logs em console e em logs/bot.log
"""
from __future__ import annotations

import json
import logging
import os
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
                paper INTEGER, bruto TEXT
            );
            CREATE TABLE IF NOT EXISTS sinais (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT, direcao TEXT, entrada REAL, stop REAL, alvos TEXT,
                aceito INTEGER, motivo TEXT
            );
            CREATE TABLE IF NOT EXISTS estado (
                chave TEXT PRIMARY KEY,
                valor TEXT
            );
            """
        )
        self.conn.commit()

    @staticmethod
    def _agora() -> str:
        return datetime.now(timezone.utc).isoformat()

    def registrar_ordem(self, ordem: dict, papel: str) -> None:
        self.conn.execute(
            "INSERT INTO ordens (ts, symbol, side, tipo, papel, amount, price, exchange_id,"
            " client_oid, status, paper, bruto) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self._agora(), ordem.get("symbol"), ordem.get("side"), ordem.get("type"), papel,
                ordem.get("amount"), ordem.get("average") or ordem.get("price"),
                ordem.get("id"), ordem.get("clientOrderId"), ordem.get("status"),
                int(bool(ordem.get("paper"))), json.dumps(ordem, default=str),
            ),
        )
        self.conn.commit()

    def registrar_sinal(self, symbol: str, sinal: Sinal, aceito: bool, motivo: str) -> None:
        self.conn.execute(
            "INSERT INTO sinais (ts, symbol, direcao, entrada, stop, alvos, aceito, motivo)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                self._agora(), symbol, sinal.direcao.value, sinal.preco_entrada,
                sinal.stop_loss, json.dumps(sinal.alvos), int(aceito), motivo,
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
            ker_filtro_ativo=False,
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

        estado_pos = self.db.carregar_estado("posicao")
        self.posicao: Posicao | None = Posicao(**estado_pos) if estado_pos else None
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

        stop_order = self.exchange.criar_stop_loss(self.cfg.symbol, sinal.direcao.value, amount_exec, sinal.stop_loss)
        self.db.registrar_ordem(stop_order, "stop_loss")

        amplitude = float((sinal.grade or {}).get("amplitude") or 0.0)
        if self.apex:  # Apex conduz até o alvo final (último nível), com trailing por degraus
            alvo = float(sinal.alvos[-1] if len(sinal.alvos) >= 2 else
                         (preco_exec + amplitude * 3 if sinal.direcao is Direcao.COMPRA else preco_exec - amplitude * 3))
        else:
            alvo = sinal.alvos[0]

        self.posicao = Posicao(
            symbol=self.cfg.symbol,
            side=sinal.direcao.value,
            amount=amount_exec,
            quantidade=qtd,
            entrada=preco_exec,
            stop=sinal.stop_loss,
            alvo=alvo,
            stop_order_id=stop_order.get("id"),
            aberta_em=datetime.now(timezone.utc).isoformat(),
            amplitude=amplitude,
            extremo=preco_exec,
        )
        self._salvar_posicao()
        log.info("Posição aberta: %s", self.posicao)

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
        self.pnl_dia += pnl
        self._salvar_dia()
        log.info("Posição encerrada (%s) @ %.8g | PnL bruto: %.4f | PnL do dia: %.4f | saldo: %.4f",
                 motivo, preco_saida, pnl, self.pnl_dia, self._capital())
        self.posicao = None
        self._salvar_posicao()
        self._registrar_circuit_breaker(pnl > 0 or motivo == "alvo")

    # ---------------- Trailing Stop Progressivo & Circuit Breaker Oficial ---------------- #
    def _atualizar_trailing(self, p: Posicao, preco: float) -> None:
        """Conduz o stop por subníveis da Fimathe (Rompimento Trailingstop Lite-3.8):
        a cada degrau de `LITE38_FATOR_DEGRAU * amplitude` a favor, o stop sobe um degrau atrás."""
        if p.amplitude <= 0:
            return
        compra = p.side == "buy"
        base = p.extremo or p.entrada
        p.extremo = max(base, preco) if compra else min(base, preco)

        degrau = p.amplitude * LITE38_FATOR_DEGRAU
        avanco = (p.extremo - p.entrada) if compra else (p.entrada - p.extremo)
        passos = int(avanco // degrau) if degrau > 0 else 0
        if passos >= 2:
            mult = (passos - 1) * LITE38_FATOR_DEGRAU
            novo = p.entrada + mult * p.amplitude if compra else p.entrada - mult * p.amplitude
            if (novo > p.stop) if compra else (novo < p.stop):
                try:
                    # Cria o novo stop ANTES de cancelar o antigo: a posição nunca fica sem proteção.
                    nova = self.exchange.criar_stop_loss(p.symbol, p.side, p.amount, novo)
                except Exception as exc:
                    log.warning("Falha ao mover o stop para %.8g (mantendo o atual): %s", novo, exc)
                else:
                    if p.stop_order_id:
                        self.exchange.cancelar_ordem(p.stop_order_id, p.symbol, {"trigger": True})
                    self.db.registrar_ordem(nova, "stop_trailing")
                    log.info("Trailing stop: %.8g -> %.8g (%d degraus a favor)", p.stop, novo, passos)
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
        log.info("Bot Fimathe iniciado | estrategia=%s | %s %s | modo=%s | stop=%s",
                 self.perfil, self.cfg.symbol, self.cfg.timeframe, self.exchange.modo, self.estrategia.modo_stop)
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
