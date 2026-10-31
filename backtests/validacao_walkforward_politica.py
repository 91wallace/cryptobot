"""Validação Walk-Forward das políticas de saída (out-of-sample).

Diferente da simulação por trades fixos, aqui AS ENTRADAS SÃO GERADAS pela
própria estratégia Apex/Fimathe (`strategies/strategy.py::EstrategiaFimathe`)
sobre velas 5m de múltiplos ativos e ~45 dias, imitando o bot live:
- sinal no fechamento do candle i -> entrada no fechamento (preço do sinal);
- enquanto em posição, novas entradas são ignoradas;
- alvo = alvos[0] do sinal.

Políticas comparadas (todas pontuadas em R = pnl / distância do stop inicial,
ou seja, risco normalizado de 1R por trade):
  LIVE_EST       stop estrutural do sinal (fora_caixinha 1:1 com alvo_1), sem trailing
  P90            stop 2.052% + trailing ativando em +0.859% (espaço = stop)
  HIBRIDA        stop 0.60%  + trailing ativando em +0.859% (espaço = 0.75x stop)
  HIBRIDA_EST    stop estrutural do sinal + trailing em +0.859% (espaço 0.75x)

Uso (VM Oracle):
  ~/cryptobot/.venv/bin/python backtests/validacao_walkforward_politica.py \
      [--dias 45] [--pares SOL/USDT:USDT,BTC/USDT:USDT,ETH/USDT:USDT]
"""

import os
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

# ---------------------------------------------------------------------------
# Políticas
# ---------------------------------------------------------------------------

POLITICAS = {
    "LIVE_EST":      {"tipo": "estrutural", "trig": None,      "espaco": None},
    "P90":           {"tipo": "pct", "dist_pct": 2.052, "trig": 0.859, "espaco": 2.052},
    "HIBRIDA":       {"tipo": "pct", "dist_pct": 0.600, "trig": 0.859, "espaco_mult": 0.75},
    "HIBRIDA_EST":   {"tipo": "estrutural", "trig": 0.859, "espaco_mult": 0.75},
}


