# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A-stock-advisor is an A-share (Chinese stock market) multi-factor quantitative investment strategy system. It combines fundamental screening (25%), technical analysis (40%), momentum (10%), and capital flow (25%) to generate daily stock picks with risk management and equity pledge avoidance.

Data sources: akshare (primary), tushare Pro (fallback), mock data (last resort). The system auto-degrades through sources if one fails.

## Common Commands

```bash
# Daily stock picking (live data via akshare)
python main.py

# Demo mode (uses mock data, fast, no network needed)
python main.py --demo

# Quick strategy validation (tests single stocks)
python test_strategy.py

# Backtest: 3-month weekly rotation
python backtest.py --period 3m --hold-mode weekly

# Backtest: specific month
python backtest.py --month 2026-05

# Backtest: demo mode (mock data)
python backtest.py --demo

# Generate mock data CSV
python utils/mock_data.py
```

## Architecture

### Pipeline Flow (`main.py`)

1. **Market environment check** — Shanghai Composite Index vs MA250, northbound capital
2. **Stock list fetch** — Full A-share market via `DataFetcher`
3. **Equity pledge data** — Fetch pledge ratios via akshare (`get_pledge_ratio_data()`)
4. **Fundamental screening** — `FundamentalScreener` filters by: ST exclusion, sector whitelist/blacklist, market cap, PE/PB/dividend yield, **equity pledge avoidance**
5. **Capital flow data** — Bulk fetch moneyflow/top_list/top_inst via tushare (optional, may fail gracefully)
6. **Per-stock analysis** (multithreaded) — Technical + momentum + signal combination + risk advice
7. **Sector limit** — Max 2 stocks per sector
8. **Pre-market order planning** — Generates limit order prices, gap rules, conditional stop/take-profit orders
9. **Report generation** — Markdown report + console output via `DailyReport`

### Module Responsibilities

| Module | Class | Role |
|--------|-------|------|
| `utils/data_fetcher.py` | `DataFetcher` | Multi-source data abstraction (akshare → tushare → mock). Caches results. Provides: stock list, historical K-lines, index data, financial indicators, northbound/margin data, **moneyflow/top_list/top_inst**, **equity pledge ratios** |
| `strategies/fundamental.py` | `FundamentalScreener` | Screens stocks by industry, market cap, valuation (PE/PB/dividend). Accepts injected pledge data via `set_pledge_data()` to exclude high-pledge stocks. Scores individual stocks -1..1 |
| `strategies/technical.py` | `TechnicalAnalyzer` | Computes MA/MACD/RSI/KDJ/ATR/volume signals. Detects divergence, breakout, box patterns, volume-price confirmation. MACD zero-axis filter (below-zero golden cross ignored). Scores -1..1 |
| `strategies/signal_engine_v2.py` | `SignalEngineV2` | **Core orchestrator**. Calculates momentum (5d/20d/60d returns) with trend quality + volatility. Calculates capital flow score from moneyflow/top_list/top_inst. Combines fundamental + technical + momentum + capital into weighted total score (-1..1). Includes veto rules, signal conflict resolution, and trend filter. Maps to advice levels: 强烈关注/关注/轻度关注/观望/谨慎/回避 |
| `strategies/risk_manager.py` | `RiskManager` | Stop-loss, trailing stop, target price, position sizing based on conviction score. Portfolio-level advice (max holdings, cash reserve) |
| `strategies/pre_market.py` | `PreMarketPlanner` | Generates pre-market limit order prices (based on MA20 proximity), gap-up/gap-down decision matrix, and conditional stop-loss/take-profit order parameters for stocks rated 关注 or above |
| `reports/daily_report.py` | `DailyReport` | Generates Markdown reports and rich console tables, including pre-market order guide section |
| `backtest.py` | `TushareBacktester` | Historical backtesting. Supports weekly rotation (`--hold-mode weekly`) or hold-to-end. Uses **ATR inverse-volatility position weighting** for portfolio returns. Computes cumulative returns, max drawdown, Sharpe/Sortino/Calmar ratios, factor IC analysis. Integrates pledge avoidance |

### Key Design Patterns

**Multi-source auto-degradation**: `DataFetcher` tries akshare first, falls back to tushare, then mock. Any single working source is sufficient.

