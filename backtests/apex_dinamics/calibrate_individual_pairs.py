import sys
import time
import pandas as pd
import numpy as np

from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
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
from backtests.apex_dinamics.gauntlet_optimizer import (
    criar_instrumento_perp,
    preparar_barras_nautilus,
    avaliar_candidato_nautilus,
)

sys.stdout.reconfigure(encoding="utf-8")

def otimizar_par_especifico(symbol: str, dias: int = 7):
    print(f"\n{'='*85}")
    print(f"🎯 OTIMIZAÇÃO DE PARÂMETROS DEDICADOS: {symbol} ({dias} DIAS)")
    print(f"{'='*85}\n")

    candles = dias * 288
    df = fetch_binance_futures_5m_7dias(symbol, target_candles=candles)
    preco_medio = float(df["close"].mean())

    instrument = criar_instrumento_perp(symbol, preco_medio)
    bar_type = BarType.from_str(f"{instrument.id}-5-MINUTE-LAST-EXTERNAL")
    venue_binance = Venue("BINANCE")
    bars = preparar_barras_nautilus(df, instrument, bar_type)

    # Grid adaptado para a volatilidade da moeda
    if symbol == "ETHUSDT":
        # Ativo mais pesado/menos volátil: canais menores e stops mais justos
        pivos = [2, 3, 4]
        amplitudes = [0.15, 0.20, 0.25, 0.30]
        degraus = [0.50, 0.60, 0.70]
        tipos_ordem = ["MARKET", "LIMIT_RETESTE"]
    elif symbol == "NEARUSDT":
        # Ativo muito volátil com falsos rompimentos: exige amplitude maior e pivô mais filtrado
        pivos = [3, 4, 5]
        amplitudes = [0.35, 0.45, 0.55, 0.65]
        degraus = [0.60, 0.75, 0.85]
        tipos_ordem = ["MARKET", "LIMIT_RETESTE"]
    elif symbol == "AVAXUSDT":
        pivos = [3, 4]
        amplitudes = [0.25, 0.35, 0.45]
        degraus = [0.60, 0.70, 0.80]
        tipos_ordem = ["MARKET", "LIMIT_RETESTE"]
    else:
        pivos = [3, 4]
        amplitudes = [0.25, 0.30, 0.35]
        degraus = [0.65, 0.75]
        tipos_ordem = ["MARKET"]

    total = len(pivos) * len(amplitudes) * len(degraus) * len(tipos_ordem)
    print(f"🔄 Testando grade com {total} combinações para {symbol}...\n")

    resultados = []
    idx = 0
    t0 = time.time()

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
                        run_id=f"{symbol}_{idx:03d}",
                    )
                    resultados.append(res)
                    passou = res["retorno_pct"] > 0 and res["sharpe"] > 1.5 and res["max_dd_pct"] < 12.0
                    status = "✅ BOM" if passou else ("🟢 POSITIVO" if res["retorno_pct"] > 0 else "❌")
                    if idx % 6 == 0 or res["retorno_pct"] > 5.0:
                        print(
                            f"[{idx:02d}/{total:02d}] P:{pivo} Amp:{amp:.2f}% Deg:{degrau:.2f}x ({t_ordem[:3]}) "
                            f"-> Ret:{res['retorno_pct']:+6.1f}% | Shp:{res['sharpe']:4.2f} | PF:{res['profit_factor']:4.2f} | DD:{res['max_dd_pct']:4.1f}% | {status}"
                        )

    dt = time.time() - t0
    df_res = pd.DataFrame(resultados)
    df_res["score"] = (df_res["retorno_pct"] * np.maximum(df_res["sharpe"], 0.01)) / (df_res["max_dd_pct"] + 0.1)
    df_res = df_res.sort_values(by="score", ascending=False)

    print(f"\n🏁 Concluído em {dt:.1f}s.")
    print(f"\n🏆 TOP 3 CONFIGURAÇÕES PARA {symbol}:")
    top_cols = ["pivo", "amp_min", "degrau", "tipo_ordem", "retorno_pct", "sharpe", "profit_factor", "max_dd_pct", "trades", "winrate"]
    print(df_res[top_cols].head(3).to_string(index=False))

    return df_res.iloc[0].to_dict()


if __name__ == "__main__":
    pares = ["AVAXUSDT", "NEARUSDT", "ETHUSDT"]
    campeoes = {}
    for p in pares:
        campeao = otimizar_par_especifico(p, dias=7)
        campeoes[p] = campeao

    print("\n" + "="*85)
    print("💎 RESUMO FINAL DE CALIBRAÇÃO DOS PARES:")
    print("="*85)
    for p, c in campeoes.items():
        print(f"Par: {p:10} -> Pivô: {c['pivo']} | Amp Mín: {c['amp_min']:.2f}% | Degrau: {c['degrau']:.2f}x | Ordem: {c['tipo_ordem']}")
        print(f"                Retorno: {c['retorno_pct']:+6.2f}% | Sharpe: {c['sharpe']:.2f} | Max DD: {c['max_dd_pct']:.2f}% | Trades: {c['trades']}")
