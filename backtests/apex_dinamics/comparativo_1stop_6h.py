"""Comparativo no Nautilus Trader:
- Cenário Original: 2 Stops consecutivos -> Pausa de 6 Horas
- Novo Cenário: 1 Stop -> Pausa imediata de 6 Horas
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
from nautilus_trader.model.identifiers import Venue, TraderId
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.objects import Money
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.data import BarType

from strategies.nautilus_apex_strategy import (
    ApexDinamicsNautilusStrategy,
    ApexDinamicsNautilusConfig,
)
from backtests.apex_dinamics.gauntlet_optimizer import (
    preparar_barras_nautilus,
    calcular_metricas_institucionais,
)
from backtests.apex_dinamics.backtest_lite38_14dias import fetch_kucoin_futures_5m_dias
from backtests.apex_dinamics.nautilus_kucoin_5x import criar_instrumento_kucoin_perp


def rodar_teste(bars, instrument, bar_type, venue, cb_max_losses: int, pausa_horas: float = 6.0, run_id: str = ""):
    engine_cfg = BacktestEngineConfig(
        trader_id=TraderId(f"CB-{run_id}"),
        logging=LoggingConfig(log_level="ERROR"),
    )
    engine = BacktestEngine(config=engine_cfg)
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(1000.0, USDT)],
        base_currency=USDT,
        default_leverage=Decimal(5),
    )
    engine.add_instrument(instrument)
    engine.add_data(bars)

    strat_cfg = ApexDinamicsNautilusConfig(
        instrument_id=str(instrument.id),
        bar_type=str(bar_type),
        alavancagem=5,
        fracao_banca_por_posicao=0.333,
        janela_pivo=4,
        amplitude_min_pct=0.40,
        folga_stop=0.10,
        fator_degrau_trailing=1.00,
        tipo_ordem="MARKET",
        cb_janela_horas=24.0,  # janela ampla para pegar o stop
        cb_duracao_pausa_horas=pausa_horas,
        cb_max_losses=cb_max_losses,
    )
    strategy = ApexDinamicsNautilusStrategy(config=strat_cfg)
    engine.add_strategy(strategy)

    inicio = time.time()
    engine.run()
    duracao = time.time() - inicio

    relatorio_conta = engine.trader.generate_account_report(venue)
    relatorio_posicoes = engine.trader.generate_positions_report()

    metrics = calcular_metricas_institucionais(relatorio_conta, relatorio_posicoes, saldo_inicial=1000.0)
    metrics["duracao_exec"] = duracao
    metrics["cb_max_losses"] = cb_max_losses
    metrics["strategy"] = strategy
    return metrics


def main():
    print("=" * 85)
    print("🧪 COMPARAÇÃO NAUTILUS TRADER: 2 STOPS vs 1 STOP COM PAUSA DE 6 HORAS")
    print("   Ativo: SOL/USDT:USDT (KuCoin Futures 5x) | Período: 14 Dias")
    print("=" * 85)

    symbol = "SOL/USDT:USDT"
    df = fetch_kucoin_futures_5m_dias(symbol, dias=14)

    preco_medio = float(df["close"].mean())
    inst = criar_instrumento_kucoin_perp("SOLUSDT", preco_medio)
    bt = BarType.from_str(f"{inst.id}-5-MINUTE-LAST-EXTERNAL")
    bars = preparar_barras_nautilus(df, inst, bt)
    venue = Venue("KUCOIN")

    print("\n[1/2] Simulando Cenário Padrão: 2 Stops -> Pausa de 6h...")
    res_2stops = rodar_teste(bars, inst, bt, venue, cb_max_losses=2, pausa_horas=6.0, run_id="2STOPS")

    print("[2/2] Simulando Novo Cenário: 1 Stop -> Pausa de 6h...")
    res_1stop = rodar_teste(bars, inst, bt, venue, cb_max_losses=1, pausa_horas=6.0, run_id="1STOP")

    print("\n" + "=" * 85)
    print("📊 RESULTADO COMPARATIVO DETALHADO")
    print("=" * 85)
    header = f"{'Métrica':<25} | {'2 Stops (Padrão)':<18} | {'1 Stop (Novo)':<18} | {'Diferença':<15}"
    print(header)
    print("-" * 85)

    def print_linha(nome, v1, v2, formato="{:,.2f}", sufixo="", dif_formato="{:+,.2f}"):
        dif = v2 - v1
        s1 = formato.format(v1) + sufixo
        s2 = formato.format(v2) + sufixo
        sd = dif_formato.format(dif) + sufixo
        print(f"{nome:<25} | {s1:<18} | {s2:<18} | {sd:<15}")

    pnl_2s = res_2stops["saldo_final"] - 1000.0
    pnl_1s = res_1stop["saldo_final"] - 1000.0

    print_linha("Saldo Final ($)", res_2stops["saldo_final"], res_1stop["saldo_final"])
    print_linha("Lucro Líquido ($)", pnl_2s, pnl_1s)
    print_linha("Retorno Total (%)", res_2stops["retorno_pct"], res_1stop["retorno_pct"], sufixo="%")
    print_linha("Total de Trades", res_2stops["trades"], res_1stop["trades"], formato="{:d}", dif_formato="{:+d}")
    print_linha("Taxa de Acerto (%)", res_2stops["winrate"], res_1stop["winrate"], formato="{:.1f}", sufixo="%")
    print_linha("Profit Factor", res_2stops["profit_factor"], res_1stop["profit_factor"])
    print_linha("Max Drawdown (%)", res_2stops["max_dd_pct"], res_1stop["max_dd_pct"], sufixo="%")
    print_linha("Sharpe Ratio", res_2stops["sharpe"], res_1stop["sharpe"])
    print("=" * 85)


if __name__ == "__main__":
    main()
