"""Testes de casos de borda e resiliência para o TUI do Cryptobot."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
import pytest

from dashboard.tui_app import CryptobotTUI
from dashboard.tui_data import BotDataProvider, BotMetrics, PositionInfo, TradeRecord
from tests.test_tui_app import get_rendered_text
from textual.widgets import DataTable, Static


def test_empty_data_directory(tmp_path: Path):
    """Testa provedor com diretório completamente vazio."""
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()
    provider = BotDataProvider(root_dir=tmp_path)
    metrics = provider.get_metrics()

    assert metrics.status == "OFFLINE"
    assert metrics.is_running is False
    assert metrics.pid is None
    assert metrics.total_trades == 0
    assert metrics.win_rate_pct == 0.0
    assert metrics.profit_total == 0.0
    assert metrics.profit_factor == 0.0
    assert len(metrics.recent_trades) == 0
    assert len(metrics.open_orders) == 0
    assert len(metrics.recent_logs) == 0


def test_corrupted_files_handling(tmp_path: Path):
    """Testa arquivos corrompidos (JSON inválido, PID inválido)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # PID inválido
    (data_dir / "bot.pid").write_text("NOT_A_PID\n", encoding="utf-8")
    # JSON corrompido
    (data_dir / "bot_meta.json").write_text("{corrupted_json", encoding="utf-8")
    (data_dir / "paper_futures.json").write_text("{{corrupted_json", encoding="utf-8")

    provider = BotDataProvider(root_dir=tmp_path)
    metrics = provider.get_metrics()

    assert metrics.status == "OFFLINE"
    assert metrics.pid is None
    assert metrics.is_running is False
    assert metrics.initial_capital == 1000.0


def test_win_rate_and_profit_factor_calculation(tmp_path: Path):
    """Testa cálculo exato de win rate e profit factor em múltiplos cenários."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Cenário misto: 3 vitórias ($30), 1 derrota ($10)
    paper_content = {
        "capital_inicial": 1000.0,
        "saldo": 1020.0,
        "historico": [
            {"ts": "2026-10-14 10:00:00", "pnl": 10.0, "symbol": "SOL/USDT:USDT", "side": "buy"},
            {"ts": "2026-10-14 11:00:00", "pnl": 15.0, "symbol": "SOL/USDT:USDT", "side": "buy"},
            {"ts": "2026-10-14 12:00:00", "pnl": 5.0, "symbol": "SOL/USDT:USDT", "side": "buy"},
            {"ts": "2026-10-14 13:00:00", "pnl": -10.0, "symbol": "SOL/USDT:USDT", "side": "sell"},
        ],
    }
    (data_dir / "paper_futures.json").write_text(json.dumps(paper_content), encoding="utf-8")

    provider = BotDataProvider(root_dir=tmp_path)
    m = provider.get_metrics()

    assert m.total_trades == 4
    assert m.winning_trades == 3
    assert m.losing_trades == 1
    assert m.win_rate_pct == 75.0
    # Profit factor: 30 / 10 = 3.0
    assert m.profit_factor == 3.0
    assert m.profit_total == 20.0


def test_circuit_breaker_active_in_sqlite(tmp_path: Path):
    """Testa detecção do circuit breaker ativo via SQLite."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Cria bot.pid ativo com o PID do processo atual
    (data_dir / "bot.pid").write_text(str(os.getpid()), encoding="utf-8")

    db_path = data_dir / "bot.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE estado (chave TEXT PRIMARY KEY, valor TEXT)")
    cb_data = {
        "suspenso_ate": time.time() + 7200,  # 2 horas no futuro
        "fechamentos": [[time.time(), "LOSS"]],
    }
    conn.execute("INSERT INTO estado (chave, valor) VALUES (?, ?)", ("cb", json.dumps(cb_data)))
    conn.commit()
    conn.close()

    provider = BotDataProvider(root_dir=tmp_path)
    m = provider.get_metrics()

    assert m.circuit_breaker_active is True
    assert m.status == "PAUSADO - CIRCUIT BREAKER"
    assert m.circuit_breaker_pause_until is not None
    assert "h" in m.circuit_breaker_pause_until


@pytest.mark.asyncio
async def test_tui_app_rendering_with_empty_state(tmp_path: Path):
    """Valida renderização perfeita da interface mesmo quando não há histórico de trades nem bot rodando."""
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()
    provider = BotDataProvider(root_dir=tmp_path)

    app = CryptobotTUI(demo_mode=False, refresh_interval=1.0, provider=provider)
    async with app.run_test() as pilot:
        card_status = pilot.app.query_one("#card-status", Static)
        text_status = get_rendered_text(card_status)
        assert "OFFLINE" in text_status

        card_trades = pilot.app.query_one("#card-trades-summary", Static)
        text_trades = get_rendered_text(card_trades)
        assert "Sem trades" in text_trades

        card_strategy = pilot.app.query_one("#card-strategy", Static)
        text_strategy = get_rendered_text(card_strategy)
        assert "Nenhuma posição aberta" in text_strategy


