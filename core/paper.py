"""Simulador nativo de corretora (paper trading).

A KuCoin não disponibiliza ambiente sandbox utilizável pelo ccxt (nem spot,
nem futures). Este módulo supre essa lacuna: o bot continua lendo preços
REAIS da KuCoin, mas as ordens são executadas localmente, com:

  * saldo e margem simulados;
  * taxa de corretagem e slippage configuráveis;
  * ordens stop (stopLossPrice) armazenadas e disparadas pelo preço real;
  * estado persistido em JSON (sobrevive a reinícios).

Para zerar a simulação basta apagar o arquivo de estado.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

EPS = 1e-12


class SimuladorPaper:
    def __init__(
        self,
        caminho_estado: Path,
        capital_inicial: float,
        moeda_quote: str,
        taxa_pct: float = 0.1,
        slippage_pct: float = 0.05,
        permite_short: bool = False,
    ) -> None:
        self.caminho = caminho_estado
        self.moeda_quote = moeda_quote
        self.taxa = taxa_pct / 100
        self.slippage = slippage_pct / 100
        self.permite_short = permite_short

        self.estado: dict[str, Any] = self._carregar() or {
            "capital_inicial": capital_inicial,
            "saldo": capital_inicial,   # caixa realizado (PnL e taxas já aplicados)
            "posicoes": {},             # symbol -> {lado, quantidade, entrada, margem}
            "ordens": {},               # id -> ordem
            "historico": [],            # execuções
        }
        self._salvar()
        log.info("Simulador paper ativo | saldo=%.4f %s | taxa=%.3f%% slippage=%.3f%% | estado=%s",
                 self.estado["saldo"], moeda_quote, taxa_pct, slippage_pct, self.caminho)

    # ------------------------------------------------------------------ #
    # Persistência
    # ------------------------------------------------------------------ #
    def _carregar(self) -> dict | None:
        if self.caminho.exists():
            with open(self.caminho, encoding="utf-8") as f:
                return json.load(f)
        return None

    def _salvar(self) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.caminho.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.estado, f, indent=2, default=str)
        os.replace(tmp, self.caminho)  # escrita atômica

    @staticmethod
    def _agora() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ #
    # Consultas
    # ------------------------------------------------------------------ #
    def saldo_livre(self, moeda: str) -> float:
        if moeda != self.moeda_quote:
            return 0.0
        comprometido = sum(p["margem"] for p in self.estado["posicoes"].values())
        return self.estado["saldo"] - comprometido

    def posicao(self, symbol: str) -> dict | None:
        return self.estado["posicoes"].get(symbol)

    def buscar_ordem(self, order_id: str) -> dict:
        ordem = self.estado["ordens"].get(order_id)
        if ordem is None:
            raise KeyError(f"Ordem paper {order_id} não encontrada")
        return ordem

    # ------------------------------------------------------------------ #
    # Ordens
    # ------------------------------------------------------------------ #
    def criar_ordem(
        self,
        symbol: str,
        side: str,
        amount: float,
        tamanho_contrato: float,
        preco_mercado: float,
        params: dict,
        client_oid: str,
    ) -> dict:
        ordem: dict[str, Any] = {
            "id": f"paper-{uuid.uuid4().hex[:20]}",
            "clientOrderId": client_oid,
            "symbol": symbol,
            "side": side,
            "type": "market",
            "amount": amount,
            "price": None,
            "contractSize": tamanho_contrato,
            "leverage": int(params.get("leverage", 1) or 1),
            "reduceOnly": bool(params.get("reduceOnly", False)),
            "status": "open",
            "timestamp": self._agora(),
            "paper": True,
        }

        stop = params.get("stopLossPrice")
        if stop is not None:
            ordem["type"] = "stop_market"
            ordem["stopLossPrice"] = float(stop)
            ordem["reduceOnly"] = True
            self.estado["ordens"][ordem["id"]] = ordem
            self._salvar()
            log.info("[PAPER] Stop registrado: %s %s @ %s", side, symbol, stop)
            return dict(ordem)

        self.estado["ordens"][ordem["id"]] = ordem
        self._executar(ordem, preco_mercado)
        return dict(ordem)

    def cancelar_ordem(self, order_id: str) -> None:
        ordem = self.estado["ordens"].get(order_id)
        if ordem and ordem["status"] == "open":
            ordem["status"] = "canceled"
            self._salvar()
            log.info("[PAPER] Ordem %s cancelada", order_id)

    def processar_preco(self, symbol: str, preco: float) -> list[dict]:
        """Dispara stops cujo gatilho foi atingido. Executa ao preço atual
        (pior ou igual ao gatilho), simulando gap de forma conservadora."""
        executadas = []
        for ordem in list(self.estado["ordens"].values()):
            if ordem["status"] != "open" or ordem["type"] != "stop_market" or ordem["symbol"] != symbol:
                continue
            gatilho = ordem["stopLossPrice"]
            disparou = preco <= gatilho if ordem["side"] == "sell" else preco >= gatilho
            if disparou:
                log.info("[PAPER] Stop disparado %s @ gatilho %s (preço %s)", symbol, gatilho, preco)
                self._executar(ordem, preco)
                executadas.append(dict(ordem))
        return executadas

    # ------------------------------------------------------------------ #
    # Motor de execução
    # ------------------------------------------------------------------ #
    def _rejeitar(self, ordem: dict, motivo: str) -> None:
        ordem.update(status="rejected", motivo=motivo)
        self._salvar()
        log.warning("[PAPER] Ordem %s rejeitada: %s", ordem["id"], motivo)

    def _executar(self, ordem: dict, preco_ref: float) -> None:
        symbol, side = ordem["symbol"], ordem["side"]
        preco = preco_ref * (1 + self.slippage) if side == "buy" else preco_ref * (1 - self.slippage)
        qtd = ordem["amount"] * ordem["contractSize"]
        taxa = qtd * preco * self.taxa
        pos = self.estado["posicoes"].get(symbol)
        pnl = 0.0

        if pos is None or pos["lado"] == side:
            # --- abertura / aumento de posição ---
            if ordem["reduceOnly"]:
                return self._rejeitar(ordem, "reduceOnly sem posição para reduzir")
            if side == "sell" and pos is None and not self.permite_short:
                return self._rejeitar(ordem, "venda a descoberto não permitida neste mercado")
            alav = max(1, ordem["leverage"])
            margem_nova = qtd * preco / alav
            if margem_nova + taxa > self.saldo_livre(self.moeda_quote) + EPS:
                return self._rejeitar(ordem, "saldo insuficiente")
            qtd_total = (pos["quantidade"] if pos else 0.0) + qtd
            entrada = ((pos["quantidade"] * pos["entrada"]) if pos else 0.0) + qtd * preco
            self.estado["posicoes"][symbol] = {
                "lado": side,
                "quantidade": qtd_total,
                "entrada": entrada / qtd_total,
                "margem": (pos["margem"] if pos else 0.0) + margem_nova,
            }
        else:
            # --- redução / fechamento ---
            fechar = min(qtd, pos["quantidade"])
            if qtd > pos["quantidade"] + EPS:
                log.warning("[PAPER] Ordem maior que a posição; fechando apenas %s", fechar)
            direcao = 1 if pos["lado"] == "buy" else -1
            pnl = (preco - pos["entrada"]) * fechar * direcao
            taxa = fechar * preco * self.taxa
            restante = pos["quantidade"] - fechar
            if restante <= EPS:
                del self.estado["posicoes"][symbol]
            else:
                pos["margem"] *= restante / pos["quantidade"]
                pos["quantidade"] = restante
            qtd = fechar

        self.estado["saldo"] += pnl - taxa
        ordem.update(
            status="closed",
            filled=ordem["amount"],
            average=preco,
            cost=qtd * preco,
            fee={"cost": taxa, "currency": self.moeda_quote},
            pnl=pnl,
            executada_em=self._agora(),
        )
        self.estado["historico"].append(
            {"ts": ordem["executada_em"], "id": ordem["id"], "symbol": symbol, "side": side,
             "quantidade": qtd, "preco": preco, "taxa": taxa, "pnl": pnl,
             "saldo": self.estado["saldo"]}
        )
        self._salvar()
        log.info("[PAPER] Executada %s %s qtd=%.8g @ %.8g | taxa=%.4f pnl=%.4f | saldo=%.4f",
                 side, symbol, qtd, preco, taxa, pnl, self.estado["saldo"])
