"""Calibrador de Trades Reais -> Laya Telemetry (System 1).

Reconstrói os trades reais (paper=0) a partir de data/ordens_oracle_vm.json
(ou data/ordens_export.json na VM), enriquece com o log do bot
(`Posição aberta`, `Alvo atingido em`, `Trailing stop`) e velas 5m da KuCoin
para computar MFE/MAE reais, e alimenta o Laya System 1 via
core/laya_telemetry.py (playbook de acertos / veto de falhas).

Também emite um relatório de calibração agregado (winrate, PnL, razão
MFE/MAE, stop vs MAE realizada) e salva em data/calibracao_trades_reais.json.

Uso (VM Oracle): ~/cryptobot/.venv/bin/python backtests/calibrador_trades_reais.py
"""

import os
import re
import sys
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from collections import Counter, defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "core"))
sys.path.insert(0, str(BASE_DIR / "strategies"))

from core.laya_telemetry import LayaTradeFeedbackCollector, get_system1_engine  # noqa: E402
from backtests.apex_dinamics.backtest_kucoin_7dias_top4_10x import (  # noqa: E402
    fetch_kucoin_futures_5m_7dias,
)

DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"

ORDEM_FILES = [
    DATA_DIR / "ordens_oracle_vm.json",
    DATA_DIR / "ordens_export.json",
]
BOT_LOG = LOG_DIR / "bot.log"

SYMBOL_DEFAULT = "SOL/USDT:USDT"
STRATEGY_ID = "STRAT_APEX_DINAMICS"
STRATEGY_NAME = "apex_dinamics (live)"
TRADE_ID_PREFIX = "real_apex_"

# ---------------------------------------------------------------------------
# Parser do bot.log
# ---------------------------------------------------------------------------

RE_ABERTURA = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s*\|\s*\w+\s*\|\s*fimathe\s*\|\s*"
    r"Posição aberta: Posicao\(symbol='(?P<symbol>[^']+)', side='(?P<side>\w+)', "
    r"amount=(?P<amount>[\d.]+).*?entrada=(?P<entrada>[\d.]+), stop=(?P<stop>[\d.]+), alvo=(?P<alvo>[\d.]+)"
)
RE_ALVO_HIT = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s*\|\s*s*\|?\s*.*fimathe\s*\|\s*"
    r"Alvo atingido em (?P<preco>[\d.]+). Encerrando"
)
RE_TRAILING = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s*\|\s*\w+\s*\|\s*fimathe\s*\|\s*"
    r"\[apex_dinamics\] Trailing stop: (?P<antigo>[\d.]+) -> (?P<novo>[\d.]+)"
)
RE_SINAL = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s*\|+\s*\w+\s*\|\s*fimathe\s*\|\s*"
    r"Sinal: Sinal\(direcao=<(?:Direcao\.)?(?:COMPRA|VENDA): '[^']+'>, preco_entrada=[\d.]+.*?motivo='(?P<motivo>[^']*)'"
)


def _parse_log_ts(ts: str) -> float:
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def parse_bot_log(log_path: Path) -> dict:
    """Extrai eventos do log: aberturas, alvos atingidos, trailing e motivos de sinal."""
    if not log_path.exists():
        print(f"[AVISO] Log do bot não encontrado: {log_path}")
        return {"aberturas": [], "alvos": [], "trailings": [], "sinais": []}

    txt = log_path.read_text(encoding="utf-8", errors="replace")
    aberturas, alvos, trailings, sinais = [], [], [], []
    for m in RE_ABERTURA.finditer(txt):
        aberturas.append({
            "ts": _parse_log_ts(m.group("ts")),
            "symbol": m.group("symbol"),
            "side": m.group("side"),
            "amount": float(m.group("amount")),
            "entrada": float(m.group("entrada")),
            "stop": float(m.group("stop")),
            "alvo": float(m.group("alvo")),
        })
    for m in RE_ALVO_HIT.finditer(txt):
        alvos.append({"ts": _parse_log_ts(m.group("ts")), "preco": float(m.group("preco"))})
    for m in RE_TRAILING.finditer(txt):
        trailings.append({"ts": _parse_log_ts(m.group("ts")), "novo": float(m.group("novo"))})
    for m in RE_SINAL.finditer(txt):
        sinais.append({"ts": _parse_log_ts(m.group("ts")), "motivo": m.group("motivo")})

    return {"aberturas": aberturas, "alvos": alvos, "trailings": trailings, "sinais": sinais}


