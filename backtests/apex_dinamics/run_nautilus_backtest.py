"""Execução de Backtest de Alta Fidelidade da Apex Dinamics™ no Nautilus Trader.

Utiliza:
  - Motor de simulação de baixa latência em Rust/Cython
  - Parâmetros oficiais do Gauntlet Loop: Pivô 3 | Amp Mín 0.30% | Degrau 0.75x
  - Conta de Margem com alavancagem 10x
  - Taxas reais da Binance Futures (Maker 0.02% / Taker 0.04%)
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
from backtests.apex_dinamics.backtest_7dias_top4_10x import fetch_binance_futures_5m_7dias


def criar_instrumento_perp(simbolo: str, preco_medio: float) -> CryptoPerpetual:
    """Cria a especificação de contrato perpétuo da Binance Futures."""
    simbolo_limpo = simbolo.replace("USDT", "")
    base_curr = Currency.from_str(simbolo_limpo)

    return CryptoPerpetual(
        instrument_id=InstrumentId(Symbol(f"{simbolo}-PERP"), Venue("BINANCE")),
        raw_symbol=Symbol(simbolo),
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
        margin_init=Decimal("0.10"),  # 10x de alavancagem
        margin_maint=Decimal("0.05"),
        maker_fee=Decimal("0.0002"),  # 0.02%
        taker_fee=Decimal("0.0004"),  # 0.04%
        ts_event=0,
        ts_init=0,
    )


def executar_backtest_nautilus(
    symbol: str = "SOLUSDT",
    dias: int = 7,
    saldo_inicial_usdt: float = 1000.0,
    alavancagem: int = 10,
    janela_pivo: int = 3,
    amplitude_min_pct: float = 0.30,
    fator_degrau_trailing: float = 0.75,
    tipo_ordem: str = "MARKET",
):
    print(f"\n{'='*80}")
    print(f"🚀 INICIANDO AUDITORIA NAUTILUS TRADER - {symbol} ({dias} Dias)")
    print("   Setup Campeão: Pivô 3 | Amp Mín 0.30% | Degrau 0.75x | 10x Alavancagem")
    print(f"{'='*80}")

    # 1. Obter dados de mercado
    candles = dias * 288
    print(f"📥 Coletando {candles} velas de 5m da Binance Futures...")
    df = fetch_binance_futures_5m_7dias(symbol, target_candles=candles)
    preco_medio = float(df["close"].mean())

    # 2. Configurar instrumento e BarType
    instrument = criar_instrumento_perp(symbol, preco_medio)
    bar_type = BarType.from_str(f"{instrument.id}-5-MINUTE-LAST-EXTERNAL")

    # 3. Preparar DataFrame com buffer editável
    df_wrangler = df.copy()
    if "open_time" in df_wrangler.columns:
        df_wrangler["timestamp"] = pd.to_datetime(df_wrangler["open_time"], unit="ms", utc=True)
        df_wrangler = df_wrangler.set_index("timestamp")

    cols_necessarias = ["open", "high", "low", "close", "volume"]
    for c in cols_necessarias:
        df_wrangler[c] = df_wrangler[c].astype(np.float64).values.copy()
    df_wrangler = df_wrangler[cols_necessarias]

    wrangler = BarDataWrangler(bar_type, instrument)
    bars = wrangler.process(df_wrangler)
    print(f"✅ {len(bars)} barras processadas e validadas para o motor Nautilus.")

    # 4. Configurar Engine
    engine_cfg = BacktestEngineConfig(
        trader_id=TraderId("APEX-BACKTESTER-001"),
        logging=LoggingConfig(log_level="ERROR"),
    )
    engine = BacktestEngine(config=engine_cfg)

    # 5. Adicionar Venue (BINANCE) com conta de Margem
    venue_binance = Venue("BINANCE")
    engine.add_venue(
        venue=venue_binance,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(saldo_inicial_usdt, USDT)],
        base_currency=USDT,
        default_leverage=Decimal(alavancagem),
    )
    engine.add_instrument(instrument)
    engine.add_data(bars)

    # 6. Adicionar Estratégia Apex Dinamics
    strat_cfg = ApexDinamicsNautilusConfig(
        instrument_id=str(instrument.id),
        bar_type=str(bar_type),
        alavancagem=alavancagem,
        fracao_banca_por_posicao=0.333,
        janela_pivo=janela_pivo,
        amplitude_min_pct=amplitude_min_pct,
        folga_stop=0.10,
        fator_degrau_trailing=fator_degrau_trailing,
        tipo_ordem=tipo_ordem,
    )
    strategy = ApexDinamicsNautilusStrategy(config=strat_cfg)
    engine.add_strategy(strategy)

    # 7. Executar Simulação
    inicio_sim = time.time()
    print("⚡ Executando simulação orientada a eventos em Rust/Cython...")
    engine.run()
    tempo_exec = time.time() - inicio_sim
    print(f"🏁 Concluído em {tempo_exec:.3f} segundos!\n")

    # 8. Extrair Relatórios Oficiais e Métricas
    relatorio_conta = engine.trader.generate_account_report(venue_binance)
    relatorio_posicoes = engine.trader.generate_positions_report()
    relatorio_fills = engine.trader.generate_order_fills_report()

    from backtests.apex_dinamics.gauntlet_optimizer import calcular_metricas_institucionais
    metrics = calcular_metricas_institucionais(relatorio_conta, relatorio_posicoes, saldo_inicial=saldo_inicial_usdt)

    saldo_final = metrics["saldo_final"]
    pnl_total = saldo_final - saldo_inicial_usdt
    rentabilidade_pct = metrics["retorno_pct"]

    print("📊 [RESUMO DE PERFORMANCE INSTITUCIONAL]")
    print(f"   Saldo Inicial:    ${saldo_inicial_usdt:,.2f} USDT")
    print(f"   Saldo Final:      ${saldo_final:,.2f} USDT")
    print(f"   PnL Líquido:      ${pnl_total:+,.2f} USDT ({rentabilidade_pct:+.2f}%)")
    print(f"   Sharpe Ratio:     {metrics['sharpe']:.2f}")
    print(f"   Profit Factor:    {metrics['profit_factor']:.2f}")
    print(f"   Max Drawdown:     {metrics['max_dd_pct']:.2f}%")
    print(f"   Total de Trades:  {metrics['trades']}")
    print(f"   Taxa de Acerto:   {metrics['winrate']:.1f}%")

    if not relatorio_fills.empty:
        total_comissoes = float(relatorio_fills["commissions"].apply(
            lambda c: float(c.as_double()) if hasattr(c, "as_double") else 0.0
        ).sum()) if "commissions" in relatorio_fills.columns else 0.0
        print(f"   Comissões Pagas:  ${total_comissoes:,.2f} USDT")

    print(f"{'='*80}\n")

    return {
        "symbol": symbol,
        "saldo_inicial": saldo_inicial_usdt,
        "saldo_final": saldo_final,
        "pnl_liquido": pnl_total,
        "retorno_pct": rentabilidade_pct,
        "metrics": metrics,
        "engine": engine,
    }


if __name__ == "__main__":
    par = sys.argv[1] if len(sys.argv) > 1 else "SOLUSDT"
    dias_teste = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    executar_backtest_nautilus(par, dias=dias_teste)
