"""Matriz final: políticas de saída x tipo de ordem, NET de taxas.

Mecânica LIMIT_RETESTE (conceito Apex/Fimathe):
  - Sinal no fechamento do candle i (rompimento confirmado);
  - ordem LIMIT MAKER no próprio nível rompido (parsed do motivo do Sinal);
  - fill só se o preço volta para o nível em até PRAZO=4 candles; senão cancela;
  - conservador: no candle do fill, stop checado antes do alvo;
  - taxa MARKET: entrada taker 0.06% + saída taker 0.06% = 0.12% round-trip;
  - taxa LIMIT_RETESTE: entrada maker 0.02% + saída taker 0.06% = 0.08% RT.

Filtro KER 0.25 ATIVO em todos os casos. Pontuação em R líquido
(pnl_R_liquido = pnl_R_bruto - taxa_rt / distância_do_stop_em_%).

Uso (VM Oracle):
  ~/cryptobot/.venv/bin/python backtests/validacao_fees_ordens.py
"""

import os
import re
import sys
import json
import argparse
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "core"))
sys.path.insert(0, str(BASE_DIR / "strategies"))

from backtests.apex_dinamics.backtest_kucoin_7dias_top4_10x import fetch_kucoin_futures_5m_7dias  # noqa: E402
from strategies.strategy import EstrategiaFimathe  # noqa: E402
from strategies.base import Direcao  # noqa: E402

DATA_DIR = BASE_DIR / "data"
PRAZO_RETESTE = 4

POLITICAS = {
    "P90": {"dist_pct": 2.052, "trig": 0.859, "espaco_mult": 1.0},
    "HIBRIDA": {"dist_pct": 0.600, "trig": 0.859, "espaco_mult": 0.75},
}

RE_NIVEL = re.compile(r"\(([\d.]+)\)")


