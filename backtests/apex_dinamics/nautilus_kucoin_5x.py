"""Otimização e Backtest no Nautilus Trader para KuCoin Futures a 5x de Alavancagem.
Executa simulação de baixa latência em Cython/Rust com:
  - Venue: KUCOIN
  - Alavancagem: 5x
  - Taxas KuCoin: Maker 0.02% / Taker 0.06%
  - Comparativo nos ativos de maior liquidez: NEARUSDT, SOLUSDT, BTCUSDT, ETHUSDT
  - Avaliação de ordens MARKET vs LIMIT (Reteste)
"""
import sys
import os
import time
from decimal import Decimal
import pandas as pd
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "strategies")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "core")))

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue, TraderId
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.currencies import USDT, Currency
from nautilus_trader.model.objects import Price, Quantity, Money
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.data import BarType
from nautilus_trader.persistence.wranglers import BarDataWrangler

from strategies.nautilus_apex_strategy import (
    ApexDinamicsNautilusStrategy,
    ApexDinamicsNautilusConfig,
)
from backtests.apex_dinamics.gauntlet_optimizer import (
    preparar_barras_nautilus,
    calcular_metricas_institucionais,
)
from backtests.apex_dinamics.backtest_kucoin_7dias_top4_10x import fetch_kucoin_futures_5m_7dias


def criar_instrumento_kucoin_perp(simbolo_base: str, preco_medio: float) -> CryptoPerpetual:
    """Cria a especificação de contrato perpétuo da KuCoin Futures com alavancagem 5x e taxas KuCoin."""
    base_curr = Currency.from_str(simbolo_base.replace("USDT", ""))
    venue_kucoin = Venue("KUCOIN")

    return CryptoPerpetual(
        instrument_id=InstrumentId(Symbol(f"{simbolo_base}-PERP"), venue_kucoin),
        raw_symbol=Symbol(f"{simbolo_base}/USDT:USDT"),
        base_currency=base_curr,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=4 if preco_medio < 10 else 2,
        size_precision=2,
        price_increment=Price.from_str("0.0001" if preco_medio < 10 else "0.01"),
        size_increment=Quantity.from_str("0.01"),
        max_quantity=Quantity.from_str("100000.00"),
        min_quantity=Quantity.from_str("0.01"),
        max_notional=None,
        min_notional=Money(5.00, USDT),
        max_price=Price.from_str("1000000.00"),
        min_price=Price.from_str("0.0001"),
        margin_init=Decimal("0.20"),   # 5x de alavancagem (20% de margem)
        margin_maint=Decimal("0.10"),  # 10% margem manutenção
        maker_fee=Decimal("0.0002"),   # 0.02%
        taker_fee=Decimal("0.0006"),   # 0.06% KuCoin taker
        ts_event=0,
        ts_init=0,
    )


def avaliar_kucoin_nautilus(
    symbol: str,
    bars: list,
    instrument: CryptoPerpetual,
    bar_type: BarType,
    venue_kucoin: Venue,
    pivo: int = 3,
    amp_min: float = 0.35,
    degrau: float = 0.75,
    tipo_ordem: str = "MARKET",
    alavancagem: int = 5,
    run_id: str = "KC-5X",
) -> dict:
    engine_cfg = BacktestEngineConfig(
        trader_id=TraderId(f"KC-{run_id}"),
        logging=LoggingConfig(log_level="ERROR"),
    )
    engine = BacktestEngine(config=engine_cfg)
    engine.add_venue(
        venue=venue_kucoin,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(1000.0, USDT)],
        base_currency=USDT,
        default_leverage=Decimal(alavancagem),
    )
    engine.add_instrument(instrument)
    engine.add_data(bars)

    strat_cfg = ApexDinamicsNautilusConfig(
        instrument_id=str(instrument.id),
        bar_type=str(bar_type),
        alavancagem=alavancagem,
        fracao_banca_por_posicao=0.50,  # 50% de alocação (permite 2 posições simultâneas com margem confortável)
        janela_pivo=pivo,
        amplitude_min_pct=amp_min,
        folga_stop=0.12,
        fator_degrau_trailing=degrau,
        tipo_ordem=tipo_ordem,
        cb_duracao_pausa_horas=6.0,
    )
    strategy = ApexDinamicsNautilusStrategy(config=strat_cfg)
    engine.add_strategy(strategy)

    engine.run()

    acct_rep = engine.trader.generate_account_report(venue_kucoin)
    pos_rep = engine.trader.generate_positions_report()

    metrics = calcular_metricas_institucionais(acct_rep, pos_rep, saldo_inicial=1000.0)
    metrics["symbol"] = symbol
    metrics["tipo_ordem"] = tipo_ordem
    metrics["amp_min"] = amp_min
    metrics["alavancagem"] = alavancagem
    return metrics


