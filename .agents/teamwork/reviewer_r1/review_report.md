# Review & Adversarial Verification Report: Cryptobot TUI

## 1. Summary of Prior Attempt Audit
The initial attempt established a baseline Textual application and decoupled reader, but an adversarial review revealed several defects, edge-case omissions, and acceptance criteria failures:
1. **Acceptance Criteria Failure (Exit with Ctrl+C):** `CryptobotTUI.BINDINGS` did not bind `ctrl+c`. In raw terminal mode, pressing `Ctrl+C` was ignored (`app._exit == False`).
2. **Broken Tab Navigation When Widget Focused:** Bindings for tabs (`1`, `2`, `3`, `4`) and pause (`p`) lacked `priority=True` and did not clear focus when switching tabs. When a `DataTable` held focus on Tab 2, pressing `1` was intercepted by Textual's focus manager and reverted back to `tab-trades`.
3. **Unrealized Position PnL Never Computed on Real Data:** `BotDataProvider.get_metrics()` constructed `active_pos` without calculating `pnl` or `pnl_pct`. As a result, the live TUI card always displayed `+$0.00 (+0.00%)` for open positions.
4. **Oldest Trades Displayed Instead of Newest:** `paper.py` appends trades chronologically to `historico`. `tui_data.py` took `trades[:25]` without reversing, displaying the oldest trades instead of the latest trades.
5. **Paper Position Ignored when `bot.db` Has No `posicao`:** In standalone paper simulation, open positions exist in `paper_futures.json`. The data provider only inspected `bot.db`, showing "Nenhuma posição aberta".
6. **Layout Crushed on Narrow / Vintage Terminals (<85 cols):** `#overview-grid` had a fixed 2x2 grid with `height: 1fr` and no scrolling. On small terminals (e.g. 70x20 or legacy conhost), card contents were severely clipped. Added `VerticalScroll(id="overview-scroll")` and dynamic responsive layout switching to single-column (`narrow`) when width < 85.
7. **DataTable Selection Reset on Every Refresh Tick:** Unconditional `table.clear()` reset `cursor_row` to 0 every second. Implemented cursor position preservation so scrolling through trades remains stable.
8. **Mathematical Inaccuracy for Zero Losses:** Profit factor hardcoded `100.0` on 0 losses; corrected to `float("inf")` and rendered as `∞ (Sem perdas)`.
9. **KuCoin Credential Visibility in Live Mode:** When in `live` mode, added detection and visual indicator of whether KuCoin API keys are present in `.env`.

## 2. Changes Made
- `dashboard/tui_app.py`:
  - Added `Binding("ctrl+c", "quit_app", priority=True)`.
  - Added `priority=True` to all hotkey bindings (`1-4`, `p`, `space`, `r`, `d`, `q`).
  - Added `_switch_tab()` method that clears focus prior to changing `TabbedContent.active`.
  - Wrapped `#overview-grid` in `VerticalScroll(id="overview-scroll")`.
  - Added dynamic responsive handler `on_resize()` using `event.size.width` to toggle CSS class `.narrow`.
  - Preserved `cursor_row` across `_refresh_tables()` updates.
  - Added KuCoin credentials indicator in live mode and formatted infinite profit factor (`∞ (Sem perdas)`).
- `dashboard/tui_data.py`:
  - Added `kucoin_configured: bool = False` to `BotMetrics`.
  - Calculated `pnl` and `pnl_pct` on `active_pos` in `get_metrics()`.
  - Added fallback to `paper_data["posicoes"]` when `bot.db` has no active position.
  - Reversed `historico_paper` trades so recent trades appear first (`recent_trades[0]` = latest trade).
  - Calculated `profit_factor = float("inf")` on positive wins and zero losses.
  - Hardened float parsing with `try...except (ValueError, TypeError)`.
- `tui.ps1`:
  - Made `$PSScriptRoot` detection resilient to execution contexts.
- `aliases.ps1`:
  - Added `[double]$Interval = 1.0` parameter to `/tui` command alias.
- `tests/test_tui_app.py`:
  - Added `test_tui_exit_with_ctrl_c`.
  - Added `test_tui_tab_switching_with_focused_table`.
  - Added `test_tui_responsive_resize`.
  - Added `test_tui_table_cursor_preserved_across_ticks`.
- `tests/test_tui_edge_cases.py`:
  - Added `test_active_position_pnl_calculation`.
  - Added `test_paper_position_fallback`.
  - Added `test_reverse_chronological_trades`.
  - Added `test_infinite_profit_factor_on_zero_losses`.
  - Added `test_live_kucoin_status_indicator`.

## 3. Automated Verification
Ran `pytest tests/ -v`:
- 25 passed in 7.68s across `test_tui_app.py`, `test_tui_data.py`, `test_tui_edge_cases.py`.
- Static compile checks verified on all modules.
