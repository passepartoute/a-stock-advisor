# -*- coding: utf-8 -*-
"""虚拟盘单日结算引擎。

对一个交易日 T：
  步骤 A：处理持仓卖出（止损 > 移动止盈 > 目标价；一字跌停卖不出；停牌保留）
  步骤 B：处理当日新挂单买入（gap 规则、涨停不可买、限价成交假设、整手与费用）
  步骤 C：按 T 日收盘估值，追加净值快照，更新 last_settled_date

行情通过 BarProvider 协议注入（get_bar(code, date) -> Bar | None），
无 bar 即视为停牌，引擎本身零网络依赖，可全部用内存假数据测试。

成交价假设（与每日报告的挂单口径一致）：
  买入：当日 low <= suggested_price 视为成交，成交价 = min(open, suggested_price)
  止损：low <= stop 触发，成交价 = min(open, stop)（跳空穿止损按开盘价）
  移动止盈：low <= trail 触发（trail 用截至 T-1 的 highest，不前视），min(open, trail)
  目标价：high >= target 触发，成交价 = max(open, target)
"""
from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Optional

from .portfolio import Portfolio


class Bar(NamedTuple):
    open: float
    high: float
    low: float
    close: float
    pre_close: float


@dataclass
class SettleConfig:
    commission_rate: float = 0.00015   # 佣金率（双向）
    min_commission: float = 5.0        # 最低佣金（元）
    stamp_tax_rate: float = 0.001      # 印花税（仅卖出）
    slippage_pct: float = 0.0          # 滑点（%）
    lot_size: int = 100                # 整手股数
    trailing_stop_pct: float = 5.0     # 移动止盈回撤（%）
    gap_up_cancel_pct: float = 3.0     # 高开取消阈值（%）
    gap_up_reduce_pct: float = 1.0     # 高开减仓阈值（%）

    @classmethod
    def from_settings(cls, config: Dict) -> "SettleConfig":
        """从 settings.yaml 的完整 config 构造。

        费用/整手读 paper_trading 段；移动止盈与 gap 阈值复用
        risk_management / pre_market_order 段，与每日报告口径单一来源。
        """
        pt = config.get("paper_trading", {}) or {}
        rm = config.get("risk_management", {}) or {}
        pm = config.get("pre_market_order", {}) or {}
        return cls(
            commission_rate=pt.get("commission_rate", 0.00015),
            min_commission=pt.get("min_commission", 5.0),
            stamp_tax_rate=pt.get("stamp_tax_rate", 0.001),
            slippage_pct=pt.get("slippage_pct", 0.0),
            lot_size=pt.get("lot_size", 100),
            trailing_stop_pct=rm.get("trailing_stop_pct", 5.0),
            gap_up_cancel_pct=pm.get("gap_up_cancel_pct", 3.0),
            gap_up_reduce_pct=pm.get("gap_up_reduce_pct", 1.0),
        )


def infer_limit_rate(code: str, name: str = "") -> float:
    """按板块推断涨跌停幅度。ST 5%，北交所 30%，科创/创业 20%，主板 10%。"""
    if "ST" in (name or ""):
        return 0.05
    if code.startswith(("8", "4")):
        return 0.30
    if code.startswith(("30", "68")):
        return 0.20
    return 0.10


