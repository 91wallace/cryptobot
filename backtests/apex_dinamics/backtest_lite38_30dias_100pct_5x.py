"""Backtest Oficial de 30 Dias (1 Mês) no Nautilus Trader:
Estratégia: Rompimento Trailingstop Lite-3.8
Configuração:
  - Capital Utilizado: 100% da Banca a cada entrada (Juros Compostos)
  - Alavancagem: 5x
  - Exchange: KuCoin Futures (USDT-M)
  - Pares: SOL/USDT, BTC/USDT, ETH/USDT
"""
import sys
import os
import time
from decimal import Decimal
import pandas as pd
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "strategies"))
sys.path.insert(0, os.path.join(BASE_DIR, "core"))

import ccxt
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue, TraderId
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.currencies import USDT, Currency
from nautilus_trader.model.objects import Price, Quantity, Money
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.data import Bar, BarType

from strategies.nautilus_apex_strategy import (
    ApexDinamicsNautilusStrategy,
    ApexDinamicsNautilusConfig,
)
from backtests.apex_dinamics.gauntlet_optimizer import (
    calcular_metricas_institucionais,
    preparar_barras_nautilus,
)


def fetch_kucoin_futures_5m_30dias(symbol: str = "SOL/USDT:USDT", dias: int = 30) -> pd.DataFrame:
    """Busca candles de 5m da KuCoin Futures API com cache local em CSV."""
    cache_file = os.path.join(BASE_DIR, "data", f"kucoin_{symbol.replace('/', '_').replace(':', '_')}_5m_{dias}dias.csv")
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)

    if os.path.exists(cache_file):
        idade = time.time() - os.path.getmtime(cache_file)
        if idade < 86400:
            print(f"📦 Carregando dados históricos do cache local: {cache_file}")
            df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            return df

    target_candles = dias * 288
    kf = ccxt.kucoinfutures({"enableRateLimit": True, "timeout": 25000})
    now_ms = int(time.time() * 1000)
    total_ms = target_candles * 5 * 60 * 1000
    start_ms = now_ms - total_ms

    all_ohlcv = []
    curr_since = start_ms

    print(f"📥 Baixando {target_candles} velas de 5m ({dias} dias) para {symbol} da KuCoin Futures...")
    while len(all_ohlcv) < target_candles:
        try:
            ohlcv = kf.fetch_ohlcv(symbol, timeframe="5m", since=curr_since, limit=200)
            if not ohlcv:
                break
            all_ohlcv.extend(ohlcv)
            curr_since = ohlcv[-1][0] + (5 * 60 * 1000)
            if curr_since >= now_ms:
                break
            time.sleep(0.08)
        except Exception as e:
            print(f"Aviso de requisição: {e}")
            time.sleep(0.5)
            break

    unique_map = {item[0]: item for item in all_ohlcv}
    sorted_items = [unique_map[k] for k in sorted(unique_map.keys())]

    if len(sorted_items) > target_candles:
        sorted_items = sorted_items[-target_candles:]

    records = []
    for item in sorted_items:
        records.append({
            "timestamp": pd.to_datetime(item[0], unit="ms", utc=True),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
        })

    df = pd.DataFrame(records).set_index("timestamp")
    df.to_csv(cache_file)
    print(f"✅ Total carregado e salvo em cache: {len(df)} velas de 5m ({df.index[0]} até {df.index[-1]}).")
    return df


def criar_instrumento_kucoin_perp_5x(simbolo_base: str, preco_medio: float) -> CryptoPerpetual:
    """Cria instrumento CryptoPerpetual para KuCoin Futures a 5x (20% margem inicial)."""
    base_curr = Currency.from_str(simbolo_base.replace("USDT", ""))
    venue_kucoin = Venue("KUCOIN")

    # Precisão de preço e tamanho adaptativa por ativo
    price_prec = 2
    size_prec = 2
    min_qty = "0.01"
    if "BTC" in simbolo_base:
        price_prec = 1
        size_prec = 3
        min_qty = "0.001"
    elif "ETH" in simbolo_base:
        price_prec = 2
        size_prec = 2
        min_qty = "0.01"

    return CryptoPerpetual(
        instrument_id=InstrumentId(Symbol(f"{simbolo_base}-PERP-5X"), venue_kucoin),
        raw_symbol=Symbol(f"{simbolo_base}/USDT:USDT"),
        base_currency=base_curr,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=price_prec,
        size_precision=size_prec,
        price_increment=Price.from_str("0.1" if price_prec == 1 else "0.01"),
        size_increment=Quantity.from_str(min_qty),
        max_quantity=Quantity.from_str("100000.00"),
        min_quantity=Quantity.from_str(min_qty),
        max_notional=None,
        min_notional=Money(5.00, USDT),
        max_price=Price.from_str("1000000.00"),
        min_price=Price.from_str("0.0001"),
        margin_init=Decimal("0.20"),   # 5x de alavancagem = 20% de margem inicial
        margin_maint=Decimal("0.10"),  # 10% margem de manutenção
        maker_fee=Decimal("0.0002"),   # 0.02%
        taker_fee=Decimal("0.0006"),   # 0.06% KuCoin taker
        ts_event=0,
        ts_init=0,
    )


