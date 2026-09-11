# -*- coding: utf-8 -*-
"""signal_recorder 测试"""
import json
import os
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.signal_recorder import dump_signals


class TestDumpSignals(unittest.TestCase):
    def test_writes_all_candidates_with_factor_scores(self):
        results = [
            {
                "code": "600000", "name": "浦发银行", "sector": "银行",
                "valid": True, "total_score": 0.35, "advice": "关注",
                "overheat": False, "conflict_triggered": False,
                "latest_price": 10.5,
                "details": {
                    "fundamental": {"score": 0.5},
                    "technical": {"score": 0.3, "details": {"rsi": 55.0}},
                    "momentum": {"score": 0.2, "r5": 3.1, "r20": 6.0, "r60": 12.0},
                    "capital_flow": {"score": 0.1, "details": {"主力净流入": 800}},
                    "chip_concentration": {"score": 0.25},
                },
            },
            {   # 被否决的股票也要记录，但标记 veto
                "code": "600001", "name": "测试", "sector": "软件",
                "valid": True, "veto": True, "veto_reason": "趋势一致向下",
                "total_score": -1.0, "advice": "回避",
                "details": {},
            },
        ]
        with tempfile.TemporaryDirectory() as d:
            path = dump_signals(results, d, date_str="2026-09-11")
            self.assertTrue(os.path.exists(path))
            payload = json.load(open(path, encoding="utf-8"))

        self.assertEqual(payload["date"], "2026-09-11")
        self.assertEqual(payload["count"], 2)
        s0, s1 = payload["stocks"]
        self.assertEqual(s0["factors"]["fundamental"], 0.5)
        self.assertEqual(s0["r5"], 3.1)
        self.assertEqual(s0["rsi"], 55.0)
        self.assertEqual(s0["main_net"], 800)
        self.assertEqual(s0["close"], 10.5)
        self.assertTrue(s1["veto"])
        self.assertIsNone(s1["factors"]["fundamental"])

    def test_creates_output_dir(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "new_dir")
            path = dump_signals([], sub, date_str="2026-09-11")
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