def _nearest(events: list, ts: float, max_delta: float, key: str = "ts") -> Optional[dict]:
    cands = [e for e in events if abs(e[key] - ts) <= max_delta]
    if not cands:
        return None
    return min(cands, key=lambda e: abs(e[key] - ts))


def _first_after(events: list, ts: float, max_delta: float) -> Optional[dict]:
    # Log do bot é emitido ~1s antes do registro da ordem; janela olha 30s para trás
    cands = [e for e in events if ts - 30.0 <= e["ts"] <= ts + max_delta]
    if not cands:
        return None
    return min(cands, key=lambda e: e["ts"])


# ---------------------------------------------------------------------------
# Reconstrução dos trades a partir das ordens
# ---------------------------------------------------------------------------

def reconstruir_trades(ordens: list, log_events: dict) -> list:
    """
    Cada ordem com papel='entrada' abre um trade; 'alvo'/'stop_executado' o
    encerra (com preço real de fill); 'stop_trailing' marca trailing ativo.
    """
    aberturas = log_events["aberturas"]
    alvos, trailings, sinais = log_events["alvos"], log_events["trailings"], log_events["sinais"]

    trades, active = [], None
    for o in sorted(ordens, key=lambda x: x["ts"]):
        papel = (o.get("papel") or "").lower()
        ts = datetime.fromisoformat(o["ts"]).timestamp()
        symbol = o.get("symbol") or SYMBOL_DEFAULT
        bruto = {}
        try:
            bruto = json.loads(o.get("bruto") or "{}")
        except Exception:
            pass

        if papel == "entrada":
            if active is not None:
                trades.append(active)  # trade órfão guardado mesmo sem exit
            ab = _nearest(aberturas, ts, 120.0, "ts")
            sl = _nearest(sinais, ts, 10.0, "ts")
            active = {
                "trade_id": f"{TRADE_ID_PREFIX}{o['id']}",
                "ordem_entrada_id": o["id"],
                "symbol": symbol,
                "entry_ts": ts,
                "side_ordem": o.get("side"),
                "paper": o.get("paper"),
                # enriquecimento via log
                "direcao": None,
                "entry_price": None,
                "entry_amount": None,
                "stop_loss_initial": None,
                "take_profit_initial": None,
                "headline": (sl or {}).get("motivo") or "Sinal Apex Dinamics (live)",
                # exit
                "exit_ts": None,
                "exit_price": None,
                "exit_reason": None,
                "exit_amount": None,
                "trailing": False,
            }
            if ab:
                active["direcao"] = "COMPRA" if ab["side"] == "buy" else "VENDA"
                active["entry_price"] = ab["entrada"]
                active["entry_amount"] = ab["amount"]
                active["stop_loss_initial"] = ab["stop"]
                active["take_profit_initial"] = ab["alvo"]
            continue

        if papel == "stop_trailing" and active is not None:
            active["trailing"] = True
            continue

        if papel == "alvo" and active is not None:
            al = _first_after(alvos, ts, 120.0)
            if al and active["exit_ts"] is None:
                active["exit_ts"] = al["ts"]
                active["exit_price"] = al["preco"]
                active["exit_reason"] = "TAKE_PROFIT"
            continue

        if papel == "stop_executado" and active is not None:
            if o.get("price") is not None and active["exit_ts"] is None:
                active["exit_ts"] = ts
                active["exit_price"] = float(o["price"])
                active["exit_reason"] = "STOP_LOSS"
                amt = o.get("amount") or bruto.get("filled") or bruto.get("info", {}).get("fillSize")
                if amt:
                    active["exit_amount"] = float(amt)
                # direção inferida pela ordem de fechamento (buy fecha short)
                if active["direcao"] is None and o.get("side") in ("buy", "sell"):
                    active["direcao"] = "VENDA" if o["side"] == "buy" else "COMPRA"
            continue

    if active is not None:
        trades.append(active)

    completos = [t for t in trades if t["exit_ts"] is not None]
    parciais = [t for t in trades if t not in completos]
    return completos, parciais


# ---------------------------------------------------------------------------
# Velas 5m para MFE/MAE
# ---------------------------------------------------------------------------