def main():
    venue_kucoin = Venue("KUCOIN")
    pares_kucoin = {
        "NEARUSDT": "NEAR/USDT:USDT",
        "SOLUSDT": "SOL/USDT:USDT",
        "BTCUSDT": "BTC/USDT:USDT",
        "ETHUSDT": "ETH/USDT:USDT",
    }

    print("\n" + "=" * 90)
    print(" 🛡️ NAUTILUS TRADER - OTIMIZAÇÃO INSTITUCIONAL KUCOIN FUTURES (5X)")
    print("=" * 90)
    print("Testando candidatos nos últimos 7 dias na KuCoin (2.016 velas de 5m)...")

    dados_processados = {}
    for name, sym_kc in pares_kucoin.items():
        try:
            print(f"Baixando {name} ({sym_kc})...")
            df = fetch_kucoin_futures_5m_7dias(sym_kc, target_candles=2016)
            preco_medio = float(df["close"].mean())
            inst = criar_instrumento_kucoin_perp(name, preco_medio)
            bt = BarType.from_str(f"{inst.id}-5-MINUTE-LAST-EXTERNAL")
            bars = preparar_barras_nautilus(df, inst, bt)
            dados_processados[name] = (inst, bt, bars)
            print(f"  [OK] {name} pronto no Nautilus ({len(bars)} barras).")
        except Exception as e:
            print(f"  [ERRO] {name}: {e}")

    resultados = []
    print("\nSimulando cenários no Nautilus Trader...")
    for name, (inst, bt, bars) in dados_processados.items():
        for amp in [0.25, 0.35, 0.45]:
            for tipo_ordem in ["MARKET", "LIMIT"]:
                r = avaliar_kucoin_nautilus(
                    symbol=name,
                    bars=bars,
                    instrument=inst,
                    bar_type=bt,
                    venue_kucoin=venue_kucoin,
                    pivo=3,
                    amp_min=amp,
                    degrau=0.75,
                    tipo_ordem=tipo_ordem,
                    alavancagem=5,
                    run_id=f"{name}-{tipo_ordem}-{int(amp*100)}",
                )
                pnl = r["saldo_final"] - 1000.0
                resultados.append({
                    "Par": name,
                    "Ordem": tipo_ordem,
                    "Amp Min (%)": f"{amp:.2f}%",
                    "Retorno (%)": f"{r['retorno_pct']:+.2f}%",
                    "PnL ($)": f"${pnl:+,.2f}",
                    "Sharpe": f"{r['sharpe']:.2f}",
                    "Max DD (%)": f"{r['max_dd_pct']:.2f}%",
                    "Trades": r["trades"],
                    "Winrate (%)": f"{r['winrate']:.1f}%",
                    "_pnl": pnl,
                })

    df_res = pd.DataFrame(resultados).sort_values(by="_pnl", ascending=False)
    print("\n" + "=" * 90)
    print("🏆 RESULTADOS CONSOLIDADOS NO NAUTILUS TRADER (KUCOIN 5X)")
    print("=" * 90)
    print(df_res.drop(columns=["_pnl"]).to_string(index=False))


if __name__ == "__main__":
    main()
