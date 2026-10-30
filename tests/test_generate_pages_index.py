# -*- coding: utf-8 -*-
"""generate_pages_index 分类分页测试"""
import importlib.util
import os
import tempfile
import unittest

_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".github", "scripts", "generate_pages_index.py")

FILES = (
    "report_2026-10-29.md", "report_2026-10-30.md",
    "backtest_20260701.md", "avoid_list_20260701.md",
    "portfolio_2026-10-29.md", "portfolio_2026-10-30.md",
    "calibration_2026-10-01.md", "calibration_2026-10-30.md",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("gen_index", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCategoryPages(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def _run_main(self, site_dir):
        import sys
        from unittest import mock
        with mock.patch.object(
                sys, "argv",
                ["gen", site_dir, "--days", "730", "--page-size", "100"]):
            self.mod.main()

    def _make_site(self, d, names=FILES):
        for name in names:
            open(os.path.join(d, name), "w").close()
        self._run_main(d)
        pages = {}
        for f in os.listdir(d):
            if f.endswith(".html"):
                pages[f] = open(os.path.join(d, f), encoding="utf-8").read()
        return pages

    def test_three_category_pages(self):
        with tempfile.TemporaryDirectory() as d:
            pages = self._make_site(d)
            index = pages["index.html"]
            # 主页：只有报告类，没有虚拟盘和校准条目
            self.assertIn("report_2026-10-30.md", index)
            self.assertIn("backtest_20260701.md", index)
            self.assertNotIn("portfolio_2026-10-30.md\"", index)
            self.assertNotIn("calibration_2026-10-30.md\"", index)
            # 三个页面互相有导航链接
            self.assertIn("paper.html", index)
            self.assertIn("calibration.html", index)

            paper = pages["paper.html"]
            self.assertIn("portfolio_2026-10-30.md", paper)
            self.assertNotIn("report_2026-10-30.md\"", paper)
            self.assertNotIn("calibration_2026-10-30.md\"", paper)
            self.assertIn("index.html", paper)

            calib = pages["calibration.html"]
            self.assertIn("calibration_2026-10-30.md", calib)
            self.assertNotIn("report_2026-10-30.md\"", calib)
            self.assertNotIn("portfolio_2026-10-30.md\"", calib)
            self.assertIn("index.html", calib)

    def test_category_page_absent_when_no_entries(self):
        with tempfile.TemporaryDirectory() as d:
            pages = self._make_site(d, names=("report_2026-10-30.md",))
            self.assertNotIn("paper.html", pages)
            self.assertNotIn("calibration.html", pages)
            self.assertIn("report_2026-10-30.md", pages["index.html"])

    def test_stale_category_pages_removed(self):
        with tempfile.TemporaryDirectory() as d:
            # 上轮有虚拟盘文件，这轮没了：paper.html 应被清掉
            self._make_site(d)
            self.assertTrue(os.path.exists(os.path.join(d, "paper.html")))
            for f in os.listdir(d):
                if f.startswith("portfolio_"):
                    os.remove(os.path.join(d, f))
            pages = self._make_site(d, names=())  # 不再重建 portfolio 文件
            self.assertNotIn("paper.html", pages)


if __name__ == "__main__":
    unittest.main()
