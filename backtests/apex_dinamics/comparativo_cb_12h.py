"""Comparativo de Circuit Breaker no Nautilus Trader:
- Cenário A: Janela de 6h (2 stops em até 6h -> pausa 6h)
- Cenário B: Janela de 12h (2 stops em até 12h -> pausa 6h)
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


def rodar_cenario(bars, instrument, bar_type, venue, cb_janela_horas, cb_duracao_pausa_horas=6.0, label=""):
    engine_cfg = BacktestEngineConfig(
        trader_id=TraderId(f"CB-{int(cb_janela_horas)}H"),
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
        cb_janela_horas=cb_janela_horas,
        cb_duracao_pausa_horas=cb_duracao_pausa_horas,
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
    metrics["label"] = label
    metrics["cb_janela"] = cb_janela_horas
    metrics["pausas_acionadas"] = strategy.suspenso_ate_ts > 0  # indicador simples
    return metrics, strategy


def main():
    print("=" * 85)
    print("🧪 COMPARAÇÃO NAUTILUS TRADER: JANELA CB 6 HORAS vs 12 HORAS")
    print("   Regra: 2 Losses consecutivos na janela -> Pausa de 6 Horas")
    print("   Ativo: SOL/USDT:USDT (KuCoin Futures 5x) | Período: 14 Dias")
    print("=" * 85)

    symbol = "SOL/USDT:USDT"
    df = fetch_kucoin_futures_5m_dias(symbol, dias=14)

    preco_medio = float(df["close"].mean())
    inst = criar_instrumento_kucoin_perp("SOLUSDT", preco_medio)
    bt = BarType.from_str(f"{inst.id}-5-MINUTE-LAST-EXTERNAL")
    bars = preparar_barras_nautilus(df, inst, bt)
    venue = Venue("KUCOIN")

    print("\n[1/2] Simulando Cenário A: Janela de 6 Horas...")
    res_6h, strat_6h = rodar_cenario(bars, inst, bt, venue, cb_janela_horas=6.0, label="Janela 6h (Original)")

    print("[2/2] Simulando Cenário B: Janela de 12 Horas...")
    res_12h, strat_12h = rodar_cenario(bars, inst, bt, venue, cb_janela_horas=12.0, label="Janela 12h (Novo)")

    print("\n" + "=" * 85)
    print("📊 RESULTADO COMPARATIVO DETALHADO")
    print("=" * 85)
    header = f"{'Métrica':<25} | {'Janela 6h':<18} | {'Janela 12h':<18} | {'Diferença':<15}"
    print(header)
    print("-" * 85)

    def print_linha(nome, v1, v2, formato="{:,.2f}", sufixo="", dif_formato="{:+,.2f}"):
        dif = v2 - v1
        s1 = formato.format(v1) + sufixo
        s2 = formato.format(v2) + sufixo
        sd = dif_formato.format(dif) + sufixo
        print(f"{nome:<25} | {s1:<18} | {s2:<18} | {sd:<15}")

    pnl_6h = res_6h["saldo_final"] - 1000.0
    pnl_12h = res_12h["saldo_final"] - 1000.0

    print_linha("Saldo Final ($)", res_6h["saldo_final"], res_12h["saldo_final"])
    print_linha("Lucro Líquido ($)", pnl_6h, pnl_12h)
    print_linha("Retorno Total (%)", res_6h["retorno_pct"], res_12h["retorno_pct"], sufixo="%")
    print_linha("Total de Trades", res_6h["trades"], res_12h["trades"], formato="{:d}", dif_formato="{:+d}")
    print_linha("Taxa de Acerto (%)", res_6h["winrate"], res_12h["winrate"], formato="{:.1f}", sufixo="%")
    print_linha("Profit Factor", res_6h["profit_factor"], res_12h["profit_factor"])
    print_linha("Max Drawdown (%)", res_6h["max_dd_pct"], res_12h["max_dd_pct"], sufixo="%")
    print_linha("Sharpe Ratio", res_6h["sharpe"], res_12h["sharpe"])
    print("=" * 85)


if __name__ == "__main__":
    main()
