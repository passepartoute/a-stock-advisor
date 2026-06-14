"""
测试运行器
使用 Python 内置 unittest，无需安装 pytest
用法:
    python run_tests.py              # 运行全部测试
    python run_tests.py -v           # 详细输出
    python -m unittest discover tests -v   # 直接用 unittest 发现
"""
import unittest
import sys
import os


def run_tests(verbosity=2):
    loader = unittest.TestLoader()
    start_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests")
    suite = loader.discover(start_dir, pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    verbosity = 2 if "-v" in sys.argv else 1
    sys.exit(run_tests(verbosity))
