"""Backtest dos 4 Melhores Pares (AVAX, SOL, NEAR, ETH) em 7 Dias Completos.
Período: 7 dias = 2.016 velas de 5m por par (paginado via Binance Futures API)
Setup:
  - Capital Inicial: $1.000 USDT
  - Alavancagem: 10x
  - Limite de Posições Concorrentes: máx 3 simultâneas (capital alocado = saldo / 3 por slot)
  - Pares: AVAXUSDT, SOLUSDT, NEARUSDT, ETHUSDT
  - 1 Nível de Rompimento da Fimathe (romper_no_canal=True)
  - Trailing Stop Progressivo por níveis
  - Circuit Breaker Temporal Escalonado (6h base / 24h reincidência, trailing stop no lucro conta como gain)
"""
import concurrent.futures
import json
import time
import urllib.request
import pandas as pd
import numpy as np

from strategy import Direcao, EstrategiaFimathe
from circuit_breaker import CircuitBreakerPar
from fimathe import calcular_atr


def fetch_binance_futures_5m_7dias(symbol: str, target_candles: int = 2016) -> pd.DataFrame:
    """Busca 2016 velas de 5m (7 dias) paginando na Binance Futures API."""
    all_raw = []
    end_time = None
    
    # Cada requisição pega no máximo 1500 velas. Para 2016 velas, 2 chamadas bastam.
    while len(all_raw) < target_candles:
        needed = min(1500, target_candles - len(all_raw))
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=5m&limit={needed}"
        if end_time is not None:
            url += f"&endTime={end_time}"
            
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode())
            
        if not data:
            break
            
        # As velas retornadas vêm em ordem cronológica ascendente
        # Se estamos paginando para trás usando endTime, prepend ou armazene
        all_raw = data + all_raw
        first_open_time = data[0][0]
        end_time = first_open_time - 1
        
        if len(data) < needed:
            break
            
        time.sleep(0.1)

    # Remover duplicatas pelo timestamp
    unique_map = {}
    for item in all_raw:
        unique_map[item[0]] = item
        
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
    df["atr_14"] = calcular_atr(df, 14)
    return df