class SettleEngine:
    def __init__(self, config: SettleConfig, bar_provider):
        self.cfg = config
        self.bars = bar_provider

    # ---------- 费用 ----------

    def _buy_fee(self, amount: float) -> float:
        return max(amount * self.cfg.commission_rate, self.cfg.min_commission)

    def _sell_fees(self, amount: float):
        commission = max(amount * self.cfg.commission_rate,
                         self.cfg.min_commission)
        stamp = amount * self.cfg.stamp_tax_rate
        return commission, stamp

    def _slip_buy(self, price: float) -> float:
        return round(price * (1 + self.cfg.slippage_pct / 100), 2)

    def _slip_sell(self, price: float) -> float:
        return round(price * (1 - self.cfg.slippage_pct / 100), 2)

    # ---------- 主流程 ----------

    def settle(self, portfolio: Portfolio, date: str,
               orders: Optional[List[Dict]] = None,
               benchmark_close: Optional[float] = None) -> Dict:
        day_trades: List[Dict] = []
        unfilled: List[Dict] = []

        self._settle_sells(portfolio, date, day_trades)
        self._settle_buys(portfolio, date, orders or [], day_trades, unfilled)
        snapshot = self._take_snapshot(portfolio, date, benchmark_close)

        portfolio.last_settled_date = date
        return {"date": date, "trades": day_trades,
                "unfilled": unfilled, "snapshot": snapshot}

    # ---------- 步骤 A：持仓卖出 ----------

    def _settle_sells(self, portfolio: Portfolio, date: str,
                      day_trades: List[Dict]) -> None:
        for code in sorted(portfolio.positions,
                           key=lambda c: portfolio.positions[c]["buy_date"]):
            pos = portfolio.positions[code]
            bar = self.bars.get_bar(code, date)
            if bar is None:
                continue  # 停牌：不卖、不更新 highest

            rate = infer_limit_rate(code, pos.get("name", ""))
            limit_down = round(bar.pre_close * (1 - rate), 2)
            one_way_down = bar.high == bar.low == limit_down

            # 移动止盈线用截至 T-1 的最高价，避免用当日 high 产生前视
            trail = round(pos["highest_price"]
                          * (1 - self.cfg.trailing_stop_pct / 100), 2)

            sell_price, reason = None, None
            if bar.low <= pos["stop_loss_price"]:
                sell_price = min(bar.open, pos["stop_loss_price"])
                reason = "止损"
            elif trail > pos["cost_price"] and bar.low <= trail:
                sell_price = min(bar.open, trail)
                reason = "移动止盈"
            elif bar.high >= pos["target_price"]:
                sell_price = max(bar.open, pos["target_price"])
                reason = "目标价"

            if sell_price is not None and not one_way_down:
                sell_price = max(self._slip_sell(sell_price), limit_down)
                self._do_sell(portfolio, pos, date, sell_price, reason,
                              day_trades)
                continue

            # 未卖出：更新持仓期最高价与最新收盘
            pos["highest_price"] = max(pos["highest_price"], bar.high)
            pos["last_close"] = bar.close

    def _do_sell(self, portfolio: Portfolio, pos: Dict, date: str,
                 price: float, reason: str,
                 day_trades: List[Dict]) -> None:
        shares = pos["shares"]
        amount = round(price * shares, 2)
        commission, stamp = self._sell_fees(amount)
        portfolio.cash += amount - commission - stamp
        cost_total = pos["cost_price"] * shares + pos.get("buy_fee", 0.0)
        pnl = round(amount - commission - stamp - cost_total, 2)
        pnl_pct = round(pnl / cost_total * 100, 2) if cost_total else 0.0
        portfolio.realized_pnl = round(portfolio.realized_pnl + pnl, 2)
        day_trades.append({
            "date": date, "code": pos["code"], "name": pos["name"],
            "side": "SELL", "price": price, "shares": shares,
            "amount": amount, "commission": commission, "stamp_tax": stamp,
            "reason": reason, "pnl": pnl, "pnl_pct": pnl_pct,
        })
        del portfolio.positions[pos["code"]]

    # ---------- 步骤 B：新挂单买入 ----------

    def _settle_buys(self, portfolio: Portfolio, date: str,
                     orders: List[Dict], day_trades: List[Dict],
                     unfilled: List[Dict]) -> None:
        prev_total = (portfolio.daily_snapshots[-1]["total_value"]
                      if portfolio.daily_snapshots
                      else portfolio.initial_capital)

        for order in orders:  # 文件内顺序即报告排名 = 优先级
            code = order["code"]
            name = order.get("name", "")
            if code in portfolio.positions:
                continue  # 已持仓（含当日卖出后仍持有的其他标的）
            bar = self.bars.get_bar(code, date)
            if bar is None:
                unfilled.append(self._unfill(order, "停牌"))
                continue

            rate = infer_limit_rate(code, name)
            limit_up = round(bar.pre_close * (1 + rate), 2)
            if bar.open >= limit_up:
                unfilled.append(self._unfill(order, "涨停不可买"))
                continue

            latest = order.get("latest_price") or bar.pre_close
            gap_pct = (bar.open - latest) / latest * 100 if latest else 0.0
            position_pct = order.get("position_pct", 0)
            reason = "挂单成交"
            if gap_pct > self.cfg.gap_up_cancel_pct:
                unfilled.append(self._unfill(order, "高开取消"))
                continue
            if gap_pct > self.cfg.gap_up_reduce_pct:
                position_pct = position_pct / 2
                reason = "挂单成交(高开减半)"
            if bar.open < order.get("stop_loss_price", 0):
                unfilled.append(self._unfill(order, "低开破止损"))
                continue

            suggested = order.get("suggested_price")
            if suggested is None or bar.low > suggested:
                unfilled.append(self._unfill(order, "价格未触及"))
                continue

            price = self._slip_buy(min(bar.open, suggested))
            budget = prev_total * position_pct / 100
            shares = int(budget / price / self.cfg.lot_size) * self.cfg.lot_size
            if shares < self.cfg.lot_size:
                unfilled.append(self._unfill(order, "现金不足"))
                continue
            amount = round(price * shares, 2)
            fee = self._buy_fee(amount)
            if amount + fee > portfolio.cash:
                unfilled.append(self._unfill(order, "现金不足"))
                continue

            portfolio.cash -= amount + fee
            portfolio.positions[code] = {
                "code": code, "name": name, "shares": shares,
                "cost_price": price, "buy_date": date, "buy_price": price,
                "stop_loss_price": order.get("stop_loss_price"),
                "target_price": order.get("target_price"),
                "highest_price": bar.high, "last_close": bar.close,
                "buy_fee": fee,
            }
            day_trades.append({
                "date": date, "code": code, "name": name,
                "side": "BUY", "price": price, "shares": shares,
                "amount": amount, "commission": fee, "stamp_tax": 0.0,
                "reason": reason, "pnl": None, "pnl_pct": None,
            })

    @staticmethod
    def _unfill(order: Dict, reason: str) -> Dict:
        return {"code": order.get("code"), "name": order.get("name", ""),
                "reason": reason}

    # ---------- 步骤 C：净值快照 ----------

    def _take_snapshot(self, portfolio: Portfolio, date: str,
                       benchmark_close: Optional[float]) -> Dict:
        closes = {}
        for code, pos in portfolio.positions.items():
            bar = self.bars.get_bar(code, date)
            closes[code] = bar.close if bar else pos.get("last_close", 0.0)
        mv = portfolio.market_value(closes)
        total = round(portfolio.cash + mv, 2)
        prev_total = (portfolio.daily_snapshots[-1]["total_value"]
                      if portfolio.daily_snapshots
                      else portfolio.initial_capital)
        snapshot = {
            "date": date, "cash": round(portfolio.cash, 2),
            "market_value": round(mv, 2), "total_value": total,
            "daily_pnl": round(total - prev_total, 2),
            "benchmark_close": benchmark_close,
        }
        portfolio.daily_snapshots.append(snapshot)
        return snapshot
