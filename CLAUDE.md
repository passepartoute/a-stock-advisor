# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A-stock-advisor is an A-share (Chinese stock market) multi-factor quantitative investment strategy system. It combines fundamental screening (20%), technical analysis (25%), momentum (10%), and capital flow (45%) to generate daily stock picks with risk management and equity pledge avoidance.

Data sources: akshare (primary), tushare Pro (fallback), mock data (last resort). The system auto-degrades through sources if one fails.

## Common Commands

```bash
# Daily stock picking (uses configured data source, default tushare)
python main.py

# Demo mode (uses mock data, fast, no network needed)
python main.py --demo

# AI demo: real market data + mocked AI signals (no LLM API key required)
python run_ai_demo.py

# Run the test suite (uses stdlib unittest via run_tests.py)
python run_tests.py
python run_tests.py -v

# Run a single test file directly with unittest
python -m unittest tests.test_signal_engine_v2 -v

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

# Format / lint / type check (manual, no build tool configured)
black .
ruff check .
mypy .
```

## Architecture

### Pipeline Flow (`main.py`)

1. **Market environment check** — Shanghai Composite Index vs MA250, northbound capital
2. **Stock list fetch** — Full A-share market via `DataFetcher`
3. **Equity pledge data** — Fetch pledge ratios via akshare (`get_pledge_ratio_data()`)
4. **Fundamental screening** — `FundamentalScreener` filters by: ST exclusion, sector whitelist/blacklist, market cap, PE/PB/dividend yield, **equity pledge avoidance**
5. **Capital flow data** — Bulk fetch moneyflow/top_list/top_inst via tushare (optional, may fail gracefully)
6. **News/sentiment data** — Fetch overnight news, comment sentiment, broker ratings for candidates (`NewsSentimentAnalyzer` + `DataFetcher`)
7. **AI macro briefing** — One LLM call per day (`AIBriefingAnalyzer` + `LLMClient`) extracts structured macro signals from a live finance briefing
8. **Per-stock analysis** (multithreaded) — Technical + momentum + signal combination + risk advice; `AIFactorAdjuster` applies AI-derived sector/score adjustments
9. **Sentiment veto/downgrade** — Apply news-based veto/downgrade to results before sector diversification
10. **Sector limit** — Max 2 stocks per sector
11. **Pre-market order planning** — Generates limit order prices, gap rules, conditional stop/take-profit orders; skips vetoed/downgraded stocks
12. **Report generation** — Markdown report + console output via `DailyReport`, including sentiment filter list

### Module Responsibilities

| Module | Class | Role |
|--------|-------|------|
| `utils/data_fetcher.py` | `DataFetcher` | Multi-source data abstraction (akshare → tushare → mock). Caches results. Provides: stock list, historical K-lines, index data, financial indicators, northbound/margin data, **moneyflow/top_list/top_inst**, **equity pledge ratios**, **news/comment/rating data**, **AI briefing raw text** (akshare media news + tushare `anns_d` official announcements) |
| `utils/llm_client.py` | `LLMClient` | Unified LLM client for kimi/openai/anthropic/ollama. Loads keys from file or environment |
| `strategies/fundamental.py` | `FundamentalScreener` | Screens stocks by industry, market cap, valuation (PE/PB/dividend). Accepts injected pledge data via `set_pledge_data()` to exclude high-pledge stocks. Scores individual stocks -1..1 |
| `strategies/technical.py` | `TechnicalAnalyzer` | Computes MA/MACD/RSI/KDJ/ATR/volume signals. Detects divergence, breakout, box patterns, volume-price confirmation. MACD zero-axis filter (below-zero golden cross ignored). Scores -1..1 |
| `strategies/signal_engine_v2.py` | `SignalEngineV2` | **Core orchestrator**. Calculates momentum (5d/20d/60d returns) with trend quality + volatility. Calculates capital flow score from moneyflow/top_list/top_inst. Combines fundamental + technical + momentum + capital into weighted total score (-1..1). Includes veto rules, signal conflict resolution, and trend filter. Maps to advice levels: 强烈关注/关注/轻度关注/观望/谨慎/回避 |
| `strategies/market_regime.py` | `MarketRegimeDetector` | Detects market regime (bull/bear/neutral) and shifts factor weights dynamically |
| `strategies/risk_manager.py` | `RiskManager` | Stop-loss, trailing stop, target price, position sizing based on conviction score. Portfolio-level advice (max holdings, cash reserve) |
| `strategies/news_sentiment.py` | `NewsSentimentAnalyzer` | **Pre-market sentiment filter**. Scans news titles (media + official announcements), comment sentiment, and broker ratings. Returns `pass` / `downgrade` / `veto`. Bad news can skip orders; good news does not directly add score. Data-missing stocks pass neutrally |
| `strategies/ai_briefing.py` | `AIBriefingAnalyzer` | Fetches a daily finance briefing and calls the LLM once to extract structured macro signals (sentiment, hot/cold sectors, policy themes, risk events, style bias, stock mentions, veto keywords) |
| `strategies/ai_factor_adjuster.py` | `AIFactorAdjuster` | Maps AI macro signals to quant adjustments: shifts factor weights, adjusts per-stock fundamental scores by sector heat/policy/risk, and overlays sentiment for mentioned stocks |
| `strategies/pre_market.py` | `PreMarketPlanner` | Generates pre-market limit order prices (based on MA20 proximity), gap-up/gap-down decision matrix, and conditional stop-loss/take-profit order parameters for stocks rated 关注 or above. Records sentiment-skipped stocks |
| `reports/daily_report.py` | `DailyReport` | Generates Markdown reports and rich console tables, including pre-market order guide section and sentiment filter list |
| `backtest.py` | `TushareBacktester` | Historical backtesting. Supports weekly rotation (`--hold-mode weekly`) or hold-to-end. Uses **ATR inverse-volatility position weighting** for portfolio returns. Computes cumulative returns, max drawdown, Sharpe/Sortino/Calmar ratios, factor IC analysis. Integrates pledge avoidance |

