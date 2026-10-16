"""Walk-Forward Analysis (WFA) e Comparativo Maker vs Taker nos 4 Pares (14 Dias).

Metodologia Institucional:
  - 14 Dias Completos (4.032 velas de 5m por par)
  - Pares: AVAXUSDT, SOLUSDT, NEARUSDT, ETHUSDT
  - Walk-Forward Split:
      * In-Sample (Dias 1 a 7): Treino e calibração
      * Out-of-Sample (Dias 8 a 14): Validação cega sem reajuste de parâmetros
  - Comparativo Taker (Mercado) vs Maker (Ordem Limite no Reteste)
"""
import sys
import time
from decimal import Decimal
import pandas as pd
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue, TraderId
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.objects import Money
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.data import BarType
from nautilus_trader.persistence.wranglers import BarDataWrangler

from strategies.nautilus_apex_strategy import (
    ApexDinamicsNautilusStrategy,
    ApexDinamicsNautilusConfig,
)
from backtests.apex_dinamics.backtest_7dias_top4_10x import fetch_binance_futures_5m_7dias
from backtests.apex_dinamics.run_nautilus_backtest import criar_instrumento_perp


def preparar_barras_nautilus(df: pd.DataFrame, instrument, bar_type):
    df_wrangler = df.copy()
    if "open_time" in df_wrangler.columns:
        df_wrangler["timestamp"] = pd.to_datetime(df_wrangler["open_time"], unit="ms", utc=True)
        df_wrangler = df_wrangler.set_index("timestamp")

    cols = ["open", "high", "low", "close", "volume"]
    for c in cols:
        df_wrangler[c] = df_wrangler[c].astype(np.float64).values.copy()
    df_wrangler = df_wrangler[cols]

    wrangler = BarDataWrangler(bar_type, instrument)
    return wrangler.process(df_wrangler)


def rodar_simulacao_nautilus(
    symbol: str,
    df_periodo: pd.DataFrame,
    tipo_ordem: str = "MARKET",
    pivo: int = 3,
    amp_min: float = 0.20,
    degrau: float = 0.65,
    saldo_inicial: float = 1000.0,
    alavancagem: int = 10,
    run_id: str = "RUN",
) -> dict:
    """Executa simulação unitária no motor Nautilus Trader."""
    preco_medio = float(df_periodo["close"].mean())
    instrument = criar_instrumento_perp(symbol, preco_medio)
    bar_type = BarType.from_str(f"{instrument.id}-5-MINUTE-LAST-EXTERNAL")
    venue_binance = Venue("BINANCE")

    bars = preparar_barras_nautilus(df_periodo, instrument, bar_type)

    engine_cfg = BacktestEngineConfig(
        trader_id=TraderId(f"WFA-{run_id}"),
        logging=LoggingConfig(log_level="ERROR"),
    )
    engine = BacktestEngine(config=engine_cfg)
    engine.add_venue(
        venue=venue_binance,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(saldo_inicial, USDT)],
        base_currency=USDT,
        default_leverage=Decimal(alavancagem),
    )
    engine.add_instrument(instrument)
    engine.add_data(bars)

    strat_cfg = ApexDinamicsNautilusConfig(
        instrument_id=str(instrument.id),
        bar_type=str(bar_type),
        alavancagem=alavancagem,
        fracao_banca_por_posicao=0.333,
        janela_pivo=pivo,
        amplitude_min_pct=amp_min,
        folga_stop=0.10,
        fator_degrau_trailing=degrau,
        tipo_ordem=tipo_ordem,
    )
    strategy = ApexDinamicsNautilusStrategy(config=strat_cfg)
    engine.add_strategy(strategy)

    engine.run()

    saldo_final = float(engine.portfolio.account(venue_binance).balance_total(USDT).as_double())
    pnl = saldo_final - saldo_inicial
    ret_pct = (pnl / saldo_inicial) * 100

    pos_rep = engine.trader.generate_positions_report()
    total_trades = len(pos_rep) if not pos_rep.empty else 0

    fills = engine.trader.generate_order_fills_report()
    total_comissoes = 0.0
    if not fills.empty and "commissions" in fills.columns:
        total_comissoes = float(fills["commissions"].apply(lambda c: float(c.as_double()) if hasattr(c, "as_double") else 0.0).sum())

    return {
        "symbol": symbol,
        "tipo_ordem": tipo_ordem,
        "trades": total_trades,
        "saldo_inicial": saldo_inicial,
        "saldo_final": saldo_final,
        "pnl": pnl,
        "retorno_pct": ret_pct,
        "comissoes": total_comissoes,
        "velas": len(bars),
    }


