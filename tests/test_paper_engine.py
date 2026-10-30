# -*- coding: utf-8 -*-
"""paper_trading.engine 结算引擎测试（FakeBarProvider，零网络）"""
import unittest

from paper_trading.engine import Bar, SettleConfig, SettleEngine
from paper_trading.portfolio import Portfolio

DATE = "2026-10-30"


class FakeBars:
    """dict[(code, date)] -> Bar 的内存行情源。"""

    def __init__(self, bars=None):
        self.bars = bars or {}

    def get_bar(self, code, date):
        return self.bars.get((code, date))


def make_engine(bars=None, **cfg_over):
    cfg = SettleConfig(**cfg_over)
    return SettleEngine(cfg, FakeBars(bars))


def make_order(code="600519", name="贵州茅台", latest=10.0, suggested=10.0,
               position_pct=20, stop=9.5, target=12.0):
    return {
        "code": code, "name": name, "advice": "关注",
        "latest_price": latest, "suggested_price": suggested,
        "position_pct": position_pct, "stop_loss_price": stop,
        "target_price": target,
        "gap_rules": [], "order_params": {},
    }


def make_position(code="600519", name="贵州茅台", shares=1000, cost=10.0,
                  stop=9.5, target=12.0, highest=11.0, last_close=10.5,
                  buy_date="2026-10-29", buy_fee=5.0):
    return {
        "code": code, "name": name, "shares": shares, "cost_price": cost,
        "buy_date": buy_date, "buy_price": cost, "stop_loss_price": stop,
        "target_price": target, "highest_price": highest,
        "last_close": last_close, "buy_fee": buy_fee,
    }


def make_portfolio(capital=100000.0, positions=None, cash=None):
    p = Portfolio(initial_capital=capital, created_at="2026-10-01")
    if positions:
        for pos in positions:
            p.positions[pos["code"]] = pos
    if cash is not None:
        p.cash = cash
    return p


class TestBuyFill(unittest.TestCase):
    def test_filled_at_suggested_when_open_higher(self):
        bars = {("600519", DATE): Bar(open=10.2, high=10.3, low=9.95,
                                      close=10.1, pre_close=10.0)}
        p = make_portfolio()
        result = make_engine(bars).settle(p, DATE, [make_order()])
        self.assertEqual(len(result["trades"]), 1)
        t = result["trades"][0]
        self.assertEqual(t["side"], "BUY")
        self.assertEqual(t["price"], 10.0)  # min(open, suggested)
        self.assertIn("600519", p.positions)
        self.assertEqual(p.positions["600519"]["highest_price"], 10.3)

    def test_filled_at_open_when_open_lower(self):
        bars = {("600519", DATE): Bar(open=9.9, high=10.0, low=9.8,
                                      close=9.95, pre_close=10.0)}
        p = make_portfolio()
        result = make_engine(bars).settle(p, DATE, [make_order()])
        self.assertEqual(result["trades"][0]["price"], 9.9)  # 开盘价更优

    def test_unfilled_when_low_above_suggested(self):
        bars = {("600519", DATE): Bar(open=10.2, high=10.3, low=10.05,
                                      close=10.2, pre_close=10.0)}
        p = make_portfolio()
        result = make_engine(bars).settle(p, DATE, [make_order()])
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["unfilled"][0]["reason"], "价格未触及")
        self.assertEqual(p.positions, {})


