# -*- coding: utf-8 -*-
"""Evaluate live-signal effectiveness since 2026-09-13 (post P7/P8 regime).

For each archived signal date, take stocks advised 强烈关注/关注 (valid, not vetoed),
compute equal-weight 5d/10d forward returns vs SSE index over the same window.
Also computes Rank IC restricted to the structured-signal period only.
Console output is ASCII-safe (GBK console).
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calibrate_weights import fetch_return_bars, forward_return, compute_ic

SIGNALS_DIR = "_pages-src"
BUY_ADVICE = ("强烈关注", "关注")


def load_full_records(signals_dir):
    """Load signals_*.json keeping advice/veto flags (calibrate's loader drops them)."""
    records, dates = [], set()
    for path in sorted(glob.glob(os.path.join(signals_dir, "signals_*.json"))):
        date = os.path.basename(path)[8:18]
        try:
            payload = json.load(open(path, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        dates.add(date)
        for s in payload.get("stocks", []):
            if not s.get("valid", True) or s.get("veto"):
                continue
            factors = s.get("factors", {}) or {}
            rec = {"date": date, "code": s["code"], "name": s.get("name", ""),
                   "advice": s.get("advice", ""),
                   "total_score": s.get("total_score"),
                   "fundamental": factors.get("fundamental"),
                   "technical": factors.get("technical"),
                   "momentum": factors.get("momentum"),
                   "capital_flow": factors.get("capital_flow"),
                   "chip_concentration": factors.get("chip_concentration")}
            records.append(rec)
    return records, sorted(dates)


def main():
    records, dates = load_full_records(SIGNALS_DIR)
    print(f"[eval] signal dates: {dates[0]} ~ {dates[-1]} ({len(dates)} days)")

    picks = {}  # date -> list of records
    for r in records:
        if r.get("advice") in BUY_ADVICE:
            picks.setdefault(r["date"], []).append(r)
    n_picks = sum(len(v) for v in picks.values())
    print(f"[eval] buy-advice picks: {n_picks} across {len(picks)} days")

    codes = sorted({r["code"] for r in records})
    print(f"[eval] fetching bars for {len(codes)} stocks + index ...")
    bars = fetch_return_bars(codes, dates[0], "2026-10-15")

    import tushare as ts
    token = open("config/.tushare_token", encoding="utf-8").read().strip()
    pro = ts.pro_api(token)
    idx = pro.index_daily(ts_code="000001.SH",
                          start_date=dates[0].replace("-", ""), end_date="20261015")
    idx_bars = [(f"{d[:4]}-{d[4:6]}-{d[6:]}", float(c))
                for d, c in sorted(zip(idx["trade_date"], idx["close"]))]

    print()
    print(f"{'date':12} {'#':>2} {'ret5':>8} {'sse5':>8} {'exc5':>8}"
          f" {'ret10':>8} {'sse10':>8} {'exc10':>8}")
    rows = []
    for d in dates:
        sel = picks.get(d, [])
        if not sel:
            continue
        def agg(h, bar_map=None):
            vals = []
            for r in sel:
                fr = forward_return(bars.get(r["code"], []), d, h)
                if fr is not None:
                    vals.append(fr)
            return sum(vals) / len(vals) if vals else None
        r5, r10 = agg(5), agg(10)
        s5 = forward_return(idx_bars, d, 5)
        s10 = forward_return(idx_bars, d, 10)
        rows.append((d, len(sel), r5, s5, r10, s10))
        def f(x):
            return "     n/a" if x is None else f"{x*100:+7.2f}%"
        e5 = None if (r5 is None or s5 is None) else r5 - s5
        e10 = None if (r10 is None or s10 is None) else r10 - s10
        print(f"{d:12} {len(sel):>2} {f(r5)} {f(s5)} {f(e5)} {f(r10)} {f(s10)} {f(e10)}")

    def stats(idx_off):
        rs = [r[idx_off] for r in rows if r[idx_off] is not None]
        ss = [r[idx_off + 1] for r in rows if r[idx_off] is not None]
        if not rs:
            return None
        wins = sum(1 for a, b in zip(rs, ss) if a > b)
        return (len(rs), sum(rs) / len(rs), sum(ss) / len(ss), wins)
    for label, off in (("5d", 2), ("10d", 4)):
        st = stats(off)
        if st:
            n, ar, asse, w = st
            print(f"[summary {label}] days={n} avg_pick={ar*100:+.2f}% "
                  f"avg_sse={asse*100:+.2f}% excess={100*(ar-asse):+.2f}% "
                  f"beat_sse={w}/{n}")

    # IC over the structured-signal period only (post P7/P8 regime)
    for r in records:
        bl = bars.get(r["code"])
        r["ret5"] = forward_return(bl, r["date"], 5) if bl else None
        r["ret10"] = forward_return(bl, r["date"], 10) if bl else None

    # per-advice-bucket average forward returns (does the ranking order returns?)
    from collections import defaultdict
    buckets = defaultdict(list)
    for r in records:
        if r.get("ret5") is not None:
            buckets[r["advice"]].append(r["ret5"])
    print("\n[buckets] avg ret5 by advice (ascending order expected):")
    for adv in ("强烈关注", "关注", "轻度关注", "观望", "谨慎", "回避"):
        v = buckets.get(adv)
        if v:
            print(f"  {adv or '(none)':6} n={len(v):4}  avg_ret5={100*sum(v)/len(v):+.2f}%")
    ic5 = compute_ic(records, 5)
    ic10 = compute_ic(records, 10)
    n5 = sum(1 for r in records if r.get("ret5") is not None)
    print(f"\n[ic] structured-signal period only, n(ret5)={n5}")
    for k, name in (("fundamental", "fund"), ("technical", "tech"),
                    ("momentum", "mom"), ("capital_flow", "cap"),
                    ("chip_concentration", "chip"), ("total_score", "TOTAL")):
        v5, v10 = ic5.get(k), ic10.get(k)
        f5 = "  n/a" if v5 is None else f"{v5:+.3f}"
        f10 = "  n/a" if v10 is None else f"{v10:+.3f}"
        print(f"  {name:6} IC5={f5}  IC10={f10}")


if __name__ == "__main__":
    main()
