# -*- coding: utf-8 -*-
"""Smoke test dos novos mecanismos de execucao (sem rede, sem bot.db real).

Valida na versao da VM (Python 3.9):
  1. Parse do nivel do rompimento a partir do motivo do Sinal.
  2. Stop P90 (dist % fixa) e pesos de trailing (trig/espaco).
  3. Trailing P90: ativacao no gatilho e subida do stop pelo extremo.
  4. Politica LIVE: stop estrutural, sem trailing P90.
  5. Ciclo de ordem de reteste: colocacao, cancelamento por prazo, fill.
Rodar: .venv/bin/python /tmp/smoke_exec.py
"""
import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, "/home/opc/cryptobot")
os.chdir("/home/opc/cryptobot")

import pandas as pd
import main as m


class FakeDB:
    def registrar_ordem(self, ordem, papel, strategy="apex"):  # noqa: ARG002
        pass

    def registrar_sinal(self, *a, **k):
        pass

    def salvar_estado(self, chave, valor):
        pass


class FakeExchange:
    def __init__(self, velas):
        self.velas = velas
        self.stops = []
        self.canceladas = []
        self.ordens = 0
        self.paper = None

    def buscar_candles(self, symbol, timeframe, limite=None):
        return self.velas

    def criar_stop_loss(self, symbol, side, amount, preco_stop):
        self.stops.append(preco_stop)
        self.ordens += 1
        return {"id": "STOP-%d" % self.ordens}

    def cancelar_ordem(self, order_id, symbol, params=None):
        self.canceladas.append(order_id)

    def criar_ordem(self, symbol, side, amount, tipo, price, params):
        self.ordens += 1
        return {"id": "ORD-%d" % self.ordens, "status": "open", "price": price, "type": tipo}

    def buscar_ordem(self, order_id, symbol, params=None):
        return {"id": order_id, "status": "closed", "average": None, "price": 103.308}


def bot_de_teste(politica="P90", tipo="LIMIT_RETESTE"):
    bot = object.__new__(m.Bot)
    bot.tipo_ordem = tipo
    bot.prazo_reteste = 4
    bot.politica_saida = politica
    bot.p90_stop_pct = 2.052
    bot.p90_trigger_pct = 0.859
    bot.p90_espaco_mult = 1.0
    bot.cfg = m.Config(symbol="SOL/USDT:USDT", quote="USDT", timeframe="5m",
                       candle_limit=300, loop_interval=60)
    bot.db = FakeDB()
    bot.posicao = None
    class EstratStub:
        name = "apex_dinamics"
        strategy_id = "STRAT_APEX_DINAMICS_V1"
    bot.estrategia = EstratStub()
    return bot


class SinalFalso:
    direcao = m.Direcao.COMPRA
    preco_entrada = 100.0
    stop_loss = 99.0
    alvos = [101.5]
    motivo = "Rompimento comprador do nivel superior (103.308)"
    grade = {"amplitude": 0.8}


def teste_stop_e_trailing():
    bot = bot_de_teste(politica="P90")
    s = SinalFalso()
    stop = bot._stop_entrada(s, 100.0)          # entrada MARKET @ 100
    trig, espaco = bot._trailing_pesos(s, 100.0)
    assert abs(stop - 97.948) < 1e-9, "stop P90: %s" % stop
    assert abs(trig - 0.859) < 1e-9, "trig: %s" % trig
    assert abs(espaco - 2.052) < 1e-9, "espaco: %s" % espaco

    # LIVE: usa stop estrutural, pesos nulos
    bot_l = bot_de_teste(politica="LIVE")
    assert bot_l._stop_entrada(s, 100.0) == 99.0 and bot_l._trailing_pesos(s, 100.0) == (0.0, 0.0)

    # ciclo do trailing P90
    ex = FakeExchange(pd.DataFrame())
    bot.exchange = ex
    p = m.Posicao(symbol="SOL/USDT:USDT", side="buy", amount=10.0, quantidade=10.0,
                  entrada=100.0, stop=97.948, alvo=999.0, stop_order_id="STOP-0",
                  aberta_em="", amplitude=0.8, extremo=100.0, trig=trig, espaco=espaco)
    bot._atualizar_trailing(p, 100.5)              # avanco 0.50 < trig 0.859: nada
    assert p.stop == 97.948 and not p.trailing_ativo
    bot._atualizar_trailing(p, 100.9)              # gatilho atingido; stop sobe p/ 98.848
    assert p.trailing_ativo and abs(p.stop - 98.848) < 1e-9
    bot._atualizar_trailing(p, 103.0)              # extremo 103.0 -> stop 100.948
    assert abs(p.stop - 100.948) < 1e-9
    bot._atualizar_trailing(p, 102.0)              # recuo: stop ja mais alto, nao desce
    assert abs(p.stop - 100.948) < 1e-9
    assert len(ex.stops) == 2, "stops criados: %d" % len(ex.stops)
    print("OK 1: stop P90 + trailing (gatilho/subida/monotonicidade)")