class TestGapRules(unittest.TestCase):
    def test_gap_up_above_cancel_threshold(self):
        # 高开 4% > 3%：取消
        bars = {("600519", DATE): Bar(open=10.4, high=10.5, low=9.9,
                                      close=10.2, pre_close=10.0)}
        p = make_portfolio()
        result = make_engine(bars).settle(p, DATE, [make_order()])
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["unfilled"][0]["reason"], "高开取消")

    def test_gap_up_reduce_halves_shares(self):
        # 高开 1.5%（1%~3%）：仓位减半 20% -> 10%
        bars = {("600519", DATE): Bar(open=10.15, high=10.2, low=9.95,
                                      close=10.1, pre_close=10.0)}
        p = make_portfolio(capital=100000.0)
        result = make_engine(bars).settle(p, DATE, [make_order(position_pct=20)])
        t = result["trades"][0]
        # budget = 100000 * 10% = 10000，price=10.0 -> 1000 股
        self.assertEqual(t["shares"], 1000)
        self.assertIn("减半", t["reason"])

    def test_gap_down_below_stop_cancels(self):
        # 开盘价低于止损价：取消
        bars = {("600519", DATE): Bar(open=9.4, high=9.9, low=9.3,
                                      close=9.6, pre_close=10.0)}
        p = make_portfolio()
        result = make_engine(bars).settle(p, DATE, [make_order()])
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["unfilled"][0]["reason"], "低开破止损")

    def test_open_at_limit_up_not_buyable(self):
        # 300 开头 20% 板：pre_close 10.0 -> 涨停 12.0；latest 11.9 使 gap 仅 0.84%，
        # 但开盘即封涨停 12.0，不可买
        bars = {("300001", DATE): Bar(open=12.0, high=12.0, low=11.9,
                                      close=12.0, pre_close=10.0)}
        p = make_portfolio()
        order = make_order(code="300001", name="特锐德", latest=11.9,
                           suggested=11.95)
        result = make_engine(bars).settle(p, DATE, [order])
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["unfilled"][0]["reason"], "涨停不可买")

    def test_limit_rate_20pct_for_chinext(self):
        # 300 开头 20% 板：pre_close 10.0，开盘 11.5 不算涨停，可买
        bars = {("300001", DATE): Bar(open=11.5, high=11.6, low=11.4,
                                      close=11.5, pre_close=10.0)}
        p = make_portfolio()
        order = make_order(code="300001", name="特锐德", latest=10.0,
                           suggested=11.5)
        # 高开 15% > 3% 会被 gap 取消——把 latest 抬高使 gap 为 0.9%
        order["latest_price"] = 11.4
        result = make_engine(bars).settle(p, DATE, [order])
        self.assertEqual(len(result["trades"]), 1)


class TestSharesAndFees(unittest.TestCase):
    def test_lot_rounding_and_min_commission(self):
        # budget = 50000*20% = 10000 -> 9999 不够 1000 股？10000/10=1000 股整
        bars = {("600519", DATE): Bar(open=10.05, high=10.2, low=9.95,
                                      close=10.0, pre_close=10.0)}
        p = make_portfolio(capital=49950.0)
        # budget = 49950*0.2 = 9990 -> floor(9990/10/100)*100 = 900 股
        result = make_engine(bars).settle(p, DATE, [make_order()])
        t = result["trades"][0]
        self.assertEqual(t["shares"], 900)
        self.assertEqual(t["amount"], 9000.0)
        # 佣金 max(9000*0.00015=1.35, 5) = 5，买入无印花税
        self.assertEqual(t["commission"], 5.0)
        self.assertEqual(t["stamp_tax"], 0.0)
        self.assertEqual(p.cash, 49950.0 - 9000.0 - 5.0)

    def test_cash_insufficient_skips_to_next_order(self):
        bars = {
            ("600519", DATE): Bar(open=10.05, high=10.2, low=9.95,
                                  close=10.0, pre_close=10.0),
            ("000001", DATE): Bar(open=10.05, high=10.2, low=9.95,
                                  close=10.0, pre_close=10.0),
        }
        # 初始 10000，现金只有 1500：第一单 budget 5000 -> 400 股 4000+5 不够；
        # 第二单 budget 1500 -> 100 股 1000+5 够，应成交
        p = make_portfolio(capital=10000.0, cash=1500.0)
        orders = [make_order(position_pct=50),
                  make_order(code="000001", name="平安银行", position_pct=15)]
        result = make_engine(bars).settle(p, DATE, orders)
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0]["code"], "000001")
        self.assertEqual(result["unfilled"][0]["reason"], "现金不足")

    def test_budget_uses_last_snapshot_total_value(self):
        # 有快照时 budget 基于快照总净值而非 initial_capital
        bars = {("600519", DATE): Bar(open=10.05, high=10.2, low=9.95,
                                      close=10.0, pre_close=10.0)}
        p = make_portfolio(capital=100000.0, cash=100000.0)
        p.daily_snapshots.append({
            "date": "2026-10-29", "cash": 50000.0, "market_value": 50000.0,
            "total_value": 50000.0, "daily_pnl": 0.0, "benchmark_close": None,
        })
        # budget = 50000*0.2 = 10000 -> 1000 股（若误用 initial 则 2000 股）
        result = make_engine(bars).settle(p, DATE, [make_order()])
        self.assertEqual(result["trades"][0]["shares"], 1000)