def velas_kucoin(symbol: str, minutos: int = 4700) -> pd.DataFrame:
    """Busca velas 5m recentes via API paginada; se falhar, usa cache local com aviso."""
    try:
        print(f"⏳ Baixando velas 5m de {symbol} (janela={minutos * 5}min ~ {minutos / 288:.1f} dias)...")
        df = fetch_kucoin_futures_5m_7dias(symbol, target_candles=minutos)
        print(f"   ✔ {len(df)} velas ({df.index[0]} → {df.index[-1]})")
        return df
    except Exception as e:
        print(f"   [AVISO] Falha ao buscar velas ({e}); tentando cache local...")
        cache = DATA_DIR / "backtest_cache" / f"{symbol.split('/')[0]}USDT_5m_30dias.csv"
        if cache.exists():
            df = pd.read_csv(cache)
            df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df.index.name = "timestamp"
            return df[["open", "high", "low", "close", "volume"]]
        raise


def mfe_mae_por_velas(trade: dict, velas: pd.DataFrame) -> dict:
    """MFE/MAE reais entre entrada e saída, direção-consciente.

    Nota: epochs são alinhados à grade 5m (floor) — a vela aberta no instante
    de entrada/saída deve entrar na janela; comparações com nanossegundos
    retornam máscara vazia nas versões antigas de pandas desta VM.
    """
    entry = pd.Timestamp(int(trade["entry_ts"]) // 300 * 300, unit="s", tz="utc")
    exit_ = pd.Timestamp(int(trade["exit_ts"]) // 300 * 300, unit="s", tz="utc")
    janela = velas[(velas.index >= entry) & (velas.index <= exit_)]
    if janela.empty:
        # tolerância: vela da própria entrada
        janela = velas[velas.index <= exit_]
        if janela.empty:
            return {"mfe_pct": 0.0, "mae_pct": 0.0, "highest": trade["entry_price"],
                    "lowest": trade["entry_price"], "mfe_time": trade["entry_ts"],
                    "mae_time": trade["entry_ts"]}

    hi, lo = float(janela["high"].max()), float(janela["low"].min())
    ts_hi = float(janela["high"].idxmax().timestamp())
    ts_lo = float(janela["low"].idxmin().timestamp())
    entry_price = trade["entry_price"]
    if trade["direcao"] == "COMPRA":
        mfe_pct = (hi - entry_price) / entry_price
        mae_pct = (lo - entry_price) / entry_price
    else:
        mfe_pct = (entry_price - lo) / entry_price
        mae_pct = (entry_price - hi) / entry_price
    return {"mfe_pct": mfe_pct, "mae_pct": mae_pct, "highest": hi, "lowest": lo,
            "mfe_time": ts_hi, "mae_time": ts_lo}


# ---------------------------------------------------------------------------
# Alimentação do Laya System 1
# ---------------------------------------------------------------------------

def trades_ja_alimentados(feedback_path: Path) -> set:
    ids = set()
    if not feedback_path.exists():
        return ids
    with feedback_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            tid = rec.get("trade_id") or ""
            if tid.startswith(TRADE_ID_PREFIX):
                ids.add(tid)
    return ids


def limpar_registros_proprios(feedback_path: Path, training_path: Path) -> int:
    """Remove registros do próprio calibrador (trade_id prefixado) para re-alimentação."""
    total = 0
    for p in (feedback_path, training_path):
        if not p.exists():
            continue
        lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        keep = [l for l in lines if TRADE_ID_PREFIX not in l]
        total += len(lines) - len(keep)
        with p.open("w", encoding="utf-8") as f:
            f.write("\n".join(keep) + ("\n" if keep else ""))
    return total


def alimentar_laya(trades: list, velas: pd.DataFrame, ja_feitos: set) -> list:
    collector = LayaTradeFeedbackCollector(enable_system1_memory=True)
    outcomes = []
    for t in trades:
        mm = mfe_mae_por_velas(t, velas)

        # Fallback: se o log não registrou a Posição aberta, usa o close da vela mais recente <= entrada
        if t.get("entry_price") is None:
            entry_ts = pd.Timestamp(int(t["entry_ts"]), unit="s", tz="utc")
            anterior = velas[velas.index <= entry_ts]
            if not anterior.empty:
                t["entry_price"] = float(anterior["close"].iloc[-1])
                t["aprox_entry"] = True
                print(f"· {t['trade_id']}: entrada aproximada pela vela 5m = {t['entry_price']:.3f}")
                # rederiva excursões com o novo preço
                mm = mfe_mae_por_velas(t, velas)
            else:
                print(f"· {t['trade_id']}: sem preço de entrada (sem log e sem velas) — pulando")
                continue

        amount = t.get("exit_amount") or t.get("entry_amount") or 0.0
        sinal = 1.0 if t["direcao"] == "COMPRA" else -1.0
        pnl_usdt = (t["exit_price"] - t["entry_price"]) * amount * sinal

        predicted = "BULLISH_CATALYST" if t["direcao"] == "COMPRA" else "BEARISH_SHOCK"

        if t["trade_id"] in ja_feitos:
            print(f"· {t['trade_id']} já alimentado anteriormente — pulando")
            continue

        ctx = collector.start_trade(
            symbol=t["symbol"],
            direction=t["direcao"],
            entry_price=float(t["entry_price"]),
            headline=t["headline"],
            predicted_impact=predicted,
            confidence=0.85,
            stop_loss_initial=float(t["stop_loss_initial"] or 0.0),
            take_profit_initial=float(t["take_profit_initial"] or 0.0),
            trade_id=t["trade_id"],
            strategy_id=STRATEGY_ID,
            strategy_name=STRATEGY_NAME,
            exchange="kucoin",
            market_type="futures",
            venue="kucoin_futures",
            broker="KuCoin",
            broker_mode="live",
            timeframe="5m",
            leverage=5.0,
        )
        # Correções históricas (timestamps e excursões reais das velas)
        ctx.entry_time = float(t["entry_ts"])
        ctx.highest_price = mm["highest"]
        ctx.lowest_price = mm["lowest"]
        ctx.mfe_time = mm["mfe_time"]
        ctx.mae_time = mm["mae_time"]
        if t["trailing"]:
            ctx.trailing_tightened = True
            ctx.exhaustion_alerts_count = max(ctx.exhaustion_alerts_count, 1)

        out = collector.close_trade(
            trade_id=t["trade_id"],
            exit_price=float(t["exit_price"]),
            exit_reason=t["exit_reason"] or "STANDARD_TRADE_COMPLETED",
            realized_pnl_usdt=round(pnl_usdt, 2),
            exit_time=float(t["exit_ts"]),
        )
        if out:
            outcomes.append(out)
            print(f"✔ {t['trade_id']}: {t['direcao']} {t['symbol']} {t['entry_price']:.3f}→{t['exit_price']:.3f} "
                  f"({t['exit_reason']}) pnl≈${pnl_usdt:+.2f} | MFE {mm['mfe_pct'] * 100:+.2f}% / MAE {mm['mae_pct'] * 100:+.2f}%")

    return [
        {"trade_id": o.trade_id, "symbol": o.symbol, "direction": o.direction,
         "entry_price": o.entry_price, "exit_price": o.exit_price,
         "entry_time": o.entry_time, "exit_time": o.exit_time,
         "realized_pnl_pct": o.realized_pnl_pct, "realized_pnl_usdt": o.realized_pnl_usdt,
         "exit_reason": o.exit_reason, "mfe_pct": o.mfe_pct, "mae_pct": o.mae_pct,
         "reward_score": o.reward_score, "success": o.success,
         "laya_feedback_label": o.laya_feedback_label, "trailing_tightened": o.trailing_tightened,
         "session_utc": o.session_utc}
        for o in outcomes
    ]


# ---------------------------------------------------------------------------
# Relatório de calibração
# ---------------------------------------------------------------------------

def relatorio(outcomes: list, parciais: int) -> dict:
    if not outcomes:
        return {"total": 0, "parciais": parciais}
    df = pd.DataFrame(outcomes)
    wins = df[df["success"]]
    losses = df[~df["success"]]
    rep = {
        "total": len(df),
        "sem_fechar": parciais,
        "winrate": round(len(wins) / len(df) * 100, 1),
        "lucro_total_usdt": round(df["realized_pnl_usdt"].sum(), 2),
        "avg_win": round(wins["realized_pnl_pct"].mean() * 100, 3) if len(wins) else None,
        "avg_loss": round(losses["realized_pnl_pct"].mean() * 100, 3) if len(losses) else None,
        "exit_reasons": dict(Counter(df["exit_reason"])),
        "feedback_labels": dict(Counter(df["laya_feedback_label"])),
        "mfe_medio_pct": round(df["mfe_pct"].mean() * 100, 3),
        "mae_medio_pct": round(df["mae_pct"].mean() * 100, 3),
        "mfe_mae_ratio_mediano": None,
        "exits_por_sessao": {},
        "destaque": df.sort_values("reward_score", ascending=False).head(3).to_dict("records"),
        "politica_sugerida": {},
    }
    ratio = (df["mfe_pct"] / df["mae_pct"].abs().where(df["mae_pct"].abs() > 1e-9, pd.NA)).dropna()
    if len(ratio):
        rep["mfe_mae_ratio_mediano"] = round(float(ratio.median()), 2)
    if "session_utc" in df.columns:
        rep["exits_por_sessao"] = dict(Counter(df["session_utc"]))

    # Sugestões de política a partir dos percentis reais de risco/percurso
    mae_abs = df["mae_pct"].abs() * 100
    mfe_abs = df["mfe_pct"].abs() * 100
    rep["politica_sugerida"] = {
        "mae_p50_pct": round(mae_abs.quantile(0.5), 3),
        "mae_p90_pct": round(mae_abs.quantile(0.9), 3),
        "mfe_p50_pct": round(mfe_abs.quantile(0.5), 3),
        "mfe_p90_pct": round(mfe_abs.quantile(0.9), 3),
        "dist_stop_sugerida_pct": round(float(mae_abs.quantile(0.9)) * 1.1, 3),
        "trailing_trigger_pct": round(float(mfe_abs.quantile(0.75)), 3),
    }
    return rep


def main() -> None:
    print("=" * 88)
    print("🎯 CALIBRADOR DE TRADES REAIS -> LAYA SYSTEM 1")
    print("=" * 88)

    ordens = None
    origem = None
    for f in ORDEM_FILES:
        if f.exists():
            with f.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            ordens = data.get("ordens", [])
            origem = f
            break
    if not ordens:
        print("❌ Nenhum arquivo de ordens reais encontrado (ordens_oracle_vm.json / ordens_export.json).")
        sys.exit(1)
    print(f"📁 Origem: {origem.name} | ordens: {len(ordens)} "
          f"| reais (paper=0): {sum(1 for o in ordens if o.get('paper') == 0)}")

    log_events = parse_bot_log(BOT_LOG)
    print(f"📜 Log: {len(log_events['aberturas'])} aberturas | {len(log_events['alvos'])} alvos | "
          f"{len(log_events['trailings'])} trailings | {len(log_events['sinais'])} sinais")

    trades, parciais = reconstruir_trades(ordens, log_events)
    print(f"🧮 Trades completos: {len(trades)} | incompletos: {len(parciais)}")

    symbol = trades[0]["symbol"] if trades else SYMBOL_DEFAULT
    velas = velas_kucoin(symbol)

    s1 = get_system1_engine()
    print(f"🧠 Engine System 1: {'conectado' if s1 else 'indisponível (telemetria local apenas)'}")

    collector_feedback = Path(os.getenv("CRYPTOBOT_FEEDBACK_FILE", str(DATA_DIR / "laya_trade_feedback.jsonl")))

    if "--resync" in sys.argv:
        removidos = limpar_registros_proprios(
            collector_feedback, Path(os.getenv("CRYPTOBOT_TRAINING_FILE", str(DATA_DIR / "laya_training_dataset.jsonl")))
        )
        print(f"♻️  Re-alimentação: {removidos} registros anteriores do calibrador removidos")

    ja = trades_ja_alimentados(collector_feedback)
    outcomes = alimentar_laya(trades, velas, ja)
    print(f"\n📤 {len(outcomes)} trades armazenados em {collector_feedback.name} e dataset de treino")

    rep = relatorio(list(outcomes), len(parciais))
    out_path = DATA_DIR / "calibracao_trades_reais.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=2, default=str)

    print("\n" + "=" * 88)
    print("📊 RELATÓRIO DE CALIBRAÇÃO")
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    print(f"\n💾 Relatório salvo em {out_path}")


if __name__ == "__main__":
    main()
