"""Testes automatizados completos para o Dashboard TUI do Cryptobot.

Valida:
1. Inicialização e integridade dos 4 blocos principais (Status, Lucro, Estratégia, Operações).
2. Atalhos de teclado interativos (Pausar/Retomar, Modo Demo, Forçar Sincronização, Abas).
3. Encerramento limpo com 'q'.
4. Consistência e atualização dinâmica das métricas.
"""
from __future__ import annotations

from io import StringIO
import pytest
from rich.console import Console
from textual.widgets import DataTable, Static, TabbedContent

from dashboard.tui_app import CryptobotTUI
from dashboard.tui_data import BotDataProvider


def get_rendered_text(widget: Static) -> str:
    """Extrai o texto renderizado completo de um widget Static com Rich renderable."""
    buf = StringIO()
    console = Console(file=buf, width=120, force_terminal=False)
    rendered = widget.render()
    inner = getattr(rendered, "_renderable", rendered)
    console.print(inner)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_tui_initialization_and_four_sections():
    """Valida a inicialização da TUI e a presença dos 4 blocos visuais obrigatórios."""
    app = CryptobotTUI(demo_mode=True, refresh_interval=1.0)
    async with app.run_test() as pilot:
        # 1. Status do Bot
        card_status = pilot.app.query_one("#card-status", Static)
        assert card_status is not None
        text_status = get_rendered_text(card_status)
        assert "Status" in text_status
        assert "PID" in text_status

        # 2. Lucro & Métricas Financeiras
        card_profit = pilot.app.query_one("#card-profit", Static)
        assert card_profit is not None
        text_profit = get_rendered_text(card_profit)
        assert "Capital" in text_profit or "Saldo" in text_profit
        assert "Win Rate" in text_profit or "Lucro" in text_profit

        # 3. Estratégia Ativa
        card_strategy = pilot.app.query_one("#card-strategy", Static)
        assert card_strategy is not None
        text_strategy = get_rendered_text(card_strategy)
        assert "Estratégia" in text_strategy
        assert "Par" in text_strategy

        # 4. Painel de Operações Recentes
        card_trades = pilot.app.query_one("#card-trades-summary", Static)
        assert card_trades is not None
        text_trades = get_rendered_text(card_trades)
        assert "Operações" in text_trades


@pytest.mark.asyncio
async def test_tui_toggle_demo_mode():
    """Valida o atalho 'd' para alternar entre modo real e demonstração."""
    app = CryptobotTUI(demo_mode=False, refresh_interval=1.0)
    async with app.run_test() as pilot:
        assert app.demo_mode is False

        # Pressiona 'd' para alternar para modo demo
        await pilot.press("d")
        assert app.demo_mode is True
        assert app.metrics.is_demo is True

        # Pressiona 'd' novamente para voltar ao modo real
        await pilot.press("d")
        assert app.demo_mode is False


@pytest.mark.asyncio
async def test_tui_pause_and_resume_refresh():
    """Valida os atalhos de pausar e retomar refresh (Espaço e 'p')."""
    app = CryptobotTUI(demo_mode=True, refresh_interval=1.0)
    async with app.run_test() as pilot:
        assert app.is_paused is False

        # Pausar via 'space'
        await pilot.press("space")
        assert app.is_paused is True
        sync_label = get_rendered_text(pilot.app.query_one("#sync-indicator", Static))
        assert "PAUSADO" in sync_label

        # Retomar via 'p'
        await pilot.press("p")
        assert app.is_paused is False
        sync_label_resumed = get_rendered_text(pilot.app.query_one("#sync-indicator", Static))
        assert "AO VIVO" in sync_label_resumed


@pytest.mark.asyncio
async def test_tui_force_refresh_now():
    """Valida o atalho 'r' para forçar sincronização imediata."""
    app = CryptobotTUI(demo_mode=True, refresh_interval=1.0)
    async with app.run_test() as pilot:
        await pilot.press("r")
        assert app.metrics is not None


@pytest.mark.asyncio
async def test_tui_tab_navigation():
    """Valida navegação entre abas via atalhos numéricos 1, 2, 3, 4."""
    app = CryptobotTUI(demo_mode=True, refresh_interval=1.0)
    async with app.run_test() as pilot:
        tabs = pilot.app.query_one("#main-tabs", TabbedContent)

        # Aba 2: Operações
        await pilot.press("2")
        assert tabs.active == "tab-trades"
        table_trades = pilot.app.query_one("#table-trades", DataTable)
        assert table_trades.row_count > 0

        # Aba 3: Estratégia
        await pilot.press("3")
        assert tabs.active == "tab-strategy"
        strat_details = pilot.app.query_one("#card-strategy-details", Static)
        assert "QUANTITATIVA" in get_rendered_text(strat_details)

        # Aba 4: Logs
        await pilot.press("4")
        assert tabs.active == "tab-logs"
        logs_content = pilot.app.query_one("#logs-content", Static)
        assert logs_content is not None

        # Aba 1: Visão Geral
        await pilot.press("1")
        assert tabs.active == "tab-overview"


@pytest.mark.asyncio
async def test_tui_clean_exit():
    """Valida encerramento limpo ao pressionar 'q'."""
    app = CryptobotTUI(demo_mode=True, refresh_interval=1.0)
    async with app.run_test() as pilot:
        await pilot.press("q")
        assert app._exit is True


@pytest.mark.asyncio
async def test_tui_exit_with_ctrl_c():
    """Valida encerramento limpo com Ctrl+C conforme critério de aceitação."""
    app = CryptobotTUI(demo_mode=True, refresh_interval=1.0)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+c")
        assert app._exit is True


@pytest.mark.asyncio
async def test_tui_tab_switching_with_focused_table():
    """Valida que atalhos 1, 2, 3, 4 têm prioridade mesmo com tabela focada."""
    app = CryptobotTUI(demo_mode=True, refresh_interval=1.0)
    async with app.run_test() as pilot:
        tabs = pilot.app.query_one("#main-tabs", TabbedContent)
        await pilot.press("2")
        assert tabs.active == "tab-trades"

        # Foca na tabela de trades
        table = pilot.app.query_one("#table-trades", DataTable)
        table.focus()
        await pilot.pause()

        # Pressiona 1 para voltar para a aba de visão geral
        await pilot.press("1")
        assert tabs.active == "tab-overview"


@pytest.mark.asyncio
async def test_tui_responsive_resize():
    """Valida adaptação dinâmica do layout para terminais pequenos (<85 cols)."""
    app = CryptobotTUI(demo_mode=True, refresh_interval=1.0)
    async with app.run_test(size=(100, 30)) as pilot:
        grid = pilot.app.query_one("#overview-grid")
        assert "narrow" not in grid.classes

        # Redimensiona para terminal vintage / conhost estreito (70 cols)
        await pilot.resize_terminal(70, 24)
        assert "narrow" in grid.classes

        # Retorna para resolução ampla (100 cols)
        await pilot.resize_terminal(100, 30)
        assert "narrow" not in grid.classes


@pytest.mark.asyncio
async def test_tui_table_cursor_preserved_across_ticks():
    """Valida que a posição do cursor na tabela não é resetada para a linha 0 nos ticks."""
    app = CryptobotTUI(demo_mode=True, refresh_interval=1.0)
    async with app.run_test() as pilot:
        await pilot.press("2")
        table = pilot.app.query_one("#table-trades", DataTable)
        assert table.row_count >= 3
        table.move_cursor(row=2)
        assert table.cursor_row == 2

        # Dispara atualização periódica assíncrona
        await pilot.app._tick_refresh()
        assert table.cursor_row == 2

