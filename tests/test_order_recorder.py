# -*- coding: utf-8 -*-
"""order_recorder 测试"""
import json
import os
import tempfile
import unittest

from utils.order_recorder import dump_orders


def _sample_order(code="600519"):
    """模拟 PreMarketPlanner.plan_order() 返回的挂单 dict。"""
    return {
        "code": code,
        "name": "贵州茅台",
        "advice": "关注",
        "latest_price": 1450.0,
        "ma20": 1440.0,
        "suggested_price": 1445.0,
        "position_pct": 15,
        "stop_loss_price": 1377.5,
        "target_price": 1885.0,
        "stop_loss_pct": -5.0,
        "target_pct": 30.0,
        "risk_reward": 6.0,
        "gap_rules": [
            {"scenario": "高开>3%", "action": "CANCEL", "description": "x", "position": 0},
            {"scenario": "平开", "action": "NORMAL", "description": "y", "position": 15},
        ],
        "order_params": {
            "stop_loss_trigger": 1377.5,
            "take_profit_trigger": 1885.0,
            "trailing_stop_desc": "从持仓期间最高点回撤 5% 触发卖出",
            "validity": "当日有效",
        },
    }


class TestDumpOrders(unittest.TestCase):
    def test_writes_all_order_fields(self):
        with tempfile.TemporaryDirectory() as d:
            path = dump_orders([_sample_order()], d, date_str="2026-10-30")
            self.assertTrue(os.path.exists(path))
            self.assertTrue(path.endswith("orders_2026-10-30.json"))
            payload = json.load(open(path, encoding="utf-8"))
            self.assertEqual(payload["date"], "2026-10-30")
            self.assertEqual(payload["count"], 1)
            self.assertIn("generated_at", payload)
            order = payload["orders"][0]
            # 下单四要素与规则必须原样保留
            self.assertEqual(order["suggested_price"], 1445.0)
            self.assertEqual(order["stop_loss_price"], 1377.5)
            self.assertEqual(order["target_price"], 1885.0)
            self.assertEqual(order["position_pct"], 15)
            self.assertEqual(order["latest_price"], 1450.0)
            self.assertEqual(len(order["gap_rules"]), 2)
            self.assertEqual(order["order_params"]["stop_loss_trigger"], 1377.5)

    def test_creates_output_dir(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "a", "b")
            path = dump_orders([], sub, date_str="2026-10-30")
            self.assertTrue(os.path.exists(path))
            payload = json.load(open(path, encoding="utf-8"))
            self.assertEqual(payload["orders"], [])
            self.assertEqual(payload["count"], 0)

    def test_multiple_orders_keep_rank_order(self):
        with tempfile.TemporaryDirectory() as d:
            orders = [_sample_order("600519"), _sample_order("000001")]
            path = dump_orders(orders, d, date_str="2026-10-30")
            payload = json.load(open(path, encoding="utf-8"))
            self.assertEqual([o["code"] for o in payload["orders"]],
                             ["600519", "000001"])


if __name__ == "__main__":
    unittest.main()