def teste_reteste_cancelamento():
    # velas fechadas apos a colocacao: NENHUMA toca o nivel 103.308
    def vela(hi, lo):
        return {"high": hi, "low": lo, "close": hi, "open": hi}

    idx = pd.date_range("2026-10-31 12:05", periods=6, freq="5min", tz="UTC")
    velas = pd.DataFrame([vela(104.5, 103.5), vela(104.4, 103.4), vela(104.3, 103.45),
                          vela(104.2, 103.44), vela(104.1, 103.43), vela(104.0, 103.42)], index=idx)
    orig_time = m.time.time
    try:
        now = pd.Timestamp("2026-10-31 12:06", tz="UTC").timestamp()  # dentro do candle de 12:05
        m.time.time = lambda: now
        bot = bot_de_teste()
        bot.exchange = FakeExchange(velas)
        bot._colocar_entrada_reteste(SinalFalso(), qtd=10.0, amount=10.0, params={"leverage": 5})
    finally:
        m.time.time = orig_time
    assert bot.pendente is not None, "pendente deveria existir"
    nivel = bot.pendente["nivel"]
    assert abs(nivel - 103.308) < 1e-9, "nivel parseado: %s" % nivel
    assert bot.pendente["order_id"], "live: ordem limit real deveria ter id"
    bot._gerenciar_pendente()
    assert bot.pendente is None, "pendente deveria ter sido cancelado pelo prazo"
    assert len(bot.exchange.canceladas) == 1, "ordem limit deveria ter sido cancelada"
    print("OK 2: parse do nivel + cancelamento por prazo (4 candles, sem reteste)")


def teste_reteste_fill():
    # velas fechadas apos a colocacao: reteste acontece na 3a vela (12:15)
    def vela(hi, lo):
        return {"high": hi, "low": lo, "close": hi, "open": hi}

    idx = pd.date_range("2026-10-31 12:05", periods=5, freq="5min", tz="UTC")
    velas = pd.DataFrame([vela(104.9, 102.1), vela(104.8, 102.2), vela(103.308, 101.9),
                          vela(104.0, 102.3), vela(104.1, 102.4)], index=idx)
    orig_time = m.time.time
    try:
        now = pd.Timestamp("2026-10-31 12:06", tz="UTC").timestamp()
        m.time.time = lambda: now
        bot = bot_de_teste()
        bot.exchange = FakeExchange(velas)
        bot._colocar_entrada_reteste(SinalFalso(), qtd=10.0, amount=10.0, params={"leverage": 5})
    finally:
        m.time.time = orig_time
    bot._gerenciar_pendente()
    assert bot.pendente is None, "fill consumiu o pendente"
    p = bot.posicao
    assert p is not None and p.side == "buy"
    assert abs(p.entrada - 103.308) < 1e-9, "entrada no nivel: %s" % p.entrada
    assert abs(p.stop - (103.308 * (1 - 0.02052))) < 1e-6, "stop P90 sobre o fill: %s" % p.stop
    assert abs(p.trig - 103.308 * 0.859 / 100.0) < 1e-9
    assert abs(p.alvo - 101.5) < 1e-9
    assert p.stop_order_id and str(p.stop_order_id).startswith("STOP"), "stop order criado"
    print("OK 3: fill do reteste -> posicao com stop P90 sobre o preco efetivo")


def teste_paper_sem_ordem_real():
    velas = pd.DataFrame([{"high": 105.0, "low": 103.0, "close": 104.0, "open": 104.0},
                          {"high": 104.0, "low": 103.308, "close": 104.0, "open": 104.0}],
                         index=pd.date_range("2026-10-31 12:05", periods=2, freq="5min", tz="UTC"))
    orig_time = m.time.time
    try:
        now = pd.Timestamp("2026-10-31 12:06", tz="UTC").timestamp()
        m.time.time = lambda: now
        ex = FakeExchange(velas)
        ex.paper = object()  # simula modo paper
        bot = bot_de_teste()
        bot.exchange = ex
        bot._colocar_entrada_reteste(SinalFalso(), qtd=10.0, amount=10.0, params={})
    finally:
        m.time.time = orig_time
    assert bot.pendente is not None and not bot.pendente.get("order_id"), "paper: sem ordem real"
    bot._gerenciar_pendente()
    assert bot.posicao is not None and abs(bot.posicao.entrada - 103.308) < 1e-9
    print("OK 4: modo paper executa o reteste virtualmente (sem ordem na exchange)")


if __name__ == "__main__":
    teste_stop_e_trailing()
    teste_reteste_cancelamento()
    teste_reteste_fill()
    teste_paper_sem_ordem_real()
    print("\nSMOKE OK: todos os testes passaram (py %s)" % sys.version.split()[0])
