"""Módulo Screener: Seleciona dinamicamente pares por Volume e Volatilidade.
Regras:
  1. Filtra contratos perpétuos USDT da Binance Futures.
  2. Descarta stablecoins, ações sintéticas e pares com baixa liquidez (< $25M volume diário).
  3. Classifica pelo Score Ponderado: Score = Volume (Milhões USDT) * (Volatilidade % ^ 1.5).
  4. Retorna até o limite máximo configurado (padrão: 50 pares).
"""
import json
import urllib.request
from typing import List, Dict


def selecionar_pares_volume_volatilidade(
    limite_maximo: int = 50,
    volume_minimo_usd: float = 25_000_000,
    volatilidade_minima_pct: float = 2.0,
) -> List[Dict]:
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        tickers = json.loads(resp.read().decode())

    # Excluir ativos não-cripto ou indesejados
    blacklist_termos = [
        "USDC", "_", "1000", "DOM", "USDP", "EUR", "BUSD",
        "SAMSUNG", "SKHYNIX", "SOXL", "SOXS", "SPCX", "NVDA",
        "AAPL", "TSLA", "AMZN", "MSFT", "GOOG"
    ]

    candidatos = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        if any(b in sym for b in blacklist_termos):
            continue

        try:
            vol_usd = float(t.get("quoteVolume", 0))
            volat_pct = abs(float(t.get("priceChangePercent", 0)))
        except (ValueError, TypeError):
            continue

        # Clivo de requisitos mínimos
        if vol_usd >= volume_minimo_usd and volat_pct >= volatilidade_minima_pct:
            vol_milhoes = vol_usd / 1_000_000
            score = vol_milhoes * (volat_pct ** 1.5)
            candidatos.append({
                "symbol": sym,
                "volume_m": vol_milhoes,
                "volatilidade_pct": volat_pct,
                "score": score,
            })

    # Ordenar pelos mais expressivos em volume + volatilidade
    candidatos.sort(key=lambda x: x["score"], reverse=True)
    return candidatos[:limite_maximo]


if __name__ == "__main__":
    pares = selecionar_pares_volume_volatilidade(50)
    print(f"Total de pares que passaram pelo crivo: {len(pares)}")
    for i, p in enumerate(pares[:10], 1):
        print(f"{i:2d}. {p['symbol']:<12} | Vol: ${p['volume_m']:>7.1f}M | Volat: {p['volatilidade_pct']:>5.2f}% | Score: {p['score']:>8.1f}")