def executar_backtest_7dias():
    pares = ["AVAXUSDT", "SOLUSDT", "NEARUSDT", "ETHUSDT"]
    
    print("=" * 96)
    print("      BACKTEST TOP 4 PARES EM 7 DIAS (2.016 VELAS DE 5M) | CARTEIRA $1.000 | 10X")
    print("=" * 96)
    print(f"Pares Focados: {', '.join(pares)}")
    print("Configuração:")
    print("  • Capital Inicial: $1.000,00 USDT")
    print("  • Alavancagem Fixa: 10x")
    print("  • Posições Simultâneas: Máx 3 simultâneas (~$333/posição)")
    print("  • Estratégia: 1 Nível Rompimento (Canal de Referência)")
    print("  • Stop: Trailing Stop Dinâmico por Níveis")
    print("  • Proteção: Circuit Breaker Escalonado (6h base / 24h reincidência)")
    print("  • Taxas: 0.04% taker por perna + 0.01% slippage")
    print("\nBaixando histórico de 7 dias (2.016 velas de 5m por par)...")

    dfs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        fut_map = {executor.submit(fetch_binance_futures_5m_7dias, p): p for p in pares}
        for f in concurrent.futures.as_completed(fut_map):
            p = fut_map[f]
            try:
                df_res = f.result()
                if len(df_res) >= 1000:
                    dfs[p] = df_res
                    print(f"  [OK] {p:<10}: {len(df_res)} velas carregadas.")
            except Exception as e:
                print(f"  [ERRO] {p}: {e}")

    if not dfs:
        print("Erro: Nenhum dado foi baixado.")
        return

    todos_ts = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    inicio_dt = todos_ts[0].strftime("%d/%m/%Y %H:%M UTC")
    fim_dt = todos_ts[-1].strftime("%d/%m/%Y %H:%M UTC")
    print(f"\nPeríodo do Backtest: {inicio_dt} até {fim_dt} (~7 dias)")

    est = EstrategiaFimathe(
        janela_pivo=3,
        qtd_niveis=4,
        folga_stop=0.10,
        amplitude_min_pct=0.20,
        usar_filtro_50=False,
        modo_stop="fora_caixinha",
        atr_filtro_ativo=False,
        romper_no_canal=True,
        ker_filtro_ativo=False,
    )

    circuit_breakers = {p: CircuitBreakerPar(horas_base=6.0, horas_reincidencia=24.0) for p in dfs}

    capital_inicial = 1000.0
    alavancagem = 10.0
    max_posicoes = 3
    saldo = capital_inicial
    posicoes_abertas = {}
    historico_trades = []
    sinais_descartados_limite = 0

    taxa = 0.04 / 100
    slippage = 0.01 / 100
    janela_min = 4 * est.janela_pivo + 2

    for ts in todos_ts:
        # A) Gerenciar posições abertas
        pares_fechar = []
        for sym, pos in list(posicoes_abertas.items()):
            df_p = dfs[sym]
            if ts not in df_p.index:
                continue
            candle = df_p.loc[ts]
            high_c = float(candle["high"])
            low_c = float(candle["low"])
            
            compra = pos["direcao"] == Direcao.COMPRA
            p_ent = pos["entrada"]
            amp = pos["amplitude"]
            
            # Trailing stop por níveis
            maior_fav = high_c if compra else low_c
            avanco = (maior_fav - p_ent) if compra else (p_ent - maior_fav)
            passos = int(avanco // (0.5 * amp)) if amp > 0 else 0
            if passos >= 2:
                mult_stop = (passos - 1) * 0.5
                if compra:
                    n_stop = p_ent + (mult_stop * amp)
                    if n_stop > pos["stop"]: pos["stop"] = n_stop
                else:
                    n_stop = p_ent - (mult_stop * amp)
                    if n_stop < pos["stop"]: pos["stop"] = n_stop

            atingiu_stop = (low_c <= pos["stop"]) if compra else (high_c >= pos["stop"])
            atingiu_alvo = (high_c >= pos["alvo_final"]) if compra else (low_c <= pos["alvo_final"])

            if atingiu_stop or atingiu_alvo:
                if atingiu_alvo and not atingiu_stop:
                    motivo = "alvo_final"
                    px_out = pos["alvo_final"] * (1 - slippage if compra else 1 + slippage)
                else:
                    motivo = "trailing_stop" if (pos["stop"] > p_ent if compra else pos["stop"] < p_ent) else "stop_loss"
                    px_out = pos["stop"] * (1 - slippage if compra else 1 + slippage)

                fator = 1.0 if compra else -1.0
                retorno_pct = ((px_out - p_ent) / p_ent) * 100 * fator
                custos_pct = (taxa * 2) * 100
                pnl_pct_liq = (retorno_pct - custos_pct) * alavancagem

                lucro_usd = pos["capital_alocado"] * (pnl_pct_liq / 100)
                saldo += lucro_usd

                # Registrar no Circuit Breaker
                circuit_breakers[sym].registrar_trade(ts, pnl_pct_liq, motivo_saida=motivo)
                
                historico_trades.append({
                    "symbol": sym,
                    "direcao": pos["direcao"].value,
                    "entrada": p_ent,
                    "saida": px_out,
                    "ts_in": pos["ts"],
                    "ts_out": ts,
                    "pnl_pct": pnl_pct_liq,
                    "lucro_usd": lucro_usd,
                    "saldo_apos": saldo,
                    "motivo": motivo,
                })
                pares_fechar.append(sym)

        for sym in pares_fechar:
            del posicoes_abertas[sym]

        # B) Avaliar novos sinais se tiver vagas abertas (< max_posicoes)
        for sym, df_p in dfs.items():
            if sym in posicoes_abertas:
                continue
            if ts not in df_p.index:
                continue
            
            idx_loc = df_p.index.get_loc(ts)
            if idx_loc < janela_min:
                continue

            suspenso, _ = circuit_breakers[sym].esta_suspenso(ts)
            if suspenso:
                continue

            sub_hist = df_p.iloc[:idx_loc]
            sinal = est.avaliar(sub_hist)
            if sinal is not None:
                if len(posicoes_abertas) >= max_posicoes:
                    sinais_descartados_limite += 1
                    continue

                compra = sinal.direcao == Direcao.COMPRA
                c_close = float(df_p.loc[ts, "close"])
                p_entrada = c_close * (1 + slippage if compra else 1 - slippage)
                amp = sinal.grade["amplitude"]
                alvo_final = sinal.alvos[-1] if len(sinal.alvos) >= 2 else (p_entrada + (amp * 3) if compra else p_entrada - (amp * 3))

                # Capital alocado: saldo atual / 3 vagas
                capital_slot = saldo / max_posicoes

                posicoes_abertas[sym] = {
                    "ts": ts,
                    "direcao": sinal.direcao,
                    "entrada": p_entrada,
                    "stop": sinal.stop_loss,
                    "amplitude": amp,
                    "alvo_final": alvo_final,
                    "capital_alocado": capital_slot,
                }

    # Consolidação
    total_trades = len(historico_trades)
    wins = [t for t in historico_trades if t["lucro_usd"] > 0]
    losses = [t for t in historico_trades if t["lucro_usd"] <= 0]
    wr = (len(wins) / total_trades * 100) if total_trades > 0 else 0
    lucro_total_usd = saldo - capital_inicial
    retorno_total_pct = (lucro_total_usd / capital_inicial) * 100

    print("\n" + "=" * 96)
    print("       RESULTADO CONSOLIDADO DO BACKTEST: 7 DIAS (TOP 4 PARES / 10X)")
    print("=" * 96)
    print(f"Capital Inicial               : $ {capital_inicial:,.2f} USDT")
    print(f"Saldo Final da Conta          : $ {saldo:,.2f} USDT")
    print(f"LUCRO LÍQUIDO TOTAL ($)       : $ {lucro_total_usd:>+,.2f} USDT")
    print(f"RENTABILIDADE TOTAL (%)       : {retorno_total_pct:>+8.2f}%")
    print(f"Alavancagem Fixa              : {alavancagem:.0f}x")
    print(f"Limite de Posições Concorrentes: {max_posicoes} pares simultâneos")
    print("-" * 96)
    print(f"Total de Trades Executados    : {total_trades}")
    print(f"Trades Vencedores (Wins)      : {len(wins)}")
    print(f"Trades Perdedores (Losses)    : {len(losses)}")
    print(f"Taxa de Acerto (Winrate)      : {wr:.2f}%")
    print(f"Sinais Descartados por Limite : {sinais_descartados_limite}")
    print("=" * 96)

    # Desempenho individual
    print("\n--- DESEMPENHO INDIVIDUAL POR PAR (7 DIAS) ---")
    print(f"{'PAR':<10} | {'TRADES':<7} | {'W/L':<8} | {'WINRATE':<9} | {'PnL TOTAL ($)':<14}")
    print("-" * 55)
    for p in pares:
        tr_p = [t for t in historico_trades if t["symbol"] == p]
        w_p = sum(1 for t in tr_p if t["lucro_usd"] > 0)
        l_p = sum(1 for t in tr_p if t["lucro_usd"] <= 0)
        wr_p = (w_p / len(tr_p) * 100) if tr_p else 0
        lucro_p = sum(t["lucro_usd"] for t in tr_p)
        wl_s = f"{w_p}W/{l_p}L"
        print(f"{p:<10} | {len(tr_p):<7} | {wl_s:<8} | {wr_p:>7.1f}% | ${lucro_p:>+12.2f}")
    print("=" * 55)

    if wins:
        top_trades = sorted(wins, key=lambda x: x["lucro_usd"], reverse=True)
        print("\n--- TOP 5 MAIORES GAINS CONDUZIDOS PELO TRAILING STOP ---")
        for i, t in enumerate(top_trades[:5], 1):
            dur_m = int((t["ts_out"] - t["ts_in"]).total_seconds() // 60)
            print(f" {i}. [{t['symbol']:<8}] {t['direcao'].upper():<4} PnL: {t['pnl_pct']:>+5.2f}% (10x) | Lucro: +${t['lucro_usd']:>6.2f} | Dur: {dur_m}m | {t['motivo'].upper()}")


if __name__ == "__main__":
    executar_backtest_7dias()