def executar_walk_forward_14dias():
    print(f"\n{'='*85}")
    print("🛡️ WALK-FORWARD ANALYSIS & COMPARATIVO MAKER VS TAKER (14 DIAS)")
    print("   Pares: AVAXUSDT, SOLUSDT, NEARUSDT, ETHUSDT")
    print(f"{'='*85}\n")

    pares = ["AVAXUSDT", "SOLUSDT", "NEARUSDT", "ETHUSDT"]
    alvo_velas = 4032  # 14 dias x 288 velas/dia

    dados = {}
    for p in pares:
        print(f"📥 Baixando 14 dias de 5m da Binance Futures para {p}...")
        df = fetch_binance_futures_5m_7dias(p, target_candles=alvo_velas)
        dados[p] = df
        print(f"   ✅ {len(df)} velas carregadas ({df.index[0].strftime('%d/%m')} até {df.index[-1].strftime('%d/%m')})")

    ponto_corte = 2016  # Metade exata: primeiros 7 dias / segundos 7 dias

    print("\n" + "="*85)
    print("📊 ETAPA 1: WALK-FORWARD ANALYSIS (IN-SAMPLE VS OUT-OF-SAMPLE)")
    print("   In-Sample: Dias 1 a 7 | Out-of-Sample: Dias 8 a 14 (Teste Cego)")
    print("="*85)

    res_wfa = []
    for p in pares:
        df_full = dados[p]
        df_in = df_full.iloc[:ponto_corte]
        df_out = df_full.iloc[ponto_corte:]

        # Executa In-Sample
        res_in = rodar_simulacao_nautilus(p, df_in, tipo_ordem="MARKET", pivo=3, amp_min=0.20, degrau=0.65, run_id=f"{p}-IN")
        # Executa Out-of-Sample (Teste cego com mesmos parâmetros)
        res_out = rodar_simulacao_nautilus(p, df_out, tipo_ordem="MARKET", pivo=3, amp_min=0.20, degrau=0.65, run_id=f"{p}-OUT")

        # Walk-Forward Efficiency (WFE)
        # Relação entre o retorno do teste cego vs treino
        ret_in = res_in["retorno_pct"]
        ret_out = res_out["retorno_pct"]
        wfe = (ret_out / ret_in * 100) if ret_in > 0 else (100.0 if ret_out > 0 else 0.0)

        res_wfa.append({
            "Par": p,
            "In-Sample PnL": f"${res_in['pnl']:+,.2f} ({res_in['retorno_pct']:+.1f}%)",
            "Trades In": res_in["trades"],
            "Out-of-Sample PnL (Cego)": f"${res_out['pnl']:+,.2f} ({res_out['retorno_pct']:+.1f}%)",
            "Trades Out": res_out["trades"],
            "WFE (%)": f"{wfe:.1f}%",
            "_pnl_in": res_in["pnl"],
            "_pnl_out": res_out["pnl"],
        })

    df_wfa = pd.DataFrame(res_wfa)
    print("\n" + df_wfa.drop(columns=["_pnl_in", "_pnl_out"]).to_string(index=False))

    total_in = sum(r["_pnl_in"] for r in res_wfa)
    total_out = sum(r["_pnl_out"] for r in res_wfa)
    print(f"\n📈 PnL Consolidado In-Sample (Dias 1-7):   ${total_in:+,.2f}")
    print(f"🎯 PnL Consolidado Out-of-Sample (Dias 8-14): ${total_out:+,.2f}")

    print("\n" + "="*85)
    print("💰 ETAPA 2: COMPARATIVO CUSTO OPERACIONAL (TAKER VS MAKER RETESTE)")
    print("   Período Completo de 14 Dias")
    print("="*85)

    res_comparativo = []
    for p in pares:
        df_full = dados[p]
        # Modo Taker (Mercado)
        res_taker = rodar_simulacao_nautilus(p, df_full, tipo_ordem="MARKET", pivo=3, amp_min=0.20, degrau=0.65, run_id=f"{p}-TAKER")
        # Modo Maker (Limite no Reteste)
        res_maker = rodar_simulacao_nautilus(p, df_full, tipo_ordem="LIMIT_RETESTE", pivo=3, amp_min=0.20, degrau=0.65, run_id=f"{p}-MAKER")

        diferenca_pnl = res_maker["pnl"] - res_taker["pnl"]

        res_comparativo.append({
            "Par": p,
            "PnL Taker ($)": f"${res_taker['pnl']:+,.2f}",
            "Retorno Taker": f"{res_taker['retorno_pct']:+.1f}%",
            "Trades Taker": res_taker["trades"],
            "PnL Maker ($)": f"${res_maker['pnl']:+,.2f}",
            "Retorno Maker": f"{res_maker['retorno_pct']:+.1f}%",
            "Trades Maker": res_maker["trades"],
            "Diferencial Maker": f"${diferenca_pnl:+,.2f}",
            "_pnl_taker": res_taker["pnl"],
            "_pnl_maker": res_maker["pnl"],
        })

    df_comp = pd.DataFrame(res_comparativo)
    print("\n" + df_comp.drop(columns=["_pnl_taker", "_pnl_maker"]).to_string(index=False))

    tot_taker = sum(r["_pnl_taker"] for r in res_comparativo)
    tot_maker = sum(r["_pnl_maker"] for r in res_comparativo)
    print(f"\n💵 Total Carteira Taker (14 Dias): ${tot_taker:+,.2f} ({tot_taker/1000*100:+.1f}%)")
    print(f"💎 Total Carteira Maker (14 Dias): ${tot_maker:+,.2f} ({tot_maker/1000*100:+.1f}%)")
    print(f"✨ Vantagem do Modo Maker:         ${tot_maker - tot_taker:+,.2f}\n")


if __name__ == "__main__":
    executar_walk_forward_14dias()
