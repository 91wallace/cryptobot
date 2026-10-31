"""Variação HÍBRIDA da política de calibração (grid curto) — Before/After vs live.

Política híbrida proposta na auditoria da simulação anterior:
  - stop inicial próximo ao live: grid 0.60%–1.05% (P50–P75 de MAE ≈ 0.56–1.0%)
  - gatilho de trailing: +0.859% de percurso (foi o número forte da calibração)
  - espaçamento do trailing: 0.75x da distância do stop (degrau elástico da Apex)

Cada combo é pontuado com os MESMOS 18 trades reais em velas 5m, preenchimento
conservador (no mesmo candle, stop vence alvo), em duas visões:
  1. tamanho fixo (como o live operou)
  2. risco equalizado (mesmo USDT em risco por trade que o live teve: pnl ×
     distância real do stop do trade ÷ distância da política)

Uso (VM Oracle):
  ~/cryptobot/.venv/bin/python backtests/simular_politica_hibrida.py
"""

import os
import sys
import json
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "core"))
sys.path.insert(0, str(BASE_DIR / "strategies"))

from backtests.calibrador_trades_reais import (  # noqa: E402
    parse_bot_log,
    reconstruir_trades,
    velas_kucoin,
    ORDEM_FILES,
    BOT_LOG,
    SYMBOL_DEFAULT,
    DATA_DIR,
)

TRIGGER_FIXO = 0.859
GRADE_STOPS = [0.60, 0.75, 0.90, 1.05]
FATOR_ESPACAMENTO = 0.75


