"""Backtest Multi-Gráficos: Monitoramento de até 50 pares com Limite de 3 Posições Simultâneas
Período: 5 dias completos (1.440 velas de 5m por par)
Alavancagem: 10x
Regras integradas:
  1. Screener dinâmico por volume e volatilidade (até 50 pares).
  2. Limite estrito de no máximo 3 posições abertas simultaneamente (capital alocado: saldo / 3 por slot).
  3. 1 Nível de rompimento da Fimathe (rompe no canal de referência).
  4. Trailing Stop Progressivo por níveis (ao bater 1º nível -> stop em 50% do nível 1; metade do nível 2 -> stop no nível 1, etc).
  5. Saída em Trailing Stop positivo contabilizada como GAIN.
  6. Circuit Breaker Temporal Escalonado: 2 stops em 6h ou alternâncias em 4h suspendem por 6h; reincidência suspende por 24h.
"""
import concurrent.futures
import json
import urllib.request
import pandas as pd
import numpy as np

from screener import selecionar_pares_volume_volatilidade
from strategy import Direcao, EstrategiaFimathe
from circuit_breaker import CircuitBreakerPar
from fimathe import calcular_atr


def fetch_binance_futures_5m_5dias(symbol: str, limit: int = 1440) -> pd.DataFrame:
    # 5 dias * 24h * 12 velas/hora = 1.440 velas
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=5m&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
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


