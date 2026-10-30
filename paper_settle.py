# -*- coding: utf-8 -*-
"""虚拟盘结算 CLI。

用法:
    python paper_settle.py                    # 追赶模式：结算所有未结算的交易日
    python paper_settle.py --init             # 显式初始化（已存在需 --force）
    python paper_settle.py --date 2026-10-29  # 结算指定日
    python paper_settle.py --replay           # 重置账本，从最早挂单重放
    python paper_settle.py --ledger-dir _site --orders-dir _site   # workflow 用

结算时序：main.py 盘前生成的 orders_T.json 针对 T 日开盘，T 日 K 线收盘后
才可得。追赶模式扫描 last_settled_date 之后、已有收盘行情的所有交易日逐日
结算，天然覆盖冷启动、周末/节假日与漏跑补算。当天（未收盘）自动跳过。
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import yaml

from paper_trading.engine import Bar, SettleConfig, SettleEngine
from paper_trading.portfolio import Portfolio
from paper_trading.report import render_daily_report

ORDERS_RE = re.compile(r"^orders_(\d{4}-\d{2}-\d{2})\.json$")


def _resolve_data_source(config: Dict) -> str:
    """数据源优先级：DATA_SOURCE 环境变量 > settings.yaml（与 main.py 一致）。"""
    return os.environ.get("DATA_SOURCE") or config.get("data_source", "auto")


def _norm_date(s) -> str:
    """'2026-10-30' 或 '20261030' 统一为 '2026-10-30'。"""
    s = str(s).strip()
    if "-" in s:
        return s[:10]
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


class DataFetcherBarProvider:
    """基于 DataFetcher 的 BarProvider，按 code 懒加载并缓存日线。

    只走真实数据源（tushare 优先：原始价 + 官方昨收，可识别除权；
    akshare 兜底：前复权，近期价格≈原始价）。绝不使用 mock 数据——
    mock 是随机行情，入账即永久污染账本；取不到一律按停牌处理。
    """

    def __init__(self, fetcher, days: int = 760):
        self.fetcher = fetcher
        self.days = days
        self._cache: Dict[str, Dict[str, Bar]] = {}

    def _fetch_df(self, code: str):
        for method in (lambda: self.fetcher._get_hist_data_tushare(code, self.days),
                       lambda: self.fetcher._get_hist_data_akshare(code, "daily", self.days)):
            try:
                df = method()
            except Exception:
                df = None
            if df is not None and not df.empty:
                return df
        return None

    def _load(self, code: str) -> Dict[str, Bar]:
        if code in self._cache:
            return self._cache[code]
        bars: Dict[str, Bar] = {}
        df = self._fetch_df(code)
        if df is None:
            print(f"     [WARN] {code} 行情获取失败，按停牌处理")
        else:
            has_pre = "昨收" in df.columns
            rows = []
            for _, r in df.iterrows():
                pre = float(r["昨收"]) if has_pre else None
                rows.append((_norm_date(r["日期"]),
                             round(float(r["开盘"]), 2),
                             round(float(r["最高"]), 2),
                             round(float(r["最低"]), 2),
                             round(float(r["收盘"]), 2), pre))
            rows.sort()
            for i, (d, o, h, l, c, pre) in enumerate(rows):
                if pre is None:
                    pre = rows[i - 1][4] if i > 0 else o
                bars[d] = Bar(open=o, high=h, low=l, close=c,
                              pre_close=pre)
        self._cache[code] = bars
        return bars

    def get_bar(self, code: str, date: str) -> Optional[Bar]:
        return self._load(code).get(date)


def _fetch_index_closes(fetcher, days: int) -> Dict[str, float]:
    """获取上证指数收盘 {date: close}。

    tushare 优先（index_daily 000001.SH，与 backtest.py 同款）；
    akshare 兜底用 stock_zh_index_daily("sh000001")。
    绝不走 get_index_hist/stock_zh_a_hist——后者对 "000001" 返回的是
    平安银行个股行情，基准会错成股价（2026-10-30 曾显示 11.64）。
    """
    closes: Dict[str, float] = {}
    pro = None
    try:
        pro = fetcher._get_tushare_pro()
    except Exception:
        pro = None
    if pro is not None:
        try:
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
            df = pro.index_daily(ts_code="000001.SH",
                                 start_date=start, end_date=end)
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    closes[_norm_date(r["trade_date"])] = float(r["close"])
                return closes
        except Exception as e:
            print(f"     [WARN] tushare 指数行情失败: {e}")
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol="sh000001")
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                closes[_norm_date(r["date"])] = float(r["close"])
    except Exception as e:
        print(f"     [WARN] akshare 指数行情失败: {e}")
    return closes


class IndexCalendar:
    """上证指数交易日历 + 基准收盘价。"""

    def __init__(self, fetcher, days: int = 760):
        self.closes: Dict[str, float] = _fetch_index_closes(fetcher, days)
        if not self.closes:
            print("     [WARN] 上证指数获取失败")

    def trade_dates_after(self, date: Optional[str]) -> List[str]:
        """date 之后所有已有收盘行情的交易日（当天未收盘自然不在内）。"""
        return sorted(d for d in self.closes if date is None or d > date)


def load_orders(orders_dir: str) -> Dict[str, List[Dict]]:
    """扫描 orders_*.json，返回 {date: orders_list}。"""
    result = {}
    for path in sorted(glob.glob(os.path.join(orders_dir, "orders_*.json"))):
        m = ORDERS_RE.match(os.path.basename(path))
        if not m:
            continue
        try:
            payload = json.load(open(path, encoding="utf-8"))
            result[m.group(1)] = payload.get("orders", [])
        except (json.JSONDecodeError, OSError) as e:
            print(f"     [WARN] 挂单文件损坏 {os.path.basename(path)}: {e}")
    return result


def settle_dates(portfolio: Portfolio, calendar: IndexCalendar,
                 orders_map: Dict[str, List[Dict]],
                 only_date: Optional[str] = None) -> List[str]:
    """追赶模式：last_settled_date 之后所有可结算（有收盘行情）的交易日。

    从首个挂单日开始（之前没有可结算对象）。当天未收盘的日期因无行情
    自然被排除，次日运行时补上。
    """
    last = portfolio.last_settled_date
    if not orders_map and last is None:
        return []
    first_order_date = min(orders_map) if orders_map else last
    dates = calendar.trade_dates_after(last)
    dates = [d for d in dates if d >= first_order_date]
    if only_date:
        dates = [d for d in dates if d == only_date]
    return dates


def run_settle(portfolio: Portfolio, engine: SettleEngine,
               calendar: IndexCalendar, orders_map: Dict[str, List[Dict]],
               dates: List[str], ledger_dir: str,
               trailing_stop_pct: float) -> int:
    """逐日结算并生成每日报告，返回结算天数。"""
    for d in dates:
        result = engine.settle(portfolio, d, orders_map.get(d, []),
                               benchmark_close=calendar.closes.get(d))
        md = render_daily_report(portfolio, result, trailing_stop_pct)
        report_path = os.path.join(ledger_dir, f"portfolio_{d}.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(md)
        n_buy = sum(1 for t in result["trades"] if t["side"] == "BUY")
        n_sell = sum(1 for t in result["trades"] if t["side"] == "SELL")
        snap = result["snapshot"]
        print(f"     {d}: 买{n_buy} 卖{n_sell} "
              f"净值 {snap['total_value']:,.0f} "
              f"({snap['daily_pnl']:+,.0f})")
    return len(dates)


def main():
    parser = argparse.ArgumentParser(description="虚拟盘结算")
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--ledger-dir", default=None,
                        help="账本与每日报告目录（默认读 settings 的 paper_trading.ledger_dir）")
    parser.add_argument("--orders-dir", default=None,
                        help="orders_*.json 目录（默认 reports/output）")
    parser.add_argument("--init", action="store_true", help="初始化账本后退出")
    parser.add_argument("--capital", type=float, default=None,
                        help="初始资金（默认读 settings 的 initial_capital）")
    parser.add_argument("--force", action="store_true",
                        help="--init/--replay 时覆盖已有账本")
    parser.add_argument("--date", default=None, help="只结算指定日 YYYY-MM-DD")
    parser.add_argument("--replay", action="store_true",
                        help="重置账本并从最早挂单重放")
    parser.add_argument("--history-days", type=int, default=760,
                        help="行情回看窗口（自然日，需覆盖 orders 730 天保留期）")
    args = parser.parse_args()

    config = yaml.safe_load(open(args.config, encoding="utf-8"))
    pt_cfg = config.get("paper_trading", {}) or {}
    if not pt_cfg.get("enabled", True):
        print("[WARN] paper_trading.enabled = false，退出")
        return

    ledger_dir = args.ledger_dir or pt_cfg.get(
        "ledger_dir", "reports/output/paper")
    orders_dir = args.orders_dir or config.get("report", {}).get(
        "output_dir", "reports/output")
    os.makedirs(ledger_dir, exist_ok=True)
    ledger_path = os.path.join(ledger_dir, "portfolio.json")
    capital = args.capital or pt_cfg.get("initial_capital", 500000)

    if args.init:
        if os.path.exists(ledger_path) and not args.force:
            print(f"[X] 账本已存在: {ledger_path}（用 --force 覆盖）")
            sys.exit(1)
        Portfolio(initial_capital=capital).save(ledger_path)
        print(f"[OK] 虚拟盘初始化: 资金 {capital:,.0f} -> {ledger_path}")
        return

    if args.replay and os.path.exists(ledger_path) and not args.force:
        print(f"[X] --replay 会清空账本 {ledger_path}（用 --force 确认）")
        sys.exit(1)

    portfolio = Portfolio.load(ledger_path)
    if portfolio is None or args.replay:
        portfolio = Portfolio(initial_capital=capital)
        print(f"[OK] 虚拟盘初始化: 资金 {capital:,.0f}")

    orders_map = load_orders(orders_dir)
    print(f"[结算] 账本 {ledger_path}")
    print(f"     挂单文件: {len(orders_map)} 个，"
          f"已结算至 {portfolio.last_settled_date or '(未开始)'}")

    from utils.data_fetcher import DataFetcher
    fetcher = DataFetcher(
        data_source=_resolve_data_source(config),
        tushare_token=open("config/.tushare_token",
                           encoding="utf-8").read().strip()
        if os.path.exists("config/.tushare_token") else None)
    provider = DataFetcherBarProvider(fetcher, days=args.history_days)
    calendar = IndexCalendar(fetcher, days=args.history_days)
    if not calendar.closes:
        print("[WARN] 无法获取交易日历（指数行情失败），退出")
        return

    engine = SettleEngine(SettleConfig.from_settings(config), provider)
    dates = settle_dates(portfolio, calendar, orders_map, args.date)
    if not dates:
        print("[OK] 没有待结算的交易日")
        return

    trailing = config.get("risk_management", {}).get("trailing_stop_pct", 5.0)
    n = run_settle(portfolio, engine, calendar, orders_map, dates,
                   ledger_dir, trailing)
    portfolio.save(ledger_path)
    latest = portfolio.daily_snapshots[-1]
    cum = (latest["total_value"] / portfolio.initial_capital - 1) * 100
    print(f"[OK] 结算 {n} 天。净值 {latest['total_value']:,.0f}，"
          f"累计收益 {cum:+.2f}%，已实现盈亏 {portfolio.realized_pnl:+,.0f}")


if __name__ == "__main__":
    main()
