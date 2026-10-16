"""Motor do Gauntlet Loop: Otimização exaustiva e cálculo de métricas institucionais no Nautilus Trader.

Métricas auditadas:
  - Retorno Semanal (%)
  - Sharpe Ratio
  - Profit Factor
  - Max Drawdown (%)
  - Taxa de Acerto (Win Rate %)
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


def calcular_metricas_institucionais(account_report: pd.DataFrame, positions_report: pd.DataFrame, saldo_inicial: float = 1000.0) -> dict:
    """Calcula Sharpe Ratio, Max Drawdown, Profit Factor e Retorno a partir dos relatórios Nautilus."""
    if account_report.empty:
        return {"retorno_pct": 0.0, "sharpe": 0.0, "profit_factor": 0.0, "max_dd_pct": 0.0, "trades": 0, "winrate": 0.0}

    # Série de saldo
    saldos = account_report["total"].astype(float).values
    if len(saldos) == 0:
        return {"retorno_pct": 0.0, "sharpe": 0.0, "profit_factor": 0.0, "max_dd_pct": 0.0, "trades": 0, "winrate": 0.0}

    saldo_final = saldos[-1]
    retorno_pct = ((saldo_final - saldo_inicial) / saldo_inicial) * 100

    # Drawdown Máximo
    picos = np.maximum.accumulate(saldos)
    drawdowns = (picos - saldos) / picos
    max_dd_pct = float(np.max(drawdowns)) * 100 if len(drawdowns) > 0 else 0.0

    # Retornos periódicos para cálculo do Sharpe
    retornos_pct = pd.Series(saldos).pct_change().dropna()
    if len(retornos_pct) > 1 and retornos_pct.std() > 0:
        # Fator anualizado considerando passos de 5m (288 por dia, 365 dias = 105.120)
        sharpe = float((retornos_pct.mean() / retornos_pct.std()) * np.sqrt(288 * 7))
    else:
        sharpe = 0.0

    # Profit Factor e Win Rate via histórico de posições
    total_trades = 0
    winrate = 0.0
    profit_factor = 0.0

    if not positions_report.empty and "realized_pnl" in positions_report.columns:
        def _parse_val(x):
            if hasattr(x, "as_double"):
                return float(x.as_double())
            s = str(x).replace("USDT", "").strip()
            try:
                return float(s)
            except Exception:
                return 0.0

        serie = positions_report["realized_pnl"].apply(_parse_val)
        pnls = serie.diff().dropna().values
        pnls = pnls[pnls != 0]
        total_trades = len(pnls)

        if total_trades > 0:
            wins = pnls[pnls > 0]
            losses = pnls[pnls < 0]
            winrate = (len(wins) / total_trades) * 100
            soma_ganhos = float(np.sum(wins)) if len(wins) > 0 else 0.0
            soma_perdas = abs(float(np.sum(losses))) if len(losses) > 0 else 0.0

            if soma_perdas > 0:
                profit_factor = soma_ganhos / soma_perdas
            else:
                profit_factor = 99.0 if soma_ganhos > 0 else 0.0

    return {
        "saldo_final": saldo_final,
        "retorno_pct": retorno_pct,
        "sharpe": sharpe,
        "profit_factor": profit_factor,
        "max_dd_pct": max_dd_pct,
        "trades": total_trades,
        "winrate": winrate,
    }


def avaliar_candidato_nautilus(
    symbol: str,
    bars: list,
    instrument,
    bar_type,
    venue_binance,
    pivo: int,
    amp_min: float,
    degrau: float,
    tipo_ordem: str = "MARKET",
    cb_pausa_horas: float = 6.0,
    saldo_inicial: float = 1000.0,
    alavancagem: int = 10,
    run_id: str = "CANDIDATE",
) -> dict:
    engine_cfg = BacktestEngineConfig(
        trader_id=TraderId(f"GAUNTLET-{run_id}"),
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
        cb_duracao_pausa_horas=cb_pausa_horas,
    )
    strategy = ApexDinamicsNautilusStrategy(config=strat_cfg)
    engine.add_strategy(strategy)

    engine.run()

    acct_rep = engine.trader.generate_account_report(venue_binance)
    pos_rep = engine.trader.generate_positions_report()

    metrics = calcular_metricas_institucionais(acct_rep, pos_rep, saldo_inicial=saldo_inicial)
    metrics["pivo"] = pivo
    metrics["amp_min"] = amp_min
    metrics["degrau"] = degrau
    metrics["tipo_ordem"] = tipo_ordem
    metrics["cb_pausa"] = cb_pausa_horas
    metrics["symbol"] = symbol

    return metrics


def rodar_gauntlet_bloco(symbol: str = "SOLUSDT", dias: int = 7):
    """Executa varredura profunda com julgamento cego contra a Régua B."""
    print(f"\n{'='*85}")
    print(f"⚔️ GAUNTLET LOOP: OTIMIZAÇÃO EXAUSTIVA - {symbol} ({dias} DIAS)")
    print("   Régua B: Retorno >= 10.0% | Sharpe >= 2.20 | Profit Factor >= 1.80 | Max DD <= 8.0%")
    print(f"{'='*85}\n")

    candles = dias * 288
    df = fetch_binance_futures_5m_7dias(symbol, target_candles=candles)
    preco_medio = float(df["close"].mean())

    instrument = criar_instrumento_perp(symbol, preco_medio)
    bar_type = BarType.from_str(f"{instrument.id}-5-MINUTE-LAST-EXTERNAL")
    venue_binance = Venue("BINANCE")
    bars = preparar_barras_nautilus(df, instrument, bar_type)

    # Grade de Busca Refinada
    pivos = [3, 4]
    amplitudes = [0.20, 0.25, 0.30, 0.35]
    degraus = [0.55, 0.65, 0.75]
    tipos_ordem = ["MARKET", "LIMIT_RETESTE"]

    total = len(pivos) * len(amplitudes) * len(degraus) * len(tipos_ordem)
    print(f"🔄 Testando {total} candidatos contra a régua institucional...\n")

    resultados = []
    idx = 0
    inicio_t = time.time()

    for pivo in pivos:
        for amp in amplitudes:
            for degrau in degraus:
                for t_ordem in tipos_ordem:
                    idx += 1
                    res = avaliar_candidato_nautilus(
                        symbol=symbol,
                        bars=bars,
                        instrument=instrument,
                        bar_type=bar_type,
                        venue_binance=venue_binance,
                        pivo=pivo,
                        amp_min=amp,
                        degrau=degrau,
                        tipo_ordem=t_ordem,
                        run_id=f"{idx:03d}",
                    )
                    resultados.append(res)

                    passou_regua = (
                        res["retorno_pct"] >= 10.0
                        and res["sharpe"] >= 2.20
                        and res["profit_factor"] >= 1.80
                        and res["max_dd_pct"] <= 8.0
                    )
                    status_str = "🏆 VENCEU A RÉGUA!" if passou_regua else "❌ Não atingiu"

                    print(
                        f"[{idx:02d}/{total:02d}] P:{pivo} Amp:{amp:.2f}% Deg:{degrau:.2f}x ({t_ordem[:3]}) "
                        f"-> Ret:{res['retorno_pct']:+6.1f}% | Shp:{res['sharpe']:4.2f} | PF:{res['profit_factor']:4.2f} | DD:{res['max_dd_pct']:4.1f}% | {status_str}"
                    )

    duracao = time.time() - inicio_t
    print(f"\n🏁 Varredura concluída em {duracao:.2f}s ({duracao/total:.2f}s por simulação).")

    df_res = pd.DataFrame(resultados)
    
    # Classificar por proximidade da Régua
    # Score = Retorno * Sharpe / (Max DD + 0.1)
    df_res["score"] = (df_res["retorno_pct"] * df_res["sharpe"]) / (df_res["max_dd_pct"] + 0.1)
    df_res = df_res.sort_values(by="score", ascending=False)

    print("\n🏆 [TOP 5 CANDIDATOS DO GAUNTLET LOOP]")
    top_cols = ["pivo", "amp_min", "degrau", "tipo_ordem", "retorno_pct", "sharpe", "profit_factor", "max_dd_pct", "trades", "winrate"]
    print(df_res[top_cols].head(5).to_string(index=False))

    return df_res


if __name__ == "__main__":
    par = sys.argv[1] if len(sys.argv) > 1 else "SOLUSDT"
    rodar_gauntlet_bloco(symbol=par, dias=7)