**Score normalization**: Every analyzer returns scores in [-1, 1]. `SignalEngine.combine()` weights them according to `config/signal_weights`.

**Config-driven**: All thresholds, weights, and risk parameters live in `config/settings.yaml`. Change the YAML to tune strategy behavior without code changes.

**Equity pledge avoidance**: Pledge data is fetched via akshare (`stock_gpzy_pledge_ratio_em`) and injected into `FundamentalScreener`. Stocks with pledge ratio ≥ `pledge_avoidance.threshold_pct` (default 30%) are auto-excluded. High-risk threshold (default 50%) marks them as 高风险.

## Configuration

- `config/settings.yaml` — Strategy configuration (investment style, thresholds, signal weights, risk params, pledge avoidance)
- `config/.tushare_token` — Tushare Pro API token (copy from `.tushare_token.example`)
- `data/` — Cache directory for mock data CSV

### Important Config Sections

- `investment_style`: `conservative` / `balanced` / `aggressive` — determines market cap range
- `data_source`: `auto` / `akshare` / `tushare` / `mock`
- `stock_pool.preferred_sectors` / `excluded_sectors` — industry whitelist/blacklist
- `pledge_avoidance` — Equity pledge filtering (enabled, threshold_pct, high_risk_pct)
- `signal_weights` — Weight allocation across fundamental/technical/momentum/capital_flow
- `risk_management` — Stop-loss, trailing stop, target profit, MA exit rules
- `pre_market_order` — Pre-market limit order pricing, gap open thresholds, order validity
- `position_management` — Max holdings, single position limits, sector limits, cash reserve

## Important Notes

- **Windows console encoding**: The project runs on Windows with GBK console encoding. Do NOT use emoji (⚠ 🔴 🟡) or special Unicode characters in `print()` statements — they cause `UnicodeEncodeError`. Use ASCII equivalents like `[!]`, `[高风险]`, `[警戒线]` instead. Markdown output files are fine with Unicode.
- **Capital flow in backtests**: Historical moneyflow/top_list data is not available, so backtests only use turnover rate + volume ratio for capital_flow scoring (25% weight). Live runs (`main.py`) include full moneyflow/top_list/inst data when available.
- **Pledge data in backtests**: Uses latest pledge data as approximation for historical periods. Pledge ratios change slowly, so this is reasonable for short-term backtests.
- **No formal test framework**: `test_strategy.py` is a manual validation script, not a pytest suite.
- **No requirements.txt**: Dependencies are akshare, tushare, pandas, numpy, pyyaml, rich. Install as needed.

## Strategy Performance (2026-06 latest)

6-month backtest (2025-12 ~ 2026-06, weekly rotation):

| Metric | Value |
|--------|-------|
| Cumulative Return | +41.5% |
| Excess vs SSE | +45.1% |
| Max Drawdown | 5.3% |
| Sharpe Ratio | 2.49 |
| Sortino Ratio | 13.6 |
| Calmar Ratio | 17.4 |
| Win Rate | 46% |
| Beta | 0.36 |
| Info Ratio | 2.70 |

## Confirmed Improvements (v2.3)

| # | Improvement | File(s) | Effect |
|---|------------|---------|--------|
| P0 | Stop-loss below current price fix | risk_manager.py | Fixes critical bug: support broken → use % stop |
| P1-1 | Trend filter (MA60/MA250 penalty) | signal_engine_v2.py, technical.py | Prevents catching falling knives, Beta 0.36 |
| P1-2 | Volatility variable scope fix | signal_engine_v2.py | Fixes UnboundLocalError risk |
| — | Momentum 15%→10%, technical 35%→40% | settings.yaml, market_regime.py | IC-driven weight optimization |
| P2 | Volume-price confirmation | technical.py | 放量+涨=做多, 放量+跌=出货 |
| P3 | Delete old SignalEngine v1 | signal_engine.py (deleted) | Code cleanup |
| P4 | ATR inverse-volatility position weighting | technical.py, backtest.py | Major improvement: +20% cumulative |
| P5 | MACD zero-axis filter | technical.py | Below-zero golden cross ignored (fake signal) |