### Key Design Patterns

**Multi-source auto-degradation**: `DataFetcher` tries akshare first, falls back to tushare, then mock. Any single working source is sufficient.

**Score normalization**: Every analyzer returns scores in [-1, 1]. `SignalEngine.combine()` weights them according to `config/signal_weights`.

**Config-driven**: All thresholds, weights, and risk parameters live in `config/settings.yaml`. Change the YAML to tune strategy behavior without code changes.

**Equity pledge avoidance**: Pledge data is fetched via akshare (`stock_gpzy_pledge_ratio_em`) and injected into `FundamentalScreener`. Stocks with pledge ratio ≥ `pledge_avoidance.threshold_pct` (default 30%) are auto-excluded. High-risk threshold (default 50%) marks them as 高风险.

**Optional AI layer**: `AIBriefingAnalyzer` makes one LLM call per run. If the LLM call fails, returns invalid JSON, or the key is missing, `main.py` falls back to the pure quant pipeline. `AIFactorAdjuster` is a pure-function mapper so it is easy to unit test and safe to skip.

## Configuration

- `config/settings.yaml` — Strategy configuration (investment style, thresholds, signal weights, risk params, pledge avoidance, AI briefing)
- `config/.tushare_token` — Tushare Pro API token (copy from `.tushare_token.example`)
- `config/.llm_api_key` — LLM API key for the AI briefing layer (copy from `.llm_api_key.example`)
- `data/` — Cache directory for mock data CSV

### Important Config Sections

- `investment_style`: `conservative` / `balanced` / `aggressive` — determines market cap range
- `data_source`: `auto` / `akshare` / `tushare` / `mock`
- `stock_pool.preferred_sectors` / `excluded_sectors` — industry whitelist/blacklist
- `pledge_avoidance` — Equity pledge filtering (enabled, threshold_pct, high_risk_pct)
- `signal_weights` — Weight allocation across fundamental/technical/momentum/capital_flow
- `market_regime` — Market-state detection and dynamic weight shifts
- `risk_management` — Stop-loss, trailing stop, target profit, MA exit rules
- `pre_market_order` — Pre-market limit order pricing, gap open thresholds, order validity
- `position_management` — Max holdings, single position limits, sector limits, cash reserve
- `news_sentiment` — Enable/disable sentiment filter, data source (`akshare`/`tushare`/`auto`), keywords, age limit, veto rules
- `pre_market_info` — Action on negative news (`veto`/`downgrade`), downgrade mapping, max daily affected
- `ai_briefing` — Enable/disable AI macro briefing, LLM provider/model, API key file, factor adjustments

## Important Notes

