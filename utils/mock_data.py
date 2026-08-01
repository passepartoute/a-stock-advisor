"""
模拟数据生成器——用于本地测试策略逻辑
运行: python utils/mock_data.py
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os

# A+H 溢价映射样本（用于 mock）
_AH_UNDERLYING_CODES = ["600000", "600028", "600036", "601398", "601857", "601988", "601318", "600030"]
_CB_UNDERLYING_CODES = ["600000", "600036", "601398", "000001", "000002", "000063", "002142", "600009"]


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

        # A/H 溢价：仅对部分 A+H 公司赋值
        ah_premium = None
        if code in _AH_UNDERLYING_CODES:
            ah_premium = round(np.random.uniform(5, 120), 2)

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
            "成交额": cap * turnover / 100,
            "AH溢价率": ah_premium
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


def generate_mock_ah_premium(n_stocks: int = 50) -> pd.DataFrame:
    """生成模拟 A/H 溢价数据"""
    np.random.seed(44)
    data = []
    for i, code in enumerate(_AH_UNDERLYING_CODES[:n_stocks]):
        a_price = round(np.random.uniform(5, 100), 2)
        h_price = round(a_price / np.random.uniform(1.05, 2.2), 2)
        premium = round((a_price / h_price - 1) * 100, 2)
        data.append({
            "代码": code,
            "名称": f"AH模拟{i+1}",
            "A股价格": a_price,
            "H股价格": h_price,
            "AH溢价率": premium
        })
    return pd.DataFrame(data)


def generate_mock_cb_data(n_bonds: int = 30) -> pd.DataFrame:
    """生成模拟可转债数据"""
    np.random.seed(45)
    data = []
    codes = _CB_UNDERLYING_CODES[:n_bonds]
    for i, stock_code in enumerate(codes):
        bond_code = f"{110000 + i:06d}"
        bond_price = round(np.random.uniform(90, 140), 2)
        conversion_value = round(np.random.uniform(70, 130), 2)
        # 转股溢价率 = 转债价格 / 转股价值 - 1
        premium = round((bond_price / conversion_value - 1) * 100, 2)
        data.append({
            "正股代码": stock_code,
            "转债代码": bond_code,
            "转债名称": f"模拟转债{i+1}",
            "转股溢价率": premium,
            "转股价值": conversion_value,
            "转债价格": bond_price,
            "转债成交额": round(np.random.lognormal(15, 0.8), 2)
        })
    return pd.DataFrame(data)


def save_mock_data(output_dir="data"):
    os.makedirs(output_dir, exist_ok=True)
    spot = generate_mock_spot(200)
    spot.to_csv(f"{output_dir}/mock_spot.csv", index=False, encoding="utf-8-sig")
    print(f"模拟快照已保存: {output_dir}/mock_spot.csv ({len(spot)} 只)")


if __name__ == "__main__":
    save_mock_data()
