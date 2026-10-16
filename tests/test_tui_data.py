import os
from pathlib import Path
import pytest
from dashboard.tui_data import BotDataProvider, BotMetrics, TradeRecord, PositionInfo

def test_data_provider_demo_metrics():
    provider = BotDataProvider()
    m = provider.generate_demo_metrics()
    assert isinstance(m, BotMetrics)
    assert m.is_demo is True
    assert m.status == "SIMULAÇÃO (DEMO)"
    assert m.initial_capital == 1000.0
    assert m.current_balance > m.initial_capital
    assert m.profit_total > 0
    assert m.win_rate_pct > 0
    assert len(m.recent_trades) > 0
    assert m.active_position is not None
    assert m.active_position.symbol == "SOL/USDT:USDT"
    assert m.strategy_name == "apex-dinamics"
    assert len(m.recent_logs) > 0

def test_data_provider_real_metrics():
    provider = BotDataProvider()
    m = provider.get_metrics(force_demo=False)
    assert isinstance(m, BotMetrics)
    assert m.status in ("ONLINE", "OFFLINE", "PAUSADO - CIRCUIT BREAKER")
    assert m.latency_ms >= 0
    assert isinstance(m.recent_trades, list)
    assert isinstance(m.open_orders, list)
    assert isinstance(m.recent_logs, list)

def test_uptime_calculation():
    provider = BotDataProvider()
    # Test valid string
    res = provider._calcular_uptime("2026-10-14 10:00:00")
    assert ("h" in res and "m" in res) or "d" in res
    # Test empty or None
    assert provider._calcular_uptime(None) == "00:00:00"
    assert provider._calcular_uptime("") == "00:00:00"

def test_pid_alive_check():
    provider = BotDataProvider()
    # Current process should be alive
    current_pid = os.getpid()
    assert provider._is_pid_alive(current_pid) is True
    # Negative or 0 should be False
    assert provider._is_pid_alive(0) is False
    assert provider._is_pid_alive(-999) is False

def test_custom_root_dir(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    provider = BotDataProvider(root_dir=tmp_path)
    metrics = provider.get_metrics(force_demo=False)
    assert metrics.status == "OFFLINE"
    assert metrics.total_trades == 0
    assert metrics.profit_total == 0.0