def executar_backtest_5x(symbol_pair: str = "SOL/USDT:USDT", nome_ativo: str = "SOLUSDT"):
    print("\n" + "=" * 90)
    print(f" 🚀 BACKTEST NAUTILUS TRADER - 1 MÊS (30 DIAS) | 100% CAPITAL (COMPOUND) | ALAVANCAGEM 5X")
    print("=" * 90)
    print(f"Estratégia        : Rompimento Trailingstop Lite-3.8")
    print(f"Exchange          : KuCoin Futures (USDT-M)")
    print(f"Par Selecionado   : {symbol_pair}")
    print(f"Alavancagem       : 5x")
    print(f"Gestão de Banca   : 100% do Capital Flutuante por Operação (Juros Compostos)")
    print(f"Capital Inicial   : $ 1.000,00 USDT")
    print(f"Pivô / Amp / Deg  : Pivô 4 | Amplitude Mín 0.40% | Degrau 1.00x")
    print(f"Circuit Breaker   : Pausa de 6.0h imediata após 1 stop (Proteção Ativa)")
    print(f"Taxas KuCoin      : Maker 0.02% / Taker 0.06%")
    print("-" * 90)

    df = fetch_kucoin_futures_5m_30dias(symbol_pair, dias=30)
    preco_medio = float(df["close"].mean())
    inst = criar_instrumento_kucoin_perp_5x(nome_ativo, preco_medio)
    bt = BarType.from_str(f"{inst.id}-5-MINUTE-LAST-EXTERNAL")
    bars = preparar_barras_nautilus(df, inst, bt)
    venue_kc = Venue("KUCOIN")

    saldo_inicial = 1000.0
    alavancagem = 5
    cb_pausa_horas = 6.0

    print(f"\nIniciando simulação no Nautilus Trader ({len(bars)} barras de 5m)...")

    engine_cfg = BacktestEngineConfig(
        trader_id=TraderId(f"LITE38-100PCT-5X-{nome_ativo}"),
        logging=LoggingConfig(log_level="ERROR"),
    )
    engine = BacktestEngine(config=engine_cfg)
    engine.add_venue(
        venue=venue_kc,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(saldo_inicial, USDT)],
        base_currency=USDT,
        default_leverage=Decimal(alavancagem),
    )
    engine.add_instrument(inst)
    engine.add_data(bars)

    strat_cfg = ApexDinamicsNautilusConfig(
        instrument_id=str(inst.id),
        bar_type=str(bt),
        alavancagem=alavancagem,
        fracao_banca_por_posicao=1.00,  # 100% do capital (juros compostos a cada trade)
        janela_pivo=4,
        amplitude_min_pct=0.40,
        folga_stop=0.10,
        fator_degrau_trailing=1.00,
        tipo_ordem="MARKET",
        cb_duracao_pausa_horas=cb_pausa_horas,
        cb_max_losses=1,
        cb_janela_horas=24.0,
    )
    strategy = ApexDinamicsNautilusStrategy(config=strat_cfg)
    engine.add_strategy(strategy)

    inicio = time.time()
    engine.run()
    tempo_exec = time.time() - inicio

    acct_rep = engine.trader.generate_account_report(venue_kc)
    pos_rep = engine.trader.generate_positions_report()

    r = calcular_metricas_institucionais(acct_rep, pos_rep, saldo_inicial=saldo_inicial)
    pnl = r["saldo_final"] - saldo_inicial

    print("\n" + "=" * 90)
    print(f" 🏆 RESULTADOS OFICIAIS ({nome_ativo} - 30 DIAS - 5X - 100% BANCA JUROS COMPOSTOS)")
    print("=" * 90)
    print(f"Estratégia                    : Rompimento Trailingstop Lite-3.8")
    print(f"Ativo                         : {nome_ativo} (KuCoin Futures)")
    print(f"Período                       : 30 Dias ({len(bars)} velas de 5m)")
    print(f"Tempo de Execução (Nautilus)  : {tempo_exec:.2f}s")
    print(f"Capital Inicial               : $ {saldo_inicial:,.2f} USDT")
    print(f"Capital Utilizado             : 100.0% da Banca por Trade (Juros Compostos)")
    print(f"Alavancagem                   : 5x")
    print(f"Saldo Final                   : $ {r['saldo_final']:,.2f} USDT")
    print(f"LUCRO LÍQUIDO TOTAL ($)       : $ {pnl:>+,.2f} USDT")
    print(f"RENTABILIDADE TOTAL (%)       : {r['retorno_pct']:>+8.2f}%")
    print(f"Índice de Sharpe (Anualizado) : {r['sharpe']:.2f}")
    print(f"Profit Factor                 : {r['profit_factor']:.2f}")
    print(f"Rebaixamento Máximo (Max DD)  : {r['max_dd_pct']:.2f}%")
    print(f"Total de Trades               : {r['trades']}")
    print(f"Taxa de Acerto (Winrate)      : {r['winrate']:.1f}%")
    print("=" * 90 + "\n")

    return r


if __name__ == "__main__":
    executar_backtest_5x("SOL/USDT:USDT", "SOLUSDT")