class TestSellRules(unittest.TestCase):
    def test_stop_loss_sells_at_stop_price(self):
        bars = {("600519", DATE): Bar(open=10.1, high=10.2, low=9.4,
                                      close=9.6, pre_close=10.5)}
        p = make_portfolio(positions=[make_position()], cash=90000.0)
        result = make_engine(bars).settle(p, DATE, [])
        t = result["trades"][0]
        self.assertEqual(t["side"], "SELL")
        self.assertEqual(t["reason"], "止损")
        self.assertEqual(t["price"], 9.5)  # min(open, stop)
        self.assertNotIn("600519", p.positions)
        # 卖出费用：commission=max(9500*0.00015,5)=5, stamp=9.5
        self.assertEqual(t["commission"], 5.0)
        self.assertEqual(t["stamp_tax"], 9.5)
        self.assertEqual(p.cash, 90000.0 + 9500.0 - 5.0 - 9.5)
        # pnl = (9.5-10)*1000 - 5(buy_fee) - 5 - 9.5 = -519.5
        self.assertAlmostEqual(t["pnl"], -519.5)
        self.assertAlmostEqual(p.realized_pnl, -519.5)

    def test_gap_down_through_stop_sells_at_open(self):
        bars = {("600519", DATE): Bar(open=9.46, high=9.5, low=9.1,
                                      close=9.2, pre_close=10.5)}
        p = make_portfolio(positions=[make_position()])
        result = make_engine(bars).settle(p, DATE, [])
        t = result["trades"][0]
        self.assertEqual(t["reason"], "止损")
        self.assertEqual(t["price"], 9.46)  # 跳空穿止损按开盘价

    def test_target_price_sells_at_target(self):
        bars = {("600519", DATE): Bar(open=10.2, high=12.1, low=10.5,
                                      close=11.8, pre_close=10.5)}
        p = make_portfolio(positions=[make_position()])
        result = make_engine(bars).settle(p, DATE, [])
        t = result["trades"][0]
        self.assertEqual(t["reason"], "目标价")
        self.assertEqual(t["price"], 12.0)  # max(open, target)

    def test_trailing_stop_uses_previous_highest(self):
        # T-1 highest=10.2 -> trail=9.69；今日冲高 11.5 回落到 low=10.9。
        # 若错误用当日 high，trail=10.925 会误触发。正确：不触发，highest 更新为 11.5
        pos = make_position(highest=10.2, last_close=10.2)
        bars = {("600519", DATE): Bar(open=10.3, high=11.5, low=10.9,
                                      close=11.2, pre_close=10.2)}
        p = make_portfolio(positions=[pos])
        result = make_engine(bars).settle(p, DATE, [])
        self.assertEqual(result["trades"], [])
        self.assertEqual(p.positions["600519"]["highest_price"], 11.5)
        self.assertEqual(p.positions["600519"]["last_close"], 11.2)

    def test_trailing_stop_triggers_in_profit_zone(self):
        # highest=11.0 -> trail=10.45 > cost 10.0；low=10.4 触发
        bars = {("600519", DATE): Bar(open=10.6, high=10.8, low=10.4,
                                      close=10.5, pre_close=10.5)}
        p = make_portfolio(positions=[make_position()])
        result = make_engine(bars).settle(p, DATE, [])
        t = result["trades"][0]
        self.assertEqual(t["reason"], "移动止盈")
        self.assertEqual(t["price"], 10.45)  # min(open, trail)

    def test_trailing_stop_ignored_below_cost(self):
        # highest=10.0 -> trail=9.5 <= cost 10.0，移动止盈不生效（保护留给止损）
        # 但 trail==stop=9.5 时止损会触发——把止损放低验证 trailing 不重复触发
        pos = make_position(highest=10.0, stop=9.0)
        bars = {("600519", DATE): Bar(open=9.8, high=9.9, low=9.4,
                                      close=9.6, pre_close=10.5)}
        p = make_portfolio(positions=[pos])
        result = make_engine(bars).settle(p, DATE, [])
        # trail=9.5 但 <= cost 不触发移动止盈；low=9.4 > stop=9.0 不触发止损
        self.assertEqual(result["trades"], [])

    def test_one_way_limit_down_cannot_sell(self):
        # 一字跌停：high==low==limit_down=9.0，即使破止损也卖不出
        bars = {("600519", DATE): Bar(open=9.0, high=9.0, low=9.0,
                                      close=9.0, pre_close=10.0)}
        p = make_portfolio(positions=[make_position()])
        result = make_engine(bars).settle(p, DATE, [])
        self.assertEqual(result["trades"], [])
        self.assertIn("600519", p.positions)