def test_active_position_pnl_calculation(tmp_path: Path):
    """Valida o cálculo exato de PnL e PnL % da posição ativa via SQLite."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Cria SQLite com posição ativa
    db_path = data_dir / "bot.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE estado (chave TEXT PRIMARY KEY, valor TEXT)")
    pos_data = {
        "symbol": "SOL/USDT:USDT",
        "side": "buy",
        "quantidade": 2.0,
        "entrada": 100.0,
        "extremo": 110.0,
        "stop": 95.0,
        "alvo": 120.0,
        "aberta_em": "2026-10-14 12:00:00",
    }
    conn.execute("INSERT INTO estado (chave, valor) VALUES (?, ?)", ("posicao", json.dumps(pos_data)))
    conn.commit()
    conn.close()

    provider = BotDataProvider(root_dir=tmp_path)
    m = provider.get_metrics()

    assert m.active_position is not None
    assert m.active_position.symbol == "SOL/USDT:USDT"
    # PnL: (110 - 100) * 2 = 20.0
    assert m.active_position.pnl == 20.0
    # PnL %: (110 / 100 - 1) * 100 = 10.0%
    assert m.active_position.pnl_pct == 10.0


def test_paper_position_fallback(tmp_path: Path):
    """Valida fallback de posição ativa pelo paper quando bot.db não tem posição."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    paper_content = {
        "capital_inicial": 1000.0,
        "saldo": 1000.0,
        "posicoes": {
            "SOL/USDT:USDT": {
                "lado": "buy",
                "quantidade": 3.0,
                "entrada": 150.0,
                "preco_atual": 155.0,
                "stop": 145.0,
                "alvo": 165.0,
                "aberta_em": "2026-10-14 13:00:00",
            }
        },
        "ordens": {},
        "historico": [],
    }
    (data_dir / "paper_futures.json").write_text(json.dumps(paper_content), encoding="utf-8")

    provider = BotDataProvider(root_dir=tmp_path)
    m = provider.get_metrics()

    assert m.active_position is not None
    assert m.active_position.symbol == "SOL/USDT:USDT"
    assert m.active_position.pnl == 15.0  # (155 - 150) * 3
    assert round(m.active_position.pnl_pct, 2) == 3.33


def test_reverse_chronological_trades(tmp_path: Path):
    """Valida que recent_trades retorna as operações mais recentes primeiro."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    paper_content = {
        "capital_inicial": 1000.0,
        "saldo": 1050.0,
        "historico": [
            {"ts": "2026-10-14 09:00:00", "pnl": 10.0, "symbol": "SOL/USDT:USDT", "side": "buy", "id": "t1"},
            {"ts": "2026-10-14 10:00:00", "pnl": 20.0, "symbol": "SOL/USDT:USDT", "side": "buy", "id": "t2"},
            {"ts": "2026-10-14 11:00:00", "pnl": 30.0, "symbol": "SOL/USDT:USDT", "side": "buy", "id": "t3"},
        ],
    }
    (data_dir / "paper_futures.json").write_text(json.dumps(paper_content), encoding="utf-8")

    provider = BotDataProvider(root_dir=tmp_path)
    m = provider.get_metrics()

    assert len(m.recent_trades) == 3
    # Mais recente primeiro
    assert m.recent_trades[0].exchange_id == "t3"
    assert m.recent_trades[1].exchange_id == "t2"
    assert m.recent_trades[2].exchange_id == "t1"


def test_infinite_profit_factor_on_zero_losses(tmp_path: Path):
    """Valida Profit Factor infinito quando há lucros e zero perdas."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    paper_content = {
        "capital_inicial": 1000.0,
        "saldo": 1050.0,
        "historico": [
            {"ts": "2026-10-14 10:00:00", "pnl": 25.0, "symbol": "SOL/USDT:USDT", "side": "buy"},
            {"ts": "2026-10-14 11:00:00", "pnl": 25.0, "symbol": "SOL/USDT:USDT", "side": "buy"},
        ],
    }
    (data_dir / "paper_futures.json").write_text(json.dumps(paper_content), encoding="utf-8")

    provider = BotDataProvider(root_dir=tmp_path)
    m = provider.get_metrics()

    assert m.winning_trades == 2
    assert m.losing_trades == 0
    assert m.profit_factor == float("inf")


def test_live_kucoin_status_indicator(monkeypatch):
    """Valida detecção de credenciais KuCoin no modo live."""
    provider = BotDataProvider()

    # Cenário sem chaves
    monkeypatch.delenv("KUCOIN_API_KEY", raising=False)
    monkeypatch.delenv("KUCOIN_API_SECRET", raising=False)
    monkeypatch.delenv("KUCOIN_API_PASSPHRASE", raising=False)
    m_no_keys = provider.get_metrics()
    assert m_no_keys.kucoin_configured is False

    # Cenário com chaves
    monkeypatch.setenv("KUCOIN_API_KEY", "test_key")
    monkeypatch.setenv("KUCOIN_API_SECRET", "test_secret")
    monkeypatch.setenv("KUCOIN_API_PASSPHRASE", "test_pass")
    m_with_keys = provider.get_metrics()
    assert m_with_keys.kucoin_configured is True

