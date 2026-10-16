"""Utilitário de exportação de dados para análise e otimização no MATLAB.

Baixa o histórico de candles OHLCV pela KuCoin (via ccxt), calcula o ATR,
as retrações e os pivôs Fimathe, e salva em formato CSV compatível
com readtable() / importdata() do MATLAB.

Uso:
  python export_matlab.py [--symbol BTC/USDT] [--timeframe 1h] [--limit 500] [--out data/fimathe_matlab.csv]
"""
from __future__ import annotations

import argparse
from pathlib import Path
from dotenv import load_dotenv

from exchange import Exchange
from fimathe import calcular_atr
from strategy import detectar_pivos

BASE_DIR = Path(__file__).resolve().parent


def exportar_para_matlab(
    symbol: str,
    timeframe: str,
    limit: int,
    caminho_saida: Path,
    market_type: str = "spot",
) -> Path:
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    exchange = Exchange(market_type=market_type, modo="paper")

    print(f"Baixando {limit} candles de {symbol} ({timeframe}) no mercado {market_type} da KuCoin...")
    df = exchange.buscar_candles(symbol, timeframe, limite=limit)

    # Adiciona ATR e métricas estatísticas
    df["atr_14"] = calcular_atr(df, periodo=14)
    df["retorno_pct"] = df["close"].pct_change() * 100
    df["volatilidade_20"] = df["retorno_pct"].rolling(20).std()

    # Adiciona detecção de pivôs para análise de canais no MATLAB
    df_pivos = detectar_pivos(df, janela=5)
    df["pivo_topo"] = df_pivos["pivo_topo"].astype(int)
    df["pivo_fundo"] = df_pivos["pivo_fundo"].astype(int)

    # Formata timestamps para leitura limpa no MATLAB (datestr / datetime)
    df_export = df.reset_index()
    df_export["timestamp"] = df_export["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")

    df_export.to_csv(caminho_saida, index=False)
    print(f"Arquivo exportado com sucesso: {caminho_saida} ({len(df_export)} linhas)")
    return caminho_saida


if __name__ == "__main__":
    load_dotenv(BASE_DIR / ".env")
    parser = argparse.ArgumentParser(description="Exportar dados para MATLAB")
    parser.add_argument("--symbol", default="BTC/USDT", help="Par negociado (ex: BTC/USDT)")
    parser.add_argument("--timeframe", default="1h", help="Tempo gráfico (ex: 15m, 1h, 4h)")
    parser.add_argument("--limit", type=int, default=500, help="Quantidade de candles")
    parser.add_argument("--market_type", default="spot", choices=["spot", "futures"], help="Mercado (spot ou futures)")
    parser.add_argument("--out", default=str(BASE_DIR / "data" / "fimathe_matlab.csv"), help="Caminho do CSV gerado")

    args = parser.parse_args()
    exportar_para_matlab(args.symbol, args.timeframe, args.limit, Path(args.out), args.market_type)