def _parse_nivel(motivo: str) -> Optional[float]:
    m = RE_NIVEL.search(motivo or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _simulo_exit(velas: pd.DataFrame, compr: bool, entry: float, stop_inicial: float,
                 alvo: float, trig: float, espaco: float, ts_start: float) -> dict:
    """Caminho de saída comum: stop (com trailing opcional) x alvo em velas 5m."""
    risk = abs(entry - stop_inicial)
    if risk <= 0:
        return {"exit_ts": None, "pnl_R_gross": 0.0, "exit_reason": "RISK_ZERO"}
    stop = stop_inicial
    extremo = entry
    atrail = False
    janela = velas[velas.index >= pd.Timestamp(int(ts_start) // 300 * 300, unit="s", tz="utc")]
    if janela.empty:
        return {"exit_ts": None, "pnl_R_gross": 0.0, "exit_reason": "SEM_VELAS"}
    for ts, row in janela.iterrows():
        hi, lo = float(row["high"]), float(row["low"])
        if (compr and lo <= stop) or (not compr and hi >= stop):
            return {"exit_reason": "TRAILING_STOP" if atrail else "STOP_LOSS",
                    "exit_ts": float(ts.timestamp()),
                    "pnl_R_gross": ((stop - entry) if compr else (entry - stop)) / risk}
        if (compr and hi >= alvo) or (not compr and lo <= alvo):
            return {"exit_reason": "TAKE_PROFIT", "exit_ts": float(ts.timestamp()),
                    "pnl_R_gross": ((alvo - entry) if compr else (entry - alvo)) / risk}
        novo_ext = max(extremo, hi) if compr else min(extremo, lo)
        if not atrail:
            ex = (novo_ext - entry) if compr else (entry - novo_ext)
            if ex >= trig:
                atrail = True
        extremo = novo_ext
        if atrail:
            cand = extremo - espaco if compr else extremo + espaco
            stop = max(stop, cand) if compr else min(stop, cand)
    close = float(janela["close"].iloc[-1])
    return {"exit_reason": "FIM_DADOS", "exit_ts": None,
            "pnl_R_gross": ((close - entry) if compr else (entry - close)) / risk}


def simular_evento(velas: pd.DataFrame, ev: dict, pol: dict, tipo: str) -> Optional[dict]:
    """MARKET: entrada no fechamento do sinal. LIMIT_RETESTE: entrada no reteste do nível."""
    compra = ev["direcao"] == Direcao.COMPRA
    compr = bool(compra)
    alvo = ev["alvo"]

    if tipo == "MARKET":
        entry = ev["entry"]
    else:
        nivel = _parse_nivel(ev.get("motivo"))
        if nivel is None:
            return None
        idx0 = int(velas.index.searchsorted(pd.Timestamp(int(ev["ts"]), unit="s", tz="UTC")))
        fill_ts = None
        for k in range(idx0, min(idx0 + PRAZO_RETESTE + 1, len(velas))):
            hi, lo = float(velas["high"].iloc[k]), float(velas["low"].iloc[k])
            if (compr and lo <= nivel) or (not compr and hi >= nivel):
                fill_ts = float(velas.index[k].timestamp())
                break
        if fill_ts is None:
            return None  # sem reteste no prazo: ordem cancelada
        entry = nivel
        valor_dist = pol["dist_pct"] / 100.0
        stop0 = entry - entry * valor_dist if compr else entry + entry * valor_dist
    if tipo == "MARKET":
        valor_dist = pol["dist_pct"] / 100.0
        stop0 = entry - entry * valor_dist if compr else entry + entry * valor_dist

    trig = entry * pol["trig"] / 100.0
    espaco = entry * valor_dist * pol["espaco_mult"]
    return _simulo_exit(velas, compr, entry, stop0, alvo, trig, espaco, ev["ts"] if tipo == "MARKET" else (fill_ts if fill_ts else ev["ts"]))


def simular_live(velas: pd.DataFrame, ev: dict) -> Optional[dict]:
    """LIVE: stop estrutural do sinal (fora_caixinha), sem trailing, alvo 1."""
    compra = ev["direcao"] == Direcao.COMPRA
    entry, stop, alvo = ev["entry"], ev["stop"], ev["alvo"]
    risk = abs(entry - stop)
    if risk <= 0 or not alvo:
        return None
    janela = velas[velas.index >= pd.Timestamp(int(ev["ts"]) // 300 * 300, unit="s", tz="utc")]
    if janela.empty:
        return None
    for ts, row in janela.iterrows():
        hi, lo = float(row["high"]), float(row["low"])
        if (compra and lo <= stop) or (not compra and hi >= stop):
            return {"exit_reason": "STOP_LOSS", "exit_ts": float(ts.timestamp()),
                    "pnl_R_gross": ((stop - entry) if compra else (entry - stop)) / risk}
        if (compra and hi >= alvo) or (not compra and lo <= alvo):
            return {"exit_reason": "TAKE_PROFIT", "exit_ts": float(ts.timestamp()),
                    "pnl_R_gross": ((alvo - entry) if compra else (entry - alvo)) / risk}
    close = float(janela["close"].iloc[-1])
    return {"exit_reason": "FIM_DADOS", "exit_ts": None,
            "pnl_R_gross": ((close - entry) if compra else (entry - close)) / risk}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=45)
    ap.add_argument("--pares", type=str, default="SOL/USDT:USDT,BTC/USDT:USDT,ETH/USDT:USDT")
    ap.add_argument("--ker-minimo", type=float, default=0.25)
    args = ap.parse_args()
    pares = [p.strip() for p in args.pares.split(",") if p.strip()]

    print("=" * 100)
    print(f"💰 MATRIZ NET DE TAXAS — {args.dias} dias | KER {args.ker_minimo:.2f} ATIVO | {', '.join(pares)}")
    print("   MARKET 0.12% RT | LIMIT_RETESTE 0.08% RT | pontuação em R líquido")
    print("=" * 100)

    strat = EstrategiaFimathe(
        janela_pivo=4, qtd_niveis=4, folga_stop=0.10,
        amplitude_min_pct=0.30, modo_stop="fora_caixinha", romper_no_canal=True,
        ker_filtro_ativo=True, ker_minimo=args.ker_minimo, ker_periodo=12,
    )

    COMBOS = [
        ("LIVE_MKT", "LIVE", "MARKET", 0.0012),
        ("P90_MKT", "P90", "MARKET", 0.0012),
        ("HIBRIDA_MKT", "HIBRIDA", "MARKET", 0.0012),
        ("P90_LMT", "P90", "LIMIT_RETESTE", 0.0008),
        ("HIBRIDA_LMT", "HIBRIDA", "LIMIT_RETESTE", 0.0008),
    ]

    por_par = {}
    for p in pares:
        print(f"\n⏳ {p}: baixando velas...")
        df = fetch_kucoin_futures_5m_7dias(p, target_candles=args.dias * 288)
        print(f"   ✔ {len(df)} velas ({df.index[0]} → {df.index[-1]})")

        eventos = []
        n = len(df)
        i = 40
        while i < n - 2:
            try:
                sinal = strat.avaliar(df.iloc[max(0, i - 400): i + 1])
            except Exception:
                sinal = None
            if sinal is not None:
                alvos = list(sinal.alvos or [])
                eventos.append({
                    "ts": float(df.index[i].timestamp()) + 300.0,
                    "entry": float(sinal.preco_entrada),
                    "direcao": sinal.direcao,
                    "stop": float(sinal.stop_loss or 0.0),
                    "alvo": float(alvos[0] if alvos else sinal.preco_entrada),
                    "motivo": sinal.motivo,
                })
            i += 1
        print(f"   🎯 {len(eventos)} sinais")

        par_res = {}
        for nome, pol_name, tipo, fee in COMBOS:
            trades = []
            fim = -1.0
            for ev in eventos:
                if ev["ts"] <= fim:
                    continue
                if pol_name == "LIVE":
                    sim = simular_live(df, ev)
                    dist_pct = abs(ev["entry"] - ev["stop"]) / ev["entry"]
                else:
                    sim = simular_evento(df, ev, POLITICAS[pol_name], tipo)
                    dist_pct = POLITICAS[pol_name]["dist_pct"] / 100.0
                if sim is None:
                    continue
                if sim.get("exit_ts"):
                    fim = max(fim, sim["exit_ts"])
                if dist_pct <= 0:
                    continue
                trades.append(sim["pnl_R_gross"] - fee / dist_pct)
            wins = sum(1 for r in trades if r > 0)
            par_res[nome] = {
                "trades": len(trades),
                "winrate_pct": round(wins / len(trades) * 100, 1) if trades else 0,
                "total_R_net": round(sum(trades), 2) if trades else 0,
                "avg_R_net": round(sum(trades) / len(trades), 3) if trades else 0,
            }
        por_par[p] = par_res

    agregado = {}
    for nome, _, _, _ in COMBOS:
        tot = sum(por_par[p].get(nome, {}).get("total_R_net", 0.0) for p in pares)
        t = sum(por_par[p].get(nome, {}).get("trades", 0) for p in pares)
        agregado[nome] = {"trades": t, "total_R_net": round(tot, 2),
                          "avg_R_net": round(tot / t, 3) if t else 0}

    print("\n" + "=" * 100)
    print("📊 MATRIZ AGREGADA (R líquido de taxas)")
    print(json.dumps(agregado, ensure_ascii=False, indent=2))
    print("\nPor par:")
    print(json.dumps(por_par, ensure_ascii=False, indent=2))

    out_path = DATA_DIR / "validacao_fees_ordens.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump({"por_par": por_par, "agregado": agregado}, fh, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 Salvo em {out_path}")


if __name__ == "__main__":
    main()
