# -*- coding: utf-8 -*-
"""每日信号持久化。

把当天全部分析结果（不仅是入选股票，而是所有候选股的因子子分）
存为 reports/output/signals_YYYY-MM-DD.json，由 workflow 归档到 gh-pages，
供 calibrate_weights.py 做滚动 IC 校准使用。

只记录数据，不做任何决策；任何异常都不应中断主流程。
"""
import json
import os
from datetime import datetime
from typing import Dict, List, Optional


FACTOR_KEYS = ("fundamental", "technical", "momentum", "capital_flow", "chip_concentration")


def _extract_stock(result: Dict) -> Dict:
    """从 analyze_stock 的 result dict 中提取校准所需字段，缺失字段给 None。"""
    details = result.get("details", {}) or {}
    momentum = details.get("momentum", {}) or {}
    tech_details = details.get("technical", {}).get("details", {}) or {}
    cf_details = details.get("capital_flow", {}).get("details", {}) or {}

    factors = {k: (details.get(k, {}) or {}).get("score") for k in FACTOR_KEYS}

    return {
        "code": result.get("code"),
        "name": result.get("name"),
        "sector": result.get("sector"),
        "valid": result.get("valid", True),
        "veto": result.get("veto", False),
        "total_score": result.get("total_score"),
        "advice": result.get("advice"),
        "overheat": result.get("overheat", False),
        "conflict_triggered": result.get("conflict_triggered", False),
        "close": result.get("latest_price"),
        "factors": factors,
        "r5": momentum.get("r5"),
        "r20": momentum.get("r20"),
        "r60": momentum.get("r60"),
        "rsi": tech_details.get("rsi"),
        "main_net": cf_details.get("主力净流入"),
        "ai_sentiment_delta": result.get("ai_sentiment_delta"),
        "ai_hot_reversal": result.get("ai_hot_reversal", False),
    }


def dump_signals(results: List[Dict], output_dir: str,
                 date_str: Optional[str] = None) -> str:
    """把当天的全部分析结果写入 signals_<date>.json，返回文件路径。"""
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    stocks = [_extract_stock(r) for r in results]
    payload = {
        "date": date_str,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(stocks),
        "stocks": stocks,
    }
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"signals_{date_str}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return path