def executar_backtest_5dias_10x(
    capital_inicial: float = 1000.0,
    alavancagem_max: float = 10.0,
    max_posicoes_simultaneas: int = 3,
    limite_screener: int = 50,
):
    print("=" * 96)
    print(f"  BACKTEST 5 DIAS | ATÉ {limite_screener} PARES | MÁXIMO 3 OPERAÇÕES SIMULTÂNEAS | ALAVANCAGEM DINÂMICA (MÁX {alavancagem_max:.0f}X)")
    print("=" * 96)
    print("Etapa 1: Filtrando pares que passam pelo clivo de volume e volatilidade...")
    
    candidatos = selecionar_pares_volume_volatilidade(
        limite_maximo=limite_screener,
        volume_minimo_usd=25_000_000,
        volatilidade_minima_pct=2.0,
    )
    lista_pares = [c["symbol"] for c in candidatos]
    print(f"Total de pares qualificados: {len(lista_pares)}")
    clean_pares = [p.encode('ascii', 'ignore').decode('ascii') for p in lista_pares]
    print(f"Pares monitorados: {', '.join(clean_pares[:15])} ... (+ {len(clean_pares)-15} pares)")

    print(f"\nEtapa 2: Baixando 5 dias (1.440 velas de 5m) para cada par em paralelo...")
    dfs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        fut_map = {executor.submit(fetch_binance_futures_5m_5dias, p): p for p in lista_pares}
        for f in concurrent.futures.as_completed(fut_map):
            p = fut_map[f]
            try:
                df_res = f.result()
                if len(df_res) >= 500:
                    dfs[p] = df_res
            except Exception as e:
                pass

    print(f"Sucesso: {len(dfs)} pares com dados completos prontos para simulação.")
    if not dfs:
        print("Erro: Nenhum par com dados disponíveis.")
        return

    # Unificar timestamps globais cronológicos
    todos_ts = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    inicio_dt = todos_ts[0].strftime("%d/%m/%Y %H:%M UTC")
    fim_dt = todos_ts[-1].strftime("%d/%m/%Y %H:%M UTC")
    print(f"Período simulado: {inicio_dt} até {fim_dt}")

    # Configuração da Estratégia
    est = EstrategiaFimathe(
        janela_pivo=3,
        qtd_niveis=4,
        folga_stop=0.10,
        amplitude_min_pct=0.25,
        usar_filtro_50=False,
        modo_stop="fora_caixinha",
        atr_filtro_ativo=False,
        romper_no_canal=True,
    )

    # Circuit breakers individuais por par (6h no 1º bloqueio, 24h na reincidência)
    circuit_breakers = {p: CircuitBreakerPar(horas_base=6.0, horas_reincidencia=24.0) for p in dfs}

    saldo = capital_inicial
    posicoes_abertas = {}  # {symbol: dados}
    historico_trades = []
    bloqueios_totais = 0
    sinais_descartados_limite = 0

    taxa = 0.04 / 100
    slippage = 0.01 / 100
    janela_min = 4 * est.janela_pivo + 2

    print(f"\nEtapa 3: Executando motor candle a candle com controle de carteira...")

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
            
            # Trailing Stop Progressivo
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
                alav_trade = pos.get("alavancagem", alavancagem_max)
                pnl_pct_liq = (retorno_pct - custos_pct) * alav_trade

                lucro_usd = pos["capital_alocado"] * (pnl_pct_liq / 100)
                saldo += lucro_usd

                # Registrar no Circuit Breaker (trailing_stop é considerado gain)
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

        # B) Avaliar novos sinais se tiver vagas abertas (< max_posicoes_simultaneas)
        for sym, df_p in dfs.items():
            if sym in posicoes_abertas:
                continue
            if ts not in df_p.index:
                continue
            
            idx_loc = df_p.index.get_loc(ts)
            if idx_loc < janela_min:
                continue

            # Checar se par está suspenso pelo Circuit Breaker (6h ou 24h)
            suspenso, _ = circuit_breakers[sym].esta_suspenso(ts)
            if suspenso:
                continue

            sub_hist = df_p.iloc[:idx_loc]
            sinal = est.avaliar(sub_hist)
            if sinal is not None:
                # Se já atingiu 3 posições simultâneas, descarta sinal excedente
                if len(posicoes_abertas) >= max_posicoes_simultaneas:
                    sinais_descartados_limite += 1
                    continue

                compra = sinal.direcao == Direcao.COMPRA
                c_close = float(df_p.loc[ts, "close"])
                p_entrada = c_close * (1 + slippage if compra else 1 - slippage)
                amp = sinal.grade["amplitude"]
                alvo_final = sinal.alvos[-1] if len(sinal.alvos) >= 2 else (p_entrada + (amp * 3) if compra else p_entrada - (amp * 3))

                # ALAVANCAGEM DINÂMICA BASEADA NA AMPLITUDE DA CAIXINHA
                # Regra: Risco máximo por trade travado em 10% do slot
                # Se caixinha for 0.5% -> alavancagem = 10x (stop de ~5% da margem)
                # Se caixinha for 1.0% -> alavancagem = 10x (stop de ~10% da margem)
                # Se caixinha for 2.0% -> alavancagem cai dinamicamente para 5x (stop de ~10% da margem)
                # Se caixinha for 3.0% -> alavancagem cai dinamicamente para 3.3x (stop de ~10% da margem)
                amp_pct = (amp / p_entrada) * 100
                if amp_pct > 0:
                    alav_dinamica = min(alavancagem_max, max(1.0, 10.0 / amp_pct))
                else:
                    alav_dinamica = alavancagem_max

                # Alocação por slot: saldo disponível dividido pelas 3 vagas
                capital_slot = saldo / max_posicoes_simultaneas

                posicoes_abertas[sym] = {
                    "ts": ts,
                    "direcao": sinal.direcao,
                    "entrada": p_entrada,
                    "stop": sinal.stop_loss,
                    "amplitude": amp,
                    "alvo_final": alvo_final,
                    "capital_alocado": capital_slot,
                    "alavancagem": alav_dinamica,
                }

    # Consolidação dos Resultados
    total_trades = len(historico_trades)
    wins = [t for t in historico_trades if t["lucro_usd"] > 0]
    losses = [t for t in historico_trades if t["lucro_usd"] <= 0]
    wr = (len(wins) / total_trades * 100) if total_trades > 0 else 0
    lucro_total_usd = saldo - capital_inicial
    retorno_total_pct = (lucro_total_usd / capital_inicial) * 100
    
    # Contabilizar bloqueios do Circuit Breaker
    bloqueios_6h = 0
    bloqueios_24h = 0
    for cb in circuit_breakers.values():
        if cb.total_bloqueios >= 1:
            bloqueios_6h += 1
        if cb.total_bloqueios >= 2:
            bloqueios_24h += (cb.total_bloqueios - 1)

    print("\n" + "=" * 96)
    print("                     RELATÓRIO FINAL DO BACKTEST DE 5 DIAS")
    print("=" * 96)
    print(f"Capital Inicial               : $ {capital_inicial:,.2f} USDT")
    print(f"Saldo Final da Conta          : $ {saldo:,.2f} USDT")
    print(f"LUCRO LÍQUIDO TOTAL ($)       : $ {lucro_total_usd:>+,.2f} USDT")
    print(f"RENTABILIDADE TOTAL (%)       : {retorno_total_pct:>+8.2f}%")
    print(f"Alavancagem Utilizada         : Dinâmica (Teto Máx {alavancagem_max:.0f}x)")
    print(f"Limite de Posições Concorrentes: {max_posicoes_simultaneas} pares no máximo")
    print(f"Total de Gráficos Monitorados : {len(dfs)} pares selecionados")
    print("-" * 96)
    print(f"Total de Trades Executados    : {total_trades}")
    print(f"Trades Vencedores (Wins)      : {len(wins)}")
    print(f"Trades Perdedores (Losses)    : {len(losses)}")
    print(f"Taxa de Acerto (Winrate)      : {wr:.2f}%")
    print(f"Sinais Descartados por Limite : {sinais_descartados_limite} (proteção contra sobre-exposição)")
    print(f"Suspensões 6h Aplicadas       : {bloqueios_6h}")
    print(f"Suspensões 24h (Reincidência) : {bloqueios_24h}")
    print("=" * 96)

    # Exibir Top 10 Melhores Trades
    if wins:
        top_trades = sorted(wins, key=lambda x: x["lucro_usd"], reverse=True)
        print("\n--- TOP 10 MAIORES GAINS CONDUZIDOS PELO TRAILING STOP ---")
        for i, t in enumerate(top_trades[:10], 1):
            dur_m = int((t["ts_out"] - t["ts_in"]).total_seconds() // 60)
            print(f" {i:2d}. [{t['symbol']:<10}] {t['direcao'].upper():<4} PnL: {t['pnl_pct']:>+6.2f}% (10x) | Lucro: +${t['lucro_usd']:>6.2f} | Dur: {dur_m}m | {t['motivo'].upper()}")


if __name__ == "__main__":
    executar_backtest_5dias_10x(
        capital_inicial=1000.0,
        alavancagem_max=10.0,
        max_posicoes_simultaneas=3,
        limite_screener=50,
    )
