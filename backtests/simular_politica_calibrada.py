"""Simulação Before/After da política sugerida pela calibração de trades reais.

Reaproveita os MESMOS 18 trades reais reconstruídos por
backtests/calibrador_trades_reais.py (entradas, direção, alvo e fills reais do
live) e resimula cada um com a política sugerida em
data/calibracao_trades_reais.json:
  - dist_stop_sugerida_pct : distância inicial do stop (default 2.052%)
  - trailing_trigger_pct   : ativação do trailing no percurso (default 0.859%)
Trailing espaçado pela mesma distância do stop, com ratchet em favor da posição.
Preenchimento conservador: no mesmo candle, stop vence alvo.

Saída: comparativo por trade e agregado ANTES (real) x DEPOIS (simulado).

Uso (VM Oracle):
  ~/cryptobot/.venv/bin/python backtests/simular_politica_calibrada.py
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
    DATA_DIR,
    SYMBOL_DEFAULT,
)

DATA_DIR = BASE_DIR / "data"
CALIB_FILE = DATA_DIR / "calibracao_trades_reais.json"

# Política sugerida (fallback se o relatório da calibração não existir)
POLITICA_DEFAULT = {
    "dist_stop_sugerida_pct": 2.052,
    "trailing_trigger_pct": 0.859,
}


def carregar_politica() -> dict:
    pol = dict(POLITICA_DEFAULT)
    if CALIB_FILE.exists():
        try:
            sug = json.loads(CALIB_FILE.read_text(encoding="utf-8")).get("politica_sugerida", {})
            for k in POLITICA_DEFAULT:
                if sug.get(k) is not None:
                    pol[k] = float(sug[k])
        except Exception:
            pass
    return pol


# ---------------------------------------------------------------------------
# Simulação de um trade com a política calibrada
# ---------------------------------------------------------------------------

def simular_trade(trade: dict, velas: pd.DataFrame, dist_stop_pct: float, trail_trigger_pct: float) -> dict:
    """Roda o trade de entrada real com stop/trailing calibrados em velas 5m."""
    entry = float(trade["entry_price"]) if trade.get("entry_price") is not None else None
    if entry is None or entry <= 0:
        return None

    compra = trade["direcao"] == "COMPRA"
    sinal = 1.0 if compra else -1.0
    alvo = trade.get("take_profit_initial")
    if alvo is None or alvo <= 0:
        alvo = entry + sinal * entry * 0.019  # fallback: alvo médio do live (~1.9%)

    dist = entry * dist_stop_pct / 100.0
    trig = entry * trail_trigger_pct / 100.0

    if compra:
        stop0 = entry - dist
        alvo_px = alvo
    else:
        stop0 = entry + dist
        alvo_px = alvo

    t0 = pd.Timestamp(int(trade["entry_ts"]) // 300 * 300, unit="s", tz="utc")
    janela = velas[velas.index >= t0]
    if janela is None or len(janela) == 0:
        return {"exit_price": entry, "exit_reason": "SEM_VELAS", "pnl_usdt": 0.0, "mfe_pct": 0.0}

    stop = stop0
    extremo = entry          # pico (compra) ou fundo (venda)
    atrail = False

    for ts, row in janela.iterrows():
        hi, lo = float(row["high"]), float(row["low"])

        # 1) stop primeiro (conservador, sem lookahead intra-candle)
        if (compra and lo <= stop) or (not compra and hi >= stop):
            motivo = "TRAILING_STOP" if atrail else "STOP_LOSS"
            return {"exit_price": stop, "exit_reason": motivo, "exit_ts": float(ts.timestamp()), "pnl": None}

        # 2) alvo
        if (compra and hi >= alvo_px) or (not compra and lo <= alvo_px):
            return {"exit_price": alvo_px, "exit_reason": "TAKE_PROFIT", "exit_ts": float(ts.timestamp()), "pnl": None}

        # 3) atualiza extremo e trailing (após as checagens do candle)
        novo_ext = max(extremo, hi) if compra else min(extremo, lo)
        if not atrail:
            excursao = (novo_ext - entry) if compra else (entry - novo_ext)
            if excursao >= trig:
                atrail = True
        extremo = novo_ext
        if atrail:
            candidato = extremo - dist if compra else extremo + dist
            stop = max(stop, candidato) if compra else min(stop, candidato)

    # fim das velas sem saída: encerra a mercado no último close (posição residencial)
    ultimo_close = float(janela["close"].iloc[-1])
    return {"exit_price": ultimo_close, "exit_reason": "FIM_DADOS", "exit_ts": None, "pnl": None}


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------

def main() -> None:
    pol = carregar_politica()
    dist_stop_pct = pol["dist_stop_sugerida_pct"]
    trail_trigger_pct = pol["trailing_trigger_pct"]
    print("=" * 96)
    print("🔁 SIMULAÇÃO BEFORE/AFTER — POLÍTICA SUGERIDA PELA CALIBRAÇÃO")
    print(f"   stop inicial: {dist_stop_pct:.3f}% | trailing ativa em: +{trail_trigger_pct:.3f}% "
          f"(espaçamento do trailing = {dist_stop_pct:.3f}%)")
    print("=" * 96)

    ordens = None
    origem = None
    for f in ORDEM_FILES:
        if f.exists():
            with f.open("r", encoding="utf-8") as fh:
                ordens = json.load(fh).get("ordens", [])
                origem = f
                break
    if not ordens:
        print("❌ Sem ordens para simular.")
        sys.exit(1)
    print(f"📁 Origem: {origem.name} | ordens: {len(ordens)}")

    log_events = parse_bot_log(BOT_LOG)
    trades, parciais = reconstruir_trades(ordens, log_events)
    print(f"🧮 Trades: {len(trades)} completos | {len(parciais)} incompletos (ignorados)\n")

    symbol = trades[0]["symbol"] if trades else SYMBOL_DEFAULT
    velas = velas_kucoin(symbol)

    linhas = []
    antes_pnl = depois_pnl = 0.0
    antes_wins = depois_wins = 0
    antes_wins_pnl = antes_losses_pnl = depois_wins_pnl = depois_losses_pnl = 0.0
    motivos_dep = {}

    print(f"{'id':>14} | {'dir':<6} | {'entrada':>8} | {'SAÍDA REAL':>24} | {'pnl real':>9} | "
          f"{'SAÍDA SIMULADA':>24} | {'pnl sim':>9} | {'delta':>9}")
    print("-" * 96)

    for t in trades:
        amount = t.get("exit_amount") or t.get("entry_amount") or 0.0
        sinal = 1.0 if t["direcao"] == "COMPRA" else -1.0
        pnl_real = (t["exit_price"] - t["entry_price"]) * amount * sinal
        antes_pnl += pnl_real
        if pnl_real > 0:
            antes_wins += 1
            antes_wins_pnl += pnl_real
        else:
            antes_losses_pnl += pnl_real

        sim = simular_trade(t, velas, dist_stop_pct, trail_trigger_pct)
        if sim is None:
            continue
        motivo_sim = sim.get("exit_reason") or "STOP_LOSS"
        pnl_sim = (sim["exit_price"] - t["entry_price"]) * amount * sinal
        depois_pnl += pnl_sim
        if pnl_sim > 0:
            depois_wins += 1
            depois_wins_pnl += pnl_sim
        else:
            depois_losses_pnl += pnl_sim
        motivos_dep[motivo_sim] = motivos_dep.get(motivo_sim, 0) + 1

        real_txt = f"{t['exit_price']:.3f} ({t['exit_reason'][:10]})"
        sim_txt = f"{sim['exit_price']:.3f} ({motivo_sim[:10]})"
        print(f"{t['trade_id']:>14} | {t['direcao']:<6} | {t['entry_price']:>8.3f} | {real_txt:>24} | "
              f"{pnl_real:>+9.2f} | {sim_txt:>24} | {pnl_sim:>+9.2f} | {pnl_sim - pnl_real:>+9.2f}")

        linhas.append({
            "trade_id": t["trade_id"], "direcao": t["direcao"],
            "entry": t["entry_price"], "amount": amount,
            "real_exit": t["exit_price"], "real_reason": t["exit_reason"], "real_pnl_usdt": round(pnl_real, 2),
            "mfe_pct_real": None, "mae_pct_real": None,
            "sim_exit": round(sim["exit_price"], 3), "sim_reason": motivo_sim, "sim_pnl_usdt": round(pnl_sim, 2),
        })

    n = len(linhas)
    resumo = {
        "politica": pol,
        "trades": n,
        "antes": {
            "pnl_total_usdt": round(antes_pnl, 2),
            "winrate_pct": round(antes_wins / n * 100, 1) if n else 0,
            "avg_win_usdt": round(antes_wins_pnl / max(antes_wins, 1), 2),
            "avg_loss_usdt": round(antes_losses_pnl / max(n - antes_wins, 1), 2),
            "wins": antes_wins, "losses": n - antes_wins,
        },
        "depois": {
            "pnl_total_usdt": round(depois_pnl, 2),
            "winrate_pct": round(depois_wins / n * 100, 1) if n else 0,
            "avg_win_usdt": round(depois_wins_pnl / max(depois_wins, 1), 2),
            "avg_loss_usdt": round(depois_losses_pnl / max(n - depois_wins, 1), 2),
            "wins": depois_wins, "losses": n - depois_wins,
            "exit_reasons": motivos_dep,
        },
        "delta_pnl_usdt": round(depois_pnl - antes_pnl, 2),
        "por_trade": linhas,
    }

    out_path = DATA_DIR / "simulacao_politica_calibrada.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(resumo, fh, ensure_ascii=False, indent=2, default=str)

    print("\n" + "=" * 96)
    print("📊 RESUMO ANTES (real) x DEPOIS (política calibrada)")
    print(json.dumps({k: resumo[k] for k in ["trades", "antes", "depois", "delta_pnl_usdt"]},
                     ensure_ascii=False, indent=2))
    print(f"\n💾 Salvo em {out_path}")


if __name__ == "__main__":
    main()
