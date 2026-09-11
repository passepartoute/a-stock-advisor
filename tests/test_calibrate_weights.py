# -*- coding: utf-8 -*-
"""calibrate_weights 纯函数与解析逻辑测试（不触网）"""
import json
import os
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calibrate_weights import (
    compute_ic, forward_return, load_records, load_reports_fallback,
    load_signals_json, spearman, suggest_weights,
)


def _mk_record(code, date, total=0.3, **factors):
    rec = {"date": date, "code": code, "name": code, "sector": "测试",
           "source": "signals", "total_score": total}
    for k in ("fundamental", "technical", "momentum", "capital_flow", "chip_concentration"):
        rec[k] = factors.get(k, 0.0)
    return rec


class TestSpearman(unittest.TestCase):
    def test_perfect_positive(self):
        xs = list(range(20))
        self.assertAlmostEqual(spearman(xs, xs), 1.0, places=6)

    def test_perfect_negative(self):
        xs = list(range(20))
        ys = list(reversed(xs))
        self.assertAlmostEqual(spearman(xs, ys), -1.0, places=6)

    def test_too_few_samples_returns_none(self):
        self.assertIsNone(spearman([1, 2, 3], [1, 2, 3]))

    def test_zero_variance_returns_none(self):
        self.assertIsNone(spearman([5.0] * 20, list(range(20))))


class TestForwardReturn(unittest.TestCase):
    def setUp(self):
        self.bars = [(f"2026-08-{d:02d}", 10.0 + i) for i, d in enumerate(range(3, 30))]

    def test_entry_on_or_after_date(self):
        # 2026-08-03 收盘 10.0 买入，+5 个交易日后收盘 15.0
        r = forward_return(self.bars, "2026-08-03", 5)
        self.assertAlmostEqual(r, 15.0 / 10.0 - 1)

    def test_skips_to_next_trading_day(self):
        # 08-05 之前没有 bar 时，从首个 >= date 的 bar 入场
        r = forward_return(self.bars, "2026-08-01", 0)
        self.assertAlmostEqual(r, 0.0)

    def test_insufficient_bars_returns_none(self):
        self.assertIsNone(forward_return(self.bars, "2026-08-25", 10))

    def test_empty_bars(self):
        self.assertIsNone(forward_return([], "2026-08-03", 5))


class TestComputeIC(unittest.TestCase):
    def test_monotonic_factor_high_ic(self):
        records = []
        for i in range(30):
            r = _mk_record(f"{i:06d}", "2026-09-01", momentum=i / 30.0)
            r["ret5"] = i / 30.0
            r["ret10"] = -i / 30.0
            records.append(r)
        ic5 = compute_ic(records, 5)
        ic10 = compute_ic(records, 10)
        self.assertAlmostEqual(ic5["momentum"], 1.0, places=6)
        self.assertAlmostEqual(ic10["momentum"], -1.0, places=6)

    def test_missing_returns_excluded(self):
        records = [_mk_record(f"{i:06d}", "2026-09-01", technical=i / 20.0)
                   for i in range(20)]
        for r in records[:10]:
            r["ret5"] = r["technical"]
        # 后 10 条无 ret5 -> 样本 10 条，刚好 >= 10，IC=1
        ic = compute_ic(records, 5)
        self.assertAlmostEqual(ic["technical"], 1.0, places=6)


class TestSuggestWeights(unittest.TestCase):
    def setUp(self):
        self.current = {"fundamental": 0.20, "technical": 0.25,
                        "momentum": 0.10, "capital_flow": 0.45}

    def test_moves_toward_positive_ic(self):
        ics = {"fundamental": 0.08, "technical": 0.02, "momentum": 0.05,
               "capital_flow": -0.06}
        suggested, note = suggest_weights(self.current, ics)
        self.assertIsNone(note)
        # IC 最高的 fundamental 增配，IC 为负的 capital_flow 降配
        self.assertGreater(suggested["fundamental"], self.current["fundamental"])
        self.assertLess(suggested["capital_flow"], self.current["capital_flow"])
        self.assertAlmostEqual(sum(suggested.values()), 1.0, places=3)

    def test_step_cap_respected(self):
        ics = {"fundamental": 0.5, "technical": 0.0, "momentum": 0.0,
               "capital_flow": 0.0}
        suggested, _ = suggest_weights(self.current, ics, step_cap=0.05)
        self.assertLessEqual(suggested["fundamental"] - 0.20, 0.05 + 1e-6)

    def test_all_negative_ic_keeps_current(self):
        ics = {k: -0.1 for k in self.current}
        suggested, note = suggest_weights(self.current, ics)
        self.assertEqual(suggested, self.current)
        self.assertIsNotNone(note)

    def test_none_ic_treated_as_zero(self):
        ics = {"fundamental": None, "technical": 0.1, "momentum": None,
               "capital_flow": None}
        suggested, note = suggest_weights(self.current, ics)
        self.assertIsNone(note)
        self.assertGreater(suggested["technical"], self.current["technical"])

    def test_bounds_respected(self):
        current = {"fundamental": 0.05, "technical": 0.05,
                   "momentum": 0.05, "capital_flow": 0.05}
        ics = {k: 0.1 for k in current}
        suggested, _ = suggest_weights(current, ics, step_cap=0.5,
                                       w_min=0.05, w_max=0.5)
        for v in suggested.values():
            self.assertGreaterEqual(v, 0.04)   # 归一化允许轻微越界
            self.assertLessEqual(v, 0.51)


