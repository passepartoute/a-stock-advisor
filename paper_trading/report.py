# -*- coding: utf-8 -*-
"""虚拟盘每日复盘报告（Markdown）。

生成 portfolio_YYYY-MM-DD.md：当日成交、未成交及原因、当前持仓、
账户概览（净值/当日盈亏/累计收益/vs 上证基准）、最近 30 日净值表。
"""
from typing import Dict, List, Optional

from .portfolio import Portfolio


def _money(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:,.2f}"


def _pct(v: Optional[float], digits: int = 2) -> str:
    return "—" if v is None else f"{v:+.{digits}f}%"


def _render_trades(trades: List[Dict]) -> List[str]:
    lines = [
        "## 当日成交",
        "",
        "| 方向 | 代码 | 名称 | 成交价 | 股数 | 金额 | 费用 | 原因 | 实现盈亏 |",
        "|------|------|------|--------|------|------|------|------|----------|",
    ]
    for t in trades:
        fees = t["commission"] + t["stamp_tax"]
        pnl = "—" if t["pnl"] is None else f"{t['pnl']:+,.2f}"
        lines.append(
            f"| {t['side']} | {t['code']} | {t['name']} | {t['price']:.2f} "
            f"| {t['shares']} | {_money(t['amount'])} | {fees:,.2f} "
            f"| {t['reason']} | {pnl} |")
    return lines


def _render_unfilled(unfilled: List[Dict]) -> List[str]:
    if not unfilled:
        return []
    lines = ["", "### 未成交订单", ""]
    for u in unfilled:
        lines.append(f"- {u['code']} {u['name']}：{u['reason']}")
    return lines


def _render_positions(portfolio: Portfolio,
                      trailing_stop_pct: float) -> List[str]:
    lines = [
        "## 当前持仓",
        "",
        "| 代码 | 名称 | 股数 | 成本价 | 现价 | 浮盈 | 止损价 | 目标价 | 距移动止盈线 |",
        "|------|------|------|--------|------|------|--------|--------|--------------|",
    ]
    for code, pos in sorted(portfolio.positions.items(),
                            key=lambda kv: kv[1]["buy_date"]):
        last = pos.get("last_close") or 0.0
        cost = pos["cost_price"]
        pnl_pct = (last - cost) / cost * 100 if cost else 0.0
        trail = pos["highest_price"] * (1 - trailing_stop_pct / 100)
        dist = (last - trail) / last * 100 if last else 0.0
        lines.append(
            f"| {code} | {pos['name']} | {pos['shares']} | {cost:.2f} "
            f"| {last:.2f} | {pnl_pct:+.1f}% | {pos['stop_loss_price']:.2f} "
            f"| {pos['target_price']:.2f} | {dist:.1f}% |")
    if not portfolio.positions:
        lines.append("| - | 空仓 | - | - | - | - | - | - | - |")
    return lines


def _benchmark_return(portfolio: Portfolio) -> Optional[float]:
    """以首个有基准值的快照为起点的基准累计涨跌幅（%）。"""
    snaps = [s for s in portfolio.daily_snapshots
             if s.get("benchmark_close")]
    if len(snaps) < 2:
        return None
    base = snaps[0]["benchmark_close"]
    last = snaps[-1]["benchmark_close"]
    return (last / base - 1) * 100 if base else None


def _render_overview(portfolio: Portfolio, snapshot: Dict) -> List[str]:
    total = snapshot["total_value"]
    cum_ret = (total / portfolio.initial_capital - 1) * 100
    bench = _benchmark_return(portfolio)
    excess = cum_ret - bench if bench is not None else None
    lines = [
        "## 账户概览",
        "",
        f"- 现金: {_money(snapshot['cash'])}",
        f"- 持仓市值: {_money(snapshot['market_value'])}",
        f"- 总净值: {_money(total)}",
        f"- 当日盈亏: {_money(snapshot['daily_pnl'])}",
        f"- 累计已实现盈亏: {_money(portfolio.realized_pnl)}",
        f"- 累计收益率: {_pct(cum_ret)}",
    ]
    if bench is not None:
        lines.append(f"- 上证同期: {_pct(bench)} | 超额: {_pct(excess)}")
    return lines


def _render_equity_table(portfolio: Portfolio, days: int = 30) -> List[str]:
    lines = [
        "## 净值表（最近 30 日）",
        "",
        "| 日期 | 总净值 | 当日盈亏 | 上证收盘 |",
        "|------|--------|----------|----------|",
    ]
    for s in portfolio.daily_snapshots[-days:]:
        bench = s.get("benchmark_close")
        lines.append(
            f"| {s['date']} | {_money(s['total_value'])} "
            f"| {_money(s['daily_pnl'])} "
            f"| {'—' if bench is None else f'{bench:,.2f}'} |")
    return lines


def render_daily_report(portfolio: Portfolio, day_result: Dict,
                        trailing_stop_pct: float = 5.0) -> str:
    """渲染某日结算后的复盘报告。day_result 为 SettleEngine.settle 的返回。"""
    date = day_result["date"]
    snapshot = day_result["snapshot"]
    lines = [
        f"# 虚拟盘每日结算 - {date}",
        "",
        f"> 初始资金: {_money(portfolio.initial_capital)}"
        f" | 起始日: {portfolio.created_at}"
        f" | 结算至: {portfolio.last_settled_date}",
        "",
    ]
    if day_result["trades"]:
        lines += _render_trades(day_result["trades"])
    else:
        lines += ["## 当日成交", "", "无成交。"]
    lines += _render_unfilled(day_result["unfilled"])
    lines += ["", ""] + _render_positions(portfolio, trailing_stop_pct)
    lines += ["", ""] + _render_overview(portfolio, snapshot)
    lines += ["", ""] + _render_equity_table(portfolio)
    lines += [
        "",
        "---",
        "",
        "**说明**：虚拟盘为模拟结算（次日挂单成交假设 + 双边费用），"
        "用于策略复盘，不构成投资建议。"
        "除权除息日按官方昨收比例调整持仓锚点，红利现金不计入（口径偏保守）。",
    ]
    return "\n".join(lines) + "\n"
