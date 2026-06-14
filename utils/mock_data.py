"""
模拟数据生成器——用于本地测试策略逻辑
运行: python utils/mock_data.py
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os

def generate_mock_spot(n_stocks: int = 200) -> pd.DataFrame:
    """生成模拟的全市场快照数据"""
    np.random.seed(42)

    sectors = [
        "银行", "证券", "保险", "白酒", "食品饮料", "家电行业",
        "医疗器械", "生物制品", "中药", "电力行业", "电网设备",
        "工程机械", "半导体", "石油行业", "有色金属",
        "教育", "游戏", "影视"
    ]

    data = []
    for i in range(n_stocks):
        code = f"{600000 + i:06d}"
        sector = np.random.choice(sectors)
        cap = np.random.lognormal(5, 1.5) * 1e8  # 市值
        pe = max(5, np.random.lognormal(3, 0.6)) if np.random.random() > 0.1 else -1
        pb = max(0.5, np.random.lognormal(0.5, 0.4))
        dy = max(0, np.random.exponential(2.5))
        price = max(1, np.random.lognormal(2.5, 0.8))
        turnover = np.random.exponential(2)

        data.append({
            "代码": code,
            "名称": f"模拟股票{i+1}",
            "所属行业": sector,
            "总市值": cap,
            "流通市值": cap * 0.7,
            "市盈率": pe,
            "市净率": pb,
            "股息率": dy,
            "涨跌幅": np.random.normal(0, 3),
            "换手率": turnover,
            "振幅": np.random.exponential(3),
            "最高": price * 1.02,
            "最低": price * 0.98,
            "今开": price * 0.99,
            "昨收": price * 0.995,
            "收盘价": price,
            "成交量": cap / price * turnover / 100,
            "成交额": cap * turnover / 100
        })

    return pd.DataFrame(data)


def generate_mock_hist(days: int = 300, trend: str = "up") -> pd.DataFrame:
    """生成模拟的日线K线数据"""
    np.random.seed(123)
    dates = pd.date_range(end=datetime.now(), periods=days, freq="B")

    if trend == "up":
        base = np.linspace(50, 80, days) + np.random.randn(days) * 2
    elif trend == "down":
        base = np.linspace(80, 50, days) + np.random.randn(days) * 2
    else:
        base = np.full(days, 65) + np.random.randn(days) * 3

    close = np.maximum(base, 1)
    open_p = close * (1 + np.random.randn(days) * 0.01)
    high = np.maximum(close, open_p) * (1 + np.random.exponential(0.01, days))
    low = np.minimum(close, open_p) * (1 - np.random.exponential(0.01, days))
    vol = np.random.lognormal(15, 0.5, days)

    return pd.DataFrame({
        "日期": dates,
        "开盘": np.round(open_p, 2),
        "收盘": np.round(close, 2),
        "最高": np.round(high, 2),
        "最低": np.round(low, 2),
        "成交量": vol.astype(int),
        "成交额": (vol * close).astype(int),
        "振幅": np.round((high - low) / low * 100, 2),
        "涨跌幅": np.round(np.diff(close, prepend=close[0]) / close * 100, 2),
        "涨跌额": np.round(np.diff(close, prepend=close[0]), 2)
    })


def save_mock_data(output_dir="data"):
    os.makedirs(output_dir, exist_ok=True)
    spot = generate_mock_spot(200)
    spot.to_csv(f"{output_dir}/mock_spot.csv", index=False, encoding="utf-8-sig")
    print(f"模拟快照已保存: {output_dir}/mock_spot.csv ({len(spot)} 只)")


if __name__ == "__main__":
    save_mock_data()