def simular_sinal(velas: pd.DataFrame, ev: dict, pol: dict) -> Optional[dict]:
    """Simula um trade a partir do evento de sinal com a política dada."""
    entry = ev["entry"]
    compra = ev["direcao"] == Direcao.COMPRA

    if pol["tipo"] == "estrutural":
        stop = ev["stop"]
    else:
        stop = entry - entry * pol["dist_pct"] / 100.0 if compra else entry + entry * pol["dist_pct"] / 100.0
    stop0 = stop
    risk = abs(entry - stop0)
    if risk <= 0:
        return None

    trig = entry * pol["trig"] / 100.0 if pol.get("trig") else None
    espaco = entry * pol["espaco"] / 100.0 if pol.get("espaco") else (
        entry * pol["espaco_mult"] * pol.get("dist_pct", 0.6) / 100.0 if pol.get("espaco_mult") and pol.get("dist_pct")
        else risk * pol.get("espaco_mult", 0.75) if pol.get("espaco_mult") else None
    )

    alvo = ev["alvo"]
    t0 = pd.Timestamp(int(ev["ts"]) // 300 * 300, unit="s", tz="utc")
    janela = velas[velas.index >= t0]
    if len(janela) == 0:
        return None

    atrail = False
    extremo = entry
    for ts, row in janela.iterrows():
        hi, lo = float(row["high"]), float(row["low"])
        if (compra and lo <= stop) or (not compra and hi >= stop):
            motivo = "TRAILING_STOP" if atrail else "STOP_LOSS"
            pnl_R = ((stop - entry) if compra else (entry - stop)) / risk
            return {"exit_price": stop, "exit_reason": motivo, "exit_ts": float(ts.timestamp()), "pnl_R": pnl_R}
        if (compra and hi >= alvo) or (not compra and lo <= alvo):
            pnl_R = ((alvo - entry) if compra else (entry - alvo)) / risk
            return {"exit_price": alvo, "exit_reason": "TAKE_PROFIT", "exit_ts": float(ts.timestamp()), "pnl_R": pnl_R}

        novo_ext = max(extremo, hi) if compra else min(extremo, lo)
        if trig and not atrail:
            ex = (novo_ext - entry) if compra else (entry - novo_ext)
            if ex >= trig:
                atrail = True
        extremo = novo_ext
        if atrail and espaco:
            cand = extremo - espaco if compra else extremo + espaco
            stop = max(stop, cand) if compra else min(stop, cand)

    close = float(janela["close"].iloc[-1])
    pnl_R = ((close - entry) if compra else (entry - close)) / risk
    return {"exit_price": close, "exit_reason": "FIM_DADOS", "exit_ts": None, "pnl_R": pnl_R}


# ---------------------------------------------------------------------------
# Geração de sinais
# ---------------------------------------------------------------------------

def gerar_sinais(df: pd.DataFrame, strat: EstrategiaFimathe, warmup: int = 40) -> list:
    eventos = []
    n = len(df)
    i = warmup
    while i < n - 2:
        janela_df = df.iloc[max(0, i - 400): i + 1]
        try:
            sinal = strat.avaliar(janela_df)
        except Exception:
            sinal = None
        if sinal is None:
            i += 1
            continue
        ts_ev = float(df.index[i].timestamp())
        direcao = sinal.direcao
        alvos = list(sinal.alvos or [])
        alvo1 = alvos[0] if alvos else sinal.preco_entrada
        eventos.append({
            "ts": ts_ev + 300.0,           # entrada no candle seguinte
            "entry": float(sinal.preco_entrada),
            "direcao": direcao,
            "stop": float(sinal.stop_loss or 0.0),
            "alvo": float(alvo1),
            "motivo": sinal.motivo,
        })
        i += 1
    return eventos


# ---------------------------------------------------------------------------
# Rodada por par
# ---------------------------------------------------------------------------

def rodar_par(symbol: str, dias: int, strat: EstrategiaFimathe) -> dict:
    print(f"\n⏳ Baixando candles de {symbol} ({dias} dias)...")
    df = fetch_kucoin_futures_5m_7dias(symbol, target_candles=dias * 288)
    print(f"   ✔ {len(df)} velas ({df.index[0]} → {df.index[-1]})")

    eventos = gerar_sinais(df, strat)
    print(f"🎯 Sinais gerados: {len(eventos)}")
    if not eventos:
        return {"symbol": symbol, "policies": {}}

    pol_out = {}
    for nome, pol in POLITICAS.items():
        pona = None
        trades_r = []
        for ev in eventos:
            if pona is not None and ev["ts"] < pona["exit_ts"]:
                continue  # em posição
            sim = simular_sinal(df, ev, pol)
            if sim is None or sim.get("pnl_R") is None:
                continue
            if pona is not None and ev["ts"] < pona["exit_ts"]:
                continue
            trades_r.append({"ts": ev["ts"], "pnl_R": sim["pnl_R"], "motivo": sim["exit_reason"],
                             "direcao": str(ev["direcao"].value), "exit_ts": sim["exit_ts"]})
            pona = {"exit_ts": sim["exit_ts"] or ev["ts"]}
        wins = sum(1 for t in trades_r if t["pnl_R"] > 0)
        total_r = sum(t["pnl_R"] for t in trades_r)
        perdas = [abs(min(0.0, t["pnl_R"])) for t in trades_r]
        pol_out[nome] = {
            "trades": len(trades_r),
            "wins": wins,
            "winrate_pct": round(wins / len(trades_r) * 100, 1) if trades_r else 0,
            "total_R": round(total_r, 2),
            "avg_R": round(total_r / len(trades_r), 3) if trades_r else 0,
            "avg_loss_R": round(-sum(perdas) / max(len([t for t in trades_r if t["pnl_R"] <= 0]), 1), 3),
        }
    return {"symbol": symbol, "policies": pol_out, "n_sinais": len(eventos)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=45)
    ap.add_argument("--pares", type=str, default="SOL/USDT:USDT,BTC/USDT:USDT,ETH/USDT:USDT")
    ap.add_argument("--ker", action="store_true", help="ativa filtro anti-choppy (Kaufman Efficiency Ratio)")
    ap.add_argument("--ker-minimo", type=float, default=0.25)
    ap.add_argument("--ker-periodo", type=int, default=12)
    ap.add_argument("--tag", type=str, default="", help="sufixo do arquivo de saída")
    args = ap.parse_args()
    pares = [p.strip() for p in args.pares.split(",") if p.strip()]

    print("=" * 100)
    print(f"🔬 VALIDAÇÃO WALK-FORWARD — {args.dias} dias | pares: {', '.join(pares)}")
    print(f"   KER: {'ATIVO (min %.2f, período %d)' % (args.ker_minimo, args.ker_periodo) if args.ker else 'INATIVO'}")
    print("   Políticas: LIVE_EST | P90 | HIBRIDA | HIBRIDA_EST (pontos em R, risco 1R por trade)")
    print("=" * 100)

    strat = EstrategiaFimathe(
        janela_pivo=4, qtd_niveis=4, folga_stop=0.10,
        amplitude_min_pct=0.30, modo_stop="fora_caixinha", romper_no_canal=True,
        ker_filtro_ativo=args.ker, ker_minimo=args.ker_minimo, ker_periodo=args.ker_periodo,
    )

    resultados = [rodar_par(p, args.dias, strat) for p in pares]

    # agregado entre pares
    agregado = {}
    for nome in POLITICAS:
        t = w = 0
        tot = 0.0
        for r in resultados:
            po = r["policies"].get(nome, {})
            t += po.get("trades", 0)
            w += po.get("wins", 0)
            tot += po.get("total_R", 0.0)
        agregado[nome] = {"trades": t, "wins": w, "total_R": round(tot, 2),
                          "winrate_pct": round(w / t * 100, 1) if t else 0,
                          "avg_R": round(tot / t, 3) if t else 0}

    print("\n" + "=" * 100)
    print("📊 AGREGADO WALK-FORWARD (R normalizado, mesmo modelo de risco)")
    print(json.dumps(agregado, ensure_ascii=False, indent=2))
    for r in resultados:
        print(f"\n--- {r['symbol']} ---")
        print(json.dumps(r.get("policies", {}), ensure_ascii=False, indent=2))

    sufixo = ("_ker%.2f" % args.ker_minimo) if args.ker else ""
    sufixo += ("_" + args.tag) if args.tag else ""
    out_path = DATA_DIR / ("validacao_walkforward_politica%s.json" % sufixo)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump({"por_par": resultados, "agregado": agregado}, fh, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 Salvo em {out_path}")


if __name__ == "__main__":
    main()