class TestSuspensionAndIdempotency(unittest.TestCase):
    def test_suspended_position_kept_and_order_skipped(self):
        p = make_portfolio(positions=[make_position()])
        result = make_engine({}).settle(p, DATE, [make_order(code="000001",
                                                             name="平安银行")])
        self.assertEqual(result["trades"], [])
        self.assertIn("600519", p.positions)  # 停牌保留
        self.assertEqual(p.positions["600519"]["highest_price"], 11.0)  # 不更新
        self.assertEqual(result["unfilled"][0]["reason"], "停牌")

    def test_settle_updates_last_settled_date(self):
        p = make_portfolio()
        make_engine({}).settle(p, DATE, [])
        self.assertEqual(p.last_settled_date, DATE)

    def test_trades_accumulate_in_portfolio(self):
        # 每日成交必须累积到账本 trades，否则无法跨日复盘
        bars = {
            ("600519", "2026-10-29"): Bar(open=10.05, high=10.2, low=9.95,
                                          close=10.0, pre_close=10.0),
            ("600519", DATE): Bar(open=10.1, high=10.2, low=9.4,
                                  close=9.6, pre_close=10.0),
        }
        p = make_portfolio()
        engine = make_engine(bars)
        engine.settle(p, "2026-10-29", [make_order()])   # 买入
        engine.settle(p, DATE, [])                        # 止损卖出
        sides = [t["side"] for t in p.trades]
        self.assertEqual(sides, ["BUY", "SELL"])
        self.assertEqual(p.trades[1]["reason"], "止损")


class TestSnapshot(unittest.TestCase):
    def test_snapshot_values(self):
        bars = {
            ("600519", DATE): Bar(open=10.05, high=10.3, low=9.95,
                                  close=11.0, pre_close=10.0),
        }
        p = make_portfolio(capital=100000.0, cash=100000.0)
        result = make_engine(bars).settle(p, DATE, [make_order()],
                                          benchmark_close=3681.47)
        snap = result["snapshot"]
        # 买 2000 股 * 10 = 20000 + 5 佣金 -> cash 79995
        self.assertEqual(snap["cash"], 79995.0)
        # 市值 = 2000 * 11.0 = 22000
        self.assertEqual(snap["market_value"], 22000.0)
        self.assertEqual(snap["total_value"], 101995.0)
        self.assertEqual(snap["daily_pnl"], 101995.0 - 100000.0)
        self.assertEqual(snap["benchmark_close"], 3681.47)
        self.assertEqual(p.daily_snapshots[-1], snap)

    def test_existing_position_marked_to_close(self):
        bars = {("600519", DATE): Bar(open=10.5, high=10.8, low=10.4,
                                      close=10.7, pre_close=10.5)}
        p = make_portfolio(positions=[make_position(highest=10.2)],
                           cash=50000.0)
        result = make_engine(bars).settle(p, DATE, [])
        snap = result["snapshot"]
        self.assertEqual(snap["market_value"], 1000 * 10.7)
        self.assertEqual(p.positions["600519"]["last_close"], 10.7)


if __name__ == "__main__":
    unittest.main()
