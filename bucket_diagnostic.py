# -*- coding: utf-8 -*-
"""分组收益诊断：按综合评分/单因子分桶，看下期收益的单调性。

回答的问题：排名是"整体反向"还是"头部失效、中段有效"？
- 按日期横截面把候选股按 total_score 分成 5 档（Q1 最低分 ~ Q5 最高分），
  统计各档未来 5/10 日收益均值、中位数、胜率。
- 若收益随档位单调递增 -> 排名有效；若 Q5 < Q1 -> 整体反向；
  若 Q4/Q3 最高而 Q5 回落 -> 头部失效（过热），应改头部筛选而非反转信号。

用法:
    python bucket_diagnostic.py --signals-dir /tmp/pages-src --days 90
    python bucket_diagnostic.py --signals-dir /tmp/pages-src --days 90 --no-fetch

复用 calibrate_weights 的数据加载与收益拉取管线。
"""
import argparse
import os
import statistics
from collections import defaultdict
from typing import Dict, List, Optional

from calibrate_weights import (
    FACTOR_KEYS, FACTOR_NAMES, attach_returns, fetch_return_bars, load_records,
)

N_BUCKETS = 5


def bucket_records(records: List[Dict], key: str) -> Dict[str, List[Dict]]:
    """按日期横截面分桶：每天内按 key 升序分成 N_BUCKETS 档。

    返回 {"Q1": [...], ...}，Q1 为该因子当日最低分组。
    当日样本不足 N_BUCKETS*2 时跳过该日（分桶无意义）。
    """
    by_date: Dict[str, List[Dict]] = defaultdict(list)
    for r in records:
        if r.get(key) is not None:
            by_date[r["date"]].append(r)

    buckets: Dict[str, List[Dict]] = {f"Q{i+1}": [] for i in range(N_BUCKETS)}
    for date, recs in sorted(by_date.items()):
        recs = sorted(recs, key=lambda r: r[key])
        n = len(recs)
        if n < N_BUCKETS * 2:
            continue
        for i, r in enumerate(recs):
            b = min(i * N_BUCKETS // n, N_BUCKETS - 1)
            buckets[f"Q{b+1}"].append(r)
    return buckets


def bucket_stats(recs: List[Dict], ret_key: str) -> Optional[Dict]:
    vals = [r[ret_key] for r in recs if r.get(ret_key) is not None]
    if len(vals) < 5:
        return None
    return {
        "n": len(vals),
        "mean": statistics.mean(vals),
        "median": statistics.median(vals),
        "win": sum(1 for v in vals if v > 0) / len(vals),
    }


def render_markdown(date: str, days: int, records: List[Dict],
                    score_buckets: Dict[str, List[Dict]],
                    factor_spreads: Dict[str, Dict[str, Optional[float]]]) -> str:
    def row(label, recs, ret_key):
        st = bucket_stats(recs, ret_key)
        if not st:
            return f"| {label} | <5 | — | — | — |"
        return (f"| {label} | {st['n']} | {st['mean']:+.2%} "
                f"| {st['median']:+.2%} | {st['win']:.0%} |")

    lines = [
        f"# 分组收益诊断报告 - {date}",
        "",
        f"> 窗口: 最近 {days} 天 | 信号记录: {len(records)} 条",
        "> 分桶方式: 每日横截面按评分升序五等分（Q1=最低分, Q5=最高分）",
        "",
        "## 综合评分分桶（ret5）",
        "",
        "| 档位 | 样本 | 平均收益 | 中位数 | 胜率 |",
        "|------|------|----------|--------|------|",
    ]
    for i in range(N_BUCKETS):
        q = f"Q{i+1}"
        lines.append(row(q, score_buckets[q], "ret5"))
    lines += [
        "",
        "## 综合评分分桶（ret10）",
        "",
        "| 档位 | 样本 | 平均收益 | 中位数 | 胜率 |",
        "|------|------|----------|--------|------|",
    ]
    for i in range(N_BUCKETS):
        q = f"Q{i+1}"
        lines.append(row(q, score_buckets[q], "ret10"))
    lines += [
        "",
        "## 单因子多空价差（Q5-Q1 平均收益差，正=因子方向正确）",
        "",
        "| 因子 | ret5 价差 | ret10 价差 |",
        "|------|-----------|------------|",
    ]
    for k in FACTOR_KEYS + ("total_score",):
        name = FACTOR_NAMES.get(k, "综合评分")
        sp = factor_spreads.get(k, {})
        r5 = "—" if sp.get("ret5") is None else f"{sp['ret5']:+.2%}"
        r10 = "—" if sp.get("ret10") is None else f"{sp['ret10']:+.2%}"
        lines.append(f"| {name} | {r5} | {r10} |")
    lines += [
        "",
        "## 局限",
        "",
        "- 信号样本只覆盖通过初筛的候选股，非全市场",
        "- 同一股票连续多日入选会重复计数（窗口重叠），分组间不完全独立",
        "- 分桶按日横截面做，收益未做市场中性化（未减当日大盘收益）",
    ]
    return "\n".join(lines)


def print_summary(score_buckets: Dict[str, List[Dict]],
                  factor_spreads: Dict[str, Dict[str, Optional[float]]]) -> None:
    print("")
    print("  >> 综合评分分桶 (ret5)")
    print("  档位    样本    平均收益    中位数     胜率")
    for i in range(N_BUCKETS):
        q = f"Q{i+1}"
        st = bucket_stats(score_buckets[q], "ret5")
        if not st:
            print(f"  {q}      <5      —          —          —")
        else:
            print(f"  {q}      {st['n']:<6}  {st['mean']:+.2%}     "
                  f"{st['median']:+.2%}     {st['win']:.0%}")
    print("")
    print("  >> 单因子多空价差 (Q5-Q1)")
    for k in FACTOR_KEYS + ("total_score",):
        name = FACTOR_NAMES.get(k, "综合评分")
        sp = factor_spreads.get(k, {})
        r5 = "—" if sp.get("ret5") is None else f"{sp['ret5']:+.2%}"
        r10 = "—" if sp.get("ret10") is None else f"{sp['ret10']:+.2%}"
        print(f"  {name:<6}  ret5: {r5:>8}   ret10: {r10:>8}")


def main():
    parser = argparse.ArgumentParser(description="分组收益诊断")
    parser.add_argument("--signals-dir", default="_pages-src")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--token-file", default="config/.tushare_token")
    parser.add_argument("--no-fetch", action="store_true")
    args = parser.parse_args()

    print(f"[诊断] 窗口最近 {args.days} 天，读取 {args.signals_dir}")
    records = load_records(args.signals_dir, args.days, args.end_date)
    print(f"     信号记录: {len(records)} 条")
    if not records:
        print("[中止] 窗口内无信号数据")
        return

    if not args.no_fetch:
        codes = sorted({r["code"] for r in records})
        print(f"     拉取 {len(codes)} 只股票行情...")
        from datetime import datetime, timedelta
        end = args.end_date or datetime.now().strftime("%Y-%m-%d")
        start = (datetime.strptime(end, "%Y-%m-%d")
                 - timedelta(days=args.days)).strftime("%Y-%m-%d")
        bars_end = min((datetime.strptime(end, "%Y-%m-%d") + timedelta(days=30))
                       .strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))
        bars = fetch_return_bars(codes, start, bars_end, args.token_file)
        attach_returns(records, bars)
    else:
        print("     [离线模式] 跳过收益计算")

    score_buckets = bucket_records(records, "total_score")
    factor_spreads: Dict[str, Dict[str, Optional[float]]] = {}
    for k in FACTOR_KEYS + ("total_score",):
        bk = bucket_records(records, k)
        spread: Dict[str, Optional[float]] = {}
        for ret_key in ("ret5", "ret10"):
            s5, s1 = bucket_stats(bk["Q5"], ret_key), bucket_stats(bk["Q1"], ret_key)
            spread = None
            if s5 and s1:
                spread = s5["mean"] - s1["mean"]
            spread_val = spread
            spread = spread_val
            factor_spreads.setdefault(k, {})[ret_key] = spread

    print_summary(score_buckets, factor_spreads)

    from datetime import datetime
    date = args.end_date or datetime.now().strftime("%Y-%m-%d")
    report = render_markdown(date, args.days, records, score_buckets, factor_spreads)
    output = args.output or os.path.join(
        "reports", "output", f"bucket_diagnostic_{date}.md")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n[完成] 诊断报告: {output}")


if __name__ == "__main__":
    main()
