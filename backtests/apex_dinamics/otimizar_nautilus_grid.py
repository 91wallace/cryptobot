"""Otimização Paramétrica da Apex Dinamics™ no Nautilus Trader.

Executa simulação quantitativa combinatória em lote nos dados reais de 7 dias da Binance Futures.
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


def otimizar_grade_nautilus(
    symbol: str = "SOLUSDT",
    dias: int = 7,
    saldo_inicial: float = 1000.0,
    alavancagem: int = 10,
    grade_pivos: list[int] = [3, 4],
    grade_amplitudes: list[float] = [0.15, 0.20, 0.30],
    grade_degraus: list[float] = [0.35, 0.50, 0.65],
):
    print(f"\n{'='*85}")
    print(f"🔬 OTIMIZAÇÃO PARAMÉTRICA NAUTILUS TRADER - {symbol} ({dias} Dias)")
    print(f"{'='*85}")

    candles = dias * 288
    print(f"📥 Baixando histórico da Binance Futures ({candles} velas de 5m)...")
    df = fetch_binance_futures_5m_7dias(symbol, target_candles=candles)
    preco_medio = float(df["close"].mean())

    instrument = criar_instrumento_perp(symbol, preco_medio)
    bar_type = BarType.from_str(f"{instrument.id}-5-MINUTE-LAST-EXTERNAL")
    venue_binance = Venue("BINANCE")

    bars = preparar_barras_nautilus(df, instrument, bar_type)
    print(f"✅ {len(bars)} barras processadas no formato institucional.")

    total_combinacoes = len(grade_pivos) * len(grade_amplitudes) * len(grade_degraus)
    print(f"🔄 Executando {total_combinacoes} backtests completos em eventos de alta fidelidade...\n")

    resultados = []
    idx = 0
    tempo_inicio = time.time()

    for pivo in grade_pivos:
        for amp in grade_amplitudes:
            for degrau in grade_degraus:
                idx += 1

                engine_cfg = BacktestEngineConfig(
                    trader_id=TraderId(f"OPT-{idx:03d}"),
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
                    amplitude_min_pct=amp,
                    folga_stop=0.10,
                    fator_degrau_trailing=degrau,
                )
                strategy = ApexDinamicsNautilusStrategy(config=strat_cfg)
                engine.add_strategy(strategy)

                # Executa
                engine.run()

                saldo_final = float(engine.portfolio.account(venue_binance).balance_total(USDT).as_double())
                pnl = saldo_final - saldo_inicial
                ret_pct = (pnl / saldo_inicial) * 100

                pos_rep = engine.trader.generate_positions_report()
                total_trades = len(pos_rep) if not pos_rep.empty else 0

                resultados.append({
                    "Par": symbol,
                    "Pivô": pivo,
                    "Amp Min (%)": f"{amp:.2f}%",
                    "Degrau": f"{degrau:.2f}x",
                    "Trades": total_trades,
                    "Saldo Final ($)": f"${saldo_final:,.2f}",
                    "PnL ($)": f"{pnl:+,.2f}",
                    "Retorno (%)": f"{ret_pct:+.2f}%",
                    "_pnl_raw": pnl,
                })

                print(
                    f"[{idx:02d}/{total_combinacoes:02d}] Pivô: {pivo} | Amp: {amp:.2f}% | Degrau: {degrau:.2f}x "
                    f"-> Trades: {total_trades:2d} | PnL: ${pnl:+7.2f} ({ret_pct:+6.2f}%)"
                )

    tempo_total = time.time() - tempo_inicio
    print(f"\n🏁 Grid Search finalizado em {tempo_total:.2f} segundos! ({tempo_total/total_combinacoes:.2f}s por teste)")

    df_res = pd.DataFrame(resultados).sort_values(by="_pnl_raw", ascending=False).drop(columns=["_pnl_raw"])

    print(f"\n🏆 [RANKING TOP CONFIGURAÇÕES NO NAUTILUS TRADER - {symbol}]")
    print(df_res.head(8).to_string(index=False))

    return df_res


if __name__ == "__main__":
    par_alvo = sys.argv[1] if len(sys.argv) > 1 else "SOLUSDT"
    otimizar_grade_nautilus(
        symbol=par_alvo,
        dias=7,
        grade_pivos=[3, 4],
        grade_amplitudes=[0.15, 0.20, 0.30],
        grade_degraus=[0.35, 0.50, 0.65],
    )
