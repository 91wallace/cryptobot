import json
import urllib.request
from decimal import Decimal
import pandas as pd

from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.examples.strategies.ema_cross import EMACross, EMACrossConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider


def fetch_binance_klines(symbol="BTCUSDT", interval="15m", limit=288):
    # 72 hours = 72 * 4 = 288 candles of 15m
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
    
    records = []
    for item in data:
        # Binance kline structure:
        # [open_time, open, high, low, close, volume, close_time, quote_asset_volume, num_trades, ...]
        records.append({
            "timestamp": pd.to_datetime(item[0], unit="ms", utc=True),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
        })
    df = pd.DataFrame(records).set_index("timestamp")
    return df


def run_backtest():
    print("Baixando dados de 72h (288 candles de 15m) do par BTCUSDT da Binance...")
    df = fetch_binance_klines("BTCUSDT", "15m", limit=288)
    print(f"Candles obtidos: {len(df)}")
    print(f"Periodo: {df.index[0]} ate {df.index[-1]}")

    # Configuração do Engine
    engine_config = BacktestEngineConfig(trader_id=TraderId("BACKTESTER-001"))
    engine = BacktestEngine(config=engine_config)

    # Configuração da Exchange Simulada (Binance Cash / Netting)
    BINANCE = Venue("BINANCE")
    engine.add_venue(
        venue=BINANCE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=[Money(100_000, USDT)],
    )

    # Instrumento
    instrument = TestInstrumentProvider.btcusdt_binance()
    engine.add_instrument(instrument)

    # Bar Type: 15-MINUTE EXTERNAL
    bar_type = BarType.from_str(f"{instrument.id}-15-MINUTE-LAST-EXTERNAL")
    from nautilus_trader.model.objects import Price, Quantity
    bars = []
    for ts, row in df.iterrows():
        ts_ns = int(ts.value)
        bar = Bar(
            bar_type=bar_type,
            open=Price(row["open"], precision=instrument.price_precision),
            high=Price(row["high"], precision=instrument.price_precision),
            low=Price(row["low"], precision=instrument.price_precision),
            close=Price(row["close"], precision=instrument.price_precision),
            volume=Quantity(row["volume"], precision=instrument.size_precision),
            ts_event=ts_ns,
            ts_init=ts_ns,
        )
        bars.append(bar)

    engine.add_data(bars)

    # Configuração da Estratégia EMA Cross (Fast 10, Slow 20)
    strategy_config = EMACrossConfig(
        instrument_id=instrument.id,
        bar_type=bar_type,
        trade_size=Decimal("0.1"),
        fast_ema_period=10,
        slow_ema_period=20,
    )
    strategy = EMACross(config=strategy_config)
    engine.add_strategy(strategy=strategy)

    print("Executando backtest...")
    engine.run()

    # Relatórios
    positions_report = engine.trader.generate_positions_report()
    result = engine.get_result()

    print("\n" + "=" * 50)
    print("           RESULTADO DO BACKTEST")
    print("=" * 50)
    print(f"Periodo avaliado : 72 horas ({len(df)} candles de 15m)")
    print(f"Par              : {instrument.id}")
    print(f"Saldo Inicial    : 100,000 USDT")
    
    if positions_report.empty:
        print("\nNenhuma posicao fechada foi gerada no periodo.")
        return

    # Processamento de métricas
    total_entries = len(positions_report)
    
    # Extrair realized_pnl numérico
    def parse_pnl(val):
        if hasattr(val, "as_double"):
            return val.as_double()
        if isinstance(val, (int, float)):
            return float(val)
        val_str = str(val).split()[0]
        try:
            return float(val_str)
        except ValueError:
            return 0.0

    pnl_series = positions_report["realized_pnl"].apply(parse_pnl)
    wins = (pnl_series > 0).sum()
    losses = (pnl_series < 0).sum()
    ties = (pnl_series == 0).sum()
    winrate = (wins / total_entries) * 100.0 if total_entries > 0 else 0.0
    total_profit = pnl_series.sum()

    print(f"\nQuantidade de Entradas (Posicoes) : {total_entries}")
    print(f"Vitorias (Wins)                   : {wins}")
    print(f"Derrotas (Losses)                 : {losses}")
    print(f"Empates                           : {ties}")
    print(f"Winrate                           : {winrate:.2f}%")
    print(f"Total Profit Líquido              : {total_profit:+.4f} USDT")
    print("=" * 50)
    
    print("\nDetalhes das posições:")
    display_cols = [c for c in ["side", "avg_px_open", "avg_px_close", "realized_pnl", "realized_return"] if c in positions_report.columns]
    print(positions_report[display_cols].to_string())


if __name__ == "__main__":
    run_backtest()