def simular_trade_hibrido(trade: dict, velas: pd.DataFrame, dist_stop_pct: float,
                          trail_trigger_pct: float, trail_spacing_pct: float) -> dict:
    entry = trade.get("entry_price")
    if entry is None or entry <= 0:
        return None

    compra = trade["direcao"] == "COMPRA"
    sinal = 1.0 if compra else -1.0
    alvo = trade.get("take_profit_initial")
    if alvo is None or alvo <= 0:
        alvo = entry + sinal * entry * 0.019

    dist = entry * dist_stop_pct / 100.0
    trig = entry * trail_trigger_pct / 100.0
    espaco = entry * trail_spacing_pct / 100.0
    stop0 = entry - dist if compra else entry + dist

    t0 = pd.Timestamp(int(trade["entry_ts"]) // 300 * 300, unit="s", tz="utc")
    janela = velas[velas.index >= t0]
    if len(janela) == 0:
        return {"exit_price": entry, "exit_reason": "SEM_VELAS"}

    stop = stop0
    extremo = entry
    atrail = False

    for ts, row in janela.iterrows():
        hi, lo = float(row["high"]), float(row["low"])

        if (compra and lo <= stop) or (not compra and hi >= stop):
            return {"exit_price": stop, "exit_reason": "TRAILING_STOP" if atrail else "STOP_LOSS"}

        if (compra and hi >= alvo) or (not compra and lo <= alvo):
            return {"exit_price": alvo, "exit_reason": "TAKE_PROFIT"}

        novo_ext = max(extremo, hi) if compra else min(extremo, lo)
        if not atrail:
            ex = (novo_ext - entry) if compra else (entry - novo_ext)
            if ex >= trig:
                atrail = True
        extremo = novo_ext
        if atrail:
            cand = extremo - espaco if compra else extremo + espaco
            stop = max(stop, cand) if compra else min(stop, cand)

    return {"exit_price": float(janela["close"].iloc[-1]), "exit_reason": "FIM_DADOS"}


def main() -> None:
    print("=" * 100)
    print("🧪 VARIAÇÃO HÍBRIDA — stop P50–P75 de MAE + trailing 0,75x do stop + gatilho +0,859%")
    print("=" * 100)

    ordens = None
    for f in ORDEM_FILES:
        if f.exists():
            with f.open("r", encoding="utf-8") as fh:
                ordens = json.load(fh).get("ordens", [])
                break
    trades, parciais = reconstruir_trades(ordens, parse_bot_log(BOT_LOG))
    print(f"🧮 Trades: {len(trades)} | incompletos ignorados: {len(parciais)}")

    symbol = trades[0]["symbol"] if trades else SYMBOL_DEFAULT
    velas = velas_kucoin(symbol)

    # referência real + distância real de stop por trade (para risco equalizado)
    dist_real = {}
    for t in trades:
        stop = t.get("stop_loss_initial") or 0.0
        dist_real[t["trade_id"]] = abs(t["entry_price"] - stop) / t["entry_price"] if stop > 0 else 0.006

    ref_real = {"pnl": 0.0, "wins": 0}
    for t in trades:
        amount = t.get("exit_amount") or t.get("entry_amount") or 0.0
        s = 1.0 if t["direcao"] == "COMPRA" else -1.0
        pnl = (t["exit_price"] - t["entry_price"]) * amount * s
        ref_real["pnl"] += pnl
        ref_real["wins"] += pnl > 0

    resultados = []
    detalhes_melhor = None
    melhor_pnl_eq = -1e9
    melhor_cfg = None

    total_grade = len(GRADE_STOPS)
    for i, stop_pct in enumerate(GRADE_STOPS, 1):
        esp_pct = round(stop_pct * FATOR_ESPACAMENTO, 3)
        pnl_fix = pnl_eq = 0.0
        wins_fix = wins_eq = 0
        por_trade = []
        for t in trades:
            amount = t.get("exit_amount") or t.get("entry_amount") or 0.0
            s = 1.0 if t["direcao"] == "COMPRA" else -1.0
            sim = simular_trade_hibrido(t, velas, stop_pct, TRIGGER_FIXO, esp_pct)
            if sim is None:
                continue
            pnl = (sim["exit_price"] - t["entry_price"]) * amount * s
            fator_eq = dist_real[t["trade_id"]] / (stop_pct / 100.0)
            pnl_eq_v = pnl * fator_eq
            pnl_fix += pnl
            pnl_eq += pnl_eq_v
            wins_fix += pnl > 0
            wins_eq += pnl_eq_v > 0
            por_trade.append({"trade_id": t["trade_id"], "direcao": t["direcao"],
                              "sim_exit": round(sim["exit_price"], 3), "sim_reason": sim["exit_reason"],
                              "sim_pnl_usdt": round(pnl, 2), "sim_pnl_eq_usdt": round(pnl_eq_v, 2)})
        resultados.append({
            "stop_pct": stop_pct, "trailing_espaco_pct": esp_pct, "trigger_pct": TRIGGER_FIXO,
            "pnl_fixo": round(pnl_fix, 2), "wins_fixo": wins_fix,
            "pnl_risco_eq": round(pnl_eq, 2), "wins_eq": wins_eq,
        })
        if pnl_eq > melhor_pnl_eq:
            melhor_pnl_eq = pnl_eq
            melhor_cfg = resultados[-1]
            detalhes_melhor = por_trade
        print(f"[{i}/{total_grade}] stop {stop_pct:.2f}% | trailing esp {esp_pct:.3f}% | trig +{TRIGGER_FIXO}% "
              f"-> PnL fixo {pnl_fix:+9.2f}$ (W {wins_fix}) | PnL eq {pnl_eq:+9.2f}$ (W {wins_eq})")

    df = pd.DataFrame(resultados).sort_values("pnl_risco_eq", ascending=False)
    n = len(trades)
    print("\n" + "=" * 100)
    print("📊 REFERÊNCIA LIVE (antes): PnL $%+.2f | win %d/%d" % (ref_real["pnl"], ref_real["wins"], n))
    print("\n🏆 MELHOR COMBO (por PnL com risco equalizado):")
    print(json.dumps(melhor_cfg, ensure_ascii=False, indent=2))
    print("\nRanking completo:")
    print(df.drop(columns=["trigger_pct"]).to_string(index=False))

    print("\nDetalhe por trade (melhor combo):")
    for d in detalhes_melhor:
        print(f"  {d['trade_id']:>14} {d['direcao']:<6} saída {d['sim_exit']:.3f} ({d['sim_reason']:<13}) "
              f"pnl_fixo {d['sim_pnl_usdt']:>+8.2f} | pnl_eq {d['sim_pnl_eq_usdt']:>+8.2f}")

    out = {"referencia_live": ref_real, "grade": resultados, "melhor": melhor_cfg, "por_trade_melhor": detalhes_melhor}
    out_path = DATA_DIR / "simulacao_politica_hibrida.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 Salvo em {out_path}")


if __name__ == "__main__":
    main()
