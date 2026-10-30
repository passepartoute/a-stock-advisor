# -*- coding: utf-8 -*-
"""虚拟盘账本：现金、持仓、成交记录、每日净值快照的持久化。

纯数据层，无行情依赖、无网络。结算逻辑见 paper_trading.engine。
"""
import json
import os
from datetime import datetime
from typing import Dict, List, Optional

SCHEMA_VERSION = 1


class Portfolio:
    """虚拟盘账户。

    positions[code] = {
        code, name, shares, cost_price, buy_date, buy_price,
        stop_loss_price, target_price, highest_price, last_close,
    }
    trades: 成交记录列表（BUY/SELL，费用与实现盈亏单列）。
    daily_snapshots: 每日净值快照（含 benchmark_close）。
    """

    def __init__(self, initial_capital: float,
                 created_at: Optional[str] = None):
        self.version = SCHEMA_VERSION
        self.initial_capital = float(initial_capital)
        self.cash = float(initial_capital)
        self.created_at = created_at or datetime.now().strftime("%Y-%m-%d")
        self.last_settled_date: Optional[str] = None
        self.realized_pnl = 0.0
        self.positions: Dict[str, Dict] = {}
        self.trades: List[Dict] = []
        self.daily_snapshots: List[Dict] = []

    # ---------- 持久化 ----------

    def to_dict(self) -> Dict:
        return {
            "version": self.version,
            "initial_capital": self.initial_capital,
            "cash": self.cash,
            "created_at": self.created_at,
            "last_settled_date": self.last_settled_date,
            "realized_pnl": self.realized_pnl,
            "positions": self.positions,
            "trades": self.trades,
            "daily_snapshots": self.daily_snapshots,
        }

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)
        return path

    @classmethod
    def load(cls, path: str) -> Optional["Portfolio"]:
        """文件不存在时返回 None（由调用方决定是否初始化）。"""
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        p = cls(initial_capital=data["initial_capital"],
                created_at=data.get("created_at"))
        p.version = data.get("version", SCHEMA_VERSION)
        p.cash = data["cash"]
        p.last_settled_date = data.get("last_settled_date")
        p.realized_pnl = data.get("realized_pnl", 0.0)
        p.positions = data.get("positions", {})
        p.trades = data.get("trades", [])
        p.daily_snapshots = data.get("daily_snapshots", [])
        return p

    # ---------- 估值 ----------

    def market_value(self, prices: Optional[Dict[str, float]] = None) -> float:
        """持仓市值。prices 提供时用其价格（如当日收盘），否则用 last_close。"""
        total = 0.0
        for code, pos in self.positions.items():
            price = (prices or {}).get(code, pos.get("last_close", 0.0))
            total += pos["shares"] * price
        return total

    def total_value(self, prices: Optional[Dict[str, float]] = None) -> float:
        return self.cash + self.market_value(prices)