class TestLoadSignals(unittest.TestCase):
    def _write_signals(self, d, date, codes=("600000", "600001")):
        payload = {
            "date": date,
            "stocks": [
                {"code": c, "name": c, "sector": "银行", "valid": True,
                 "total_score": 0.3,
                 "factors": {"fundamental": 0.1, "technical": 0.2,
                             "momentum": 0.3, "capital_flow": 0.4,
                             "chip_concentration": 0.5}}
                for c in codes
            ],
        }
        with open(os.path.join(d, f"signals_{date}.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    def _write_report(self, d, date):
        text = f"""# A股每日投资建议 - {date}

## 今日精选

| 排名 | 代码 | 名称 | 行业 | 综合评分 | 建议 | 最新价 | 技术面 | 基本面 | 动量 | 资金面 | 筹码 | AI信号 | 关键信号 |
|------|------|------|------|----------|------|--------|--------|--------|------|--------|------|--------|----------|
| 1 | 600100 | 测试股 | 软件 | 0.35 | 关注 | 10.0 | 0.5 | 0.4 | 0.3 | 0.2 | 0.1 | - | 站上年线 |

## 其他
"""
        with open(os.path.join(d, f"report_{date}.md"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_json_preferred_over_report(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_signals(d, "2026-09-01")
            self._write_report(d, "2026-09-01")  # 同日期应被 JSON 覆盖
            self._write_report(d, "2026-09-02")  # 无 JSON，走兜底
            records = load_records(d, days=30, end_date="2026-09-10")
            by_date = {}
            for r in records:
                by_date.setdefault(r["date"], []).append(r)
            self.assertEqual(len(by_date["2026-09-01"]), 2)
            self.assertTrue(all(r["source"] == "signals" for r in by_date["2026-09-01"]))
            self.assertEqual(len(by_date["2026-09-02"]), 1)
            self.assertEqual(by_date["2026-09-02"][0]["source"], "report")
            # 兜底解析的因子列映射正确: 技术面=col7=0.5, 基本面=col8=0.4
            self.assertAlmostEqual(by_date["2026-09-02"][0]["technical"], 0.5)
            self.assertAlmostEqual(by_date["2026-09-02"][0]["fundamental"], 0.4)

    def test_window_filter(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_signals(d, "2026-06-01")  # 窗口外
            self._write_signals(d, "2026-09-05")
            records, covered = load_signals_json(d, "2026-08-01")
            self.assertEqual(len(records), 2)
            self.assertEqual(covered, {"2026-09-05"})

    def test_invalid_stock_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            payload = {"date": "2026-09-01", "stocks": [
                {"code": "600000", "valid": False, "factors": {}},
                {"code": "600001", "valid": True,
                 "factors": {"fundamental": 0.1, "technical": None}},
                {"code": "600002", "valid": True,
                 "factors": {"fundamental": 0.1, "technical": 0.2,
                             "momentum": 0.3, "capital_flow": 0.4,
                             "chip_concentration": 0.5}},
            ]}
            with open(os.path.join(d, "signals_2026-09-01.json"), "w",
                      encoding="utf-8") as f:
                json.dump(payload, f)
            records, _ = load_signals_json(d, "2026-08-01")
            # invalid 剔除；因子缺失的也剔除；只剩 600002
            self.assertEqual([r["code"] for r in records], ["600002"])


if __name__ == "__main__":
    unittest.main()
