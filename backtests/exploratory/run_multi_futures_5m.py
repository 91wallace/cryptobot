"""Backtest Multi-Ativos Fimathe em Binance Futures USDT-M.
Executa simulação simultânea nos 5 pares de maior volume em velas de 5m (72h).
Pares: BTCUSDT, ETHUSDT, SOLUSDT, NEARUSDT, XRPUSDT
"""
from pathlib import Path
import json
import urllib.request
import concurrent.futures
import pandas as pd
import numpy as np

from strategy import EstrategiaFimathe
from backtest import BacktestFimathe
from fimathe import calcular_atr


def fetch_binance_futures_5m(symbol: str, limit: int = 864) -> pd.DataFrame:
    """Busca histórico de 72h (864 candles de 5m) diretamente da API Binance Futures."""
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=5m&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    
    records = []
    for item in data:
        records.append({
            "timestamp": pd.to_datetime(item[0], unit="ms", utc=True),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
        })
    df = pd.DataFrame(records).set_index("timestamp")
    df["atr_14"] = calcular_atr(df, 14)
    return df


def testar_par(symbol: str, df: pd.DataFrame, modo_stop: str = "fora_caixinha"):
    """Executa a estratégia em um par específico."""
    est = EstrategiaFimathe(
        janela_pivo=3,
        qtd_niveis=3,
        folga_stop=0.10,
        amplitude_min_pct=0.15,
        usar_filtro_50=False,
        modo_stop=modo_stop,
        atr_filtro_ativo=False,
    )
    # Taxas reais Binance Futures: 0.04% taxa de ordem taker + 0.01% slippage
    bt = BacktestFimathe(est, taxa_pct=0.04, slippage_pct=0.01)
    res = bt.executar(df)
    return {
        "symbol": symbol,
        "total_trades": res["total_trades"],
        "wins": res["wins"],
        "losses": res["losses"],
        "win_rate_pct": res["win_rate_pct"],
        "retorno_total_pct": res["retorno_total_pct"],
        "profit_factor": res["profit_factor"],
        "max_drawdown_pct": res["max_drawdown_pct"],
        "trades": res["trades"],
        "preco_inicio": df["close"].iloc[0],
        "preco_fim": df["close"].iloc[-1],
        "var_ativo_pct": ((df["close"].iloc[-1] - df["close"].iloc[0]) / df["close"].iloc[0]) * 100,
    }


def main():
    pares = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "XRPUSDT"]
    print("=" * 80)
    print("      BACKTEST MULTI-ATIVOS FIMATHE - BINANCE FUTURES (5 MINUTOS)")
    print("=" * 80)
    print(f"Pares selecionados por maior volume: {', '.join(pares)}")
    print("Período: 72 horas (864 candles de 5m por par)")
    print("Modo de Stop testado: FORA_CAIXINHA (1:1 Proporcional)")
    print("Baixando dados de mercado em paralelo...\n")

    dados = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_symbol = {executor.submit(fetch_binance_futures_5m, p): p for p in pares}
        for future in concurrent.futures.as_completed(future_to_symbol):
            sym = future_to_symbol[future]
            try:
                dados[sym] = future.result()
                print(f"  [OK] {sym}: {len(dados[sym])} velas carregadas ({dados[sym].index[0].strftime('%d/%m %H:%M')} até {dados[sym].index[-1].strftime('%d/%m %H:%M')})")
            except Exception as e:
                print(f"  [ERRO] {sym}: {e}")

    resultados = []
    todos_trades = []

    for sym in pares:
        if sym not in dados:
            continue
        res = testar_par(sym, dados[sym], modo_stop="fora_caixinha")
        resultados.append(res)
        for t in res["trades"]:
            todos_trades.append((sym, t))

    print("\n" + "=" * 80)
    print(f"{'PAR':<10} | {'VAR ATIVO':<10} | {'TRADES':<7} | {'W/L':<6} | {'WINRATE':<8} | {'PROFIT LÍQ':<11} | {'MAX DD':<7}")
    print("-" * 80)

    total_trades_geral = sum(r["total_trades"] for r in resultados)
    total_wins_geral = sum(r["wins"] for r in resultados)
    total_losses_geral = sum(r["losses"] for r in resultados)
    lucro_acumulado_soma = sum(r["retorno_total_pct"] for r in resultados)

    for r in resultados:
        wl = f"{r['wins']}W/{r['losses']}L"
        print(f"{r['symbol']:<10} | {r['var_ativo_pct']:+8.2f}% | {r['total_trades']:<7} | {wl:<6} | {r['win_rate_pct']:>6.1f}% | {r['retorno_total_pct']:>+9.2f}% | {r['max_drawdown_pct']:>6.2f}%")

    print("-" * 80)
    wr_global = (total_wins_geral / total_trades_geral * 100) if total_trades_geral > 0 else 0.0
    print(f"{'CONSOLIDADO':<10} | {'---':<10} | {total_trades_geral:<7} | {total_wins_geral}W/{total_losses_geral}L  | {wr_global:>6.1f}% | {lucro_acumulado_soma:>+9.2f}% | {'---':<7}")
    print("=" * 80)

    if todos_trades:
        print("\n--- HISTÓRICO COMPLETO DE TRADES EXECUTADOS ---")
        for sym, t in todos_trades:
            duracao_m = t.duracao_candles * 5
            print(f" • [{sym}] {t.direcao.upper():<4} Ent: {t.preco_entrada:<9.4f} ({t.indice_entrada.strftime('%d/%m %H:%M')}) -> Saída: {t.preco_saida:<9.4f} ({t.indice_saida.strftime('%d/%m %H:%M')}) | {t.motivo_saida.upper()} | Dur: {duracao_m}m | PnL: {t.pnl_pct:+.2f}%")
    else:
        print("\nNenhum trade executado nos pares durante o período.")


if __name__ == "__main__":
    main()