- **Windows console encoding**: The project runs on Windows with GBK console encoding. Do NOT use emoji (⚠ 🔴 🟡) or special Unicode characters in `print()` statements — they cause `UnicodeEncodeError`. Use ASCII equivalents like `[!]`, `[高风险]`, `[警戒线]` instead. Markdown output files are fine with Unicode.
- **Test runner**: `run_tests.py` discovers and runs the stdlib `unittest` suite under `tests/`. `requirements-dev.txt` also lists `pytest`, but the active runner is `unittest`. Run a single file with `python -m unittest tests.test_<name> -v`.
- **News/sentiment data sources**: `akshare.stock_news_em` provides media news (market hotspots, broker comments); `tushare.pro.anns_d` provides official exchange announcements (regulatory inquiries, earnings warnings). Use `news_sentiment.data_source: auto` to merge both. Demo mode skips sentiment fetching.
- **News/sentiment in backtests**: Historical news sentiment is not currently backfilled, so backtests do not apply the sentiment veto layer. It is active only in live runs (`main.py`) by design, to avoid look-ahead bias.
- **Capital flow in backtests**: Historical moneyflow/top_list data is not available, so backtests only use turnover rate + volume ratio for capital_flow scoring (45% weight). Live runs (`main.py`) include full moneyflow/top_list/inst data when available.
- **Pledge data in backtests**: Uses latest pledge data as approximation for historical periods. Pledge ratios change slowly, so this is reasonable for short-term backtests.
- **AI briefing fallback**: The AI briefing layer is optional. If the LLM call fails, the API key is missing, or the response cannot be parsed, `main.py` falls back to the pure quant pipeline.
- **AI briefing in backtests**: The AI briefing layer is not backfilled and is disabled in `backtest.py` to avoid look-ahead bias.

## Strategy Performance (2026-07 latest)

6-month backtest (2026-01 ~ 2026-07, weekly rotation):

| Metric | Value |
|--------|-------|
| Cumulative Return | +45.0% |
| Excess vs SSE | +51.3% |
| Max Drawdown | 5.1% |
| Sharpe Ratio | 4.82 |
| Sortino Ratio | 12.28 |
| Calmar Ratio | 58.3 |
| Win Rate | 75% |
| Beta | 1.83 |
| Info Ratio | 5.75 |

## GitHub Pages Custom Domain

Reports are deployed to the `gh-pages` branch and can be served via a custom domain instead of the default `https://passepartoute.github.io/a-stock-advisor/` URL.

### Configuration

1. Set the repository variable `CUSTOM_DOMAIN` under **Settings → Secrets and variables → Actions → Variables**.
   - Example value: `myasibo.cc`
2. The workflow (`generate-report.yml`) writes a `CNAME` file into `_site` automatically when `CUSTOM_DOMAIN` is set, so the custom domain persists across deployments.
3. Configure DNS records for `myasibo.cc`. For this GitHub Pages project site, point the apex domain to the GitHub Pages IPs and use a `www` CNAME:
   - A records for `@`: `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`
   - AAAA records for `@`: `2606:50c0:8000::153`, `2606:50c0:8001::153`, `2606:50c0:8002::153`, `2606:50c0:8003::153`
   - CNAME record for `www`: `passepartoute.github.io`
4. In the repository, go to **Settings → Pages → Custom domain**, enter `myasibo.cc`, and wait for the DNS check and SSL certificate to complete.
5. Enable **Enforce HTTPS** once the certificate is issued.

If `CUSTOM_DOMAIN` is not set, the workflow falls back to the default GitHub Pages URL in the email notification.

## Confirmed Improvements (v2.4)

| # | Improvement | File(s) | Effect |
|---|------------|---------|--------|
| P0 | Stop-loss below current price fix | risk_manager.py | Fixes critical bug: support broken → use % stop |
| P1-1 | Trend filter (MA60/MA250 penalty) | signal_engine_v2.py, technical.py | Prevents catching falling knives, Beta 0.36 |
| P1-2 | Volatility variable scope fix | signal_engine_v2.py | Fixes UnboundLocalError risk |
| — | Momentum 15%→10%, technical 35%→40% | settings.yaml, market_regime.py | IC-driven weight optimization |
| — | Technical 40%→25%, capital_flow 25%→45%; valuation tighten (PE 100→50, PB 8→5) | settings.yaml | Further IC-driven optimization: +45.0% / 5.1% max drawdown |
| P2 | Volume-price confirmation | technical.py | 放量+涨=做多, 放量+跌=出货 |
| P6 | Pre-market news/sentiment veto layer | strategies/news_sentiment.py, utils/data_fetcher.py, main.py, reports/daily_report.py | Adds overnight news, comment sentiment, broker ratings as a pre-market filter; supports akshare media news + tushare `anns_d` official announcements |
| P3 | Delete old SignalEngine v1 | signal_engine.py (deleted) | Code cleanup |
| P4 | ATR inverse-volatility position weighting | technical.py, backtest.py | Major improvement: +20% cumulative |
| P5 | MACD zero-axis filter | technical.py | Below-zero golden cross ignored (fake signal) |
