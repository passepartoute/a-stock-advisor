# -*- coding: utf-8 -*-
"""滚动 IC 校准报告（方案 B：只出报告，不自动改权重）。

读取 gh-pages 归档的信号数据（signals_*.json 优先，report_*.md 表格兜底），
用 tushare 拉取推荐日后的实际收益，计算各因子的 Rank IC，
按约束（单步上限、权重边界）给出建议权重，输出 Markdown 校准报告。

用法:
    python calibrate_weights.py --signals-dir _pages-src --days 60
    python calibrate_weights.py --signals-dir _pages-src --output reports/output/calibration_2026-10-09.md
    python calibrate_weights.py --no-fetch   # 离线模式：只做解析，不算收益（调试用）

设计说明:
    - 权重建议采用"IC 比例目标 + 单步截断"：IC<=0 的因子目标权重为 0，
      每步最多向目标移动 step_cap，再夹到 [w_min, w_max] 并归一化。
      保守设计，避免追噪声；最终变更需人工确认后改 config/settings.yaml。
    - IC 用 Spearman 秩相关（pandas rank + pearson），不依赖 scipy。
"""
import argparse
import glob
import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

FACTOR_KEYS = ("fundamental", "technical", "momentum", "capital_flow", "chip_concentration")
FACTOR_NAMES = {
    "fundamental": "基本面",
    "technical": "技术面",
    "momentum": "动量",
    "capital_flow": "资金面",
    "chip_concentration": "筹码",
}

REPORT_TABLE_RE = re.compile(r"^report_(\d{4}-\d{2}-\d{2})\.md$")
SIGNALS_RE = re.compile(r"^signals_(\d{4}-\d{2}-\d{2})\.json$")


# ============================================================
# 数据加载（纯 IO，无网络）
# ============================================================

def load_signals_json(signals_dir: str, since: str) -> Tuple[List[Dict], set]:
    """读取窗口内的 signals_*.json，返回 (records, covered_dates)。"""
    records, dates = [], set()
    for path in sorted(glob.glob(os.path.join(signals_dir, "signals_*.json"))):
        m = SIGNALS_RE.match(os.path.basename(path))
        if not m or m.group(1) < since:
            continue
        try:
            payload = json.load(open(path, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        date = m.group(1)
        dates.add(date)
        for s in payload.get("stocks", []):
            factors = s.get("factors", {}) or {}
            if not s.get("valid", True):
                continue
            if any(factors.get(k) is None for k in FACTOR_KEYS):
                continue
            records.append({
                "date": date, "code": s["code"], "name": s.get("name", ""),
                "sector": s.get("sector", ""), "source": "signals",
                "total_score": s.get("total_score"),
                **{k: factors[k] for k in FACTOR_KEYS},
            })
    return records, dates


def load_reports_fallback(signals_dir: str, since: str,
                          covered_dates: set) -> List[Dict]:
    """没有 signals JSON 的日期，从 report_*.md 的"今日精选"表解析因子子分。

    表列: 排名|代码|名称|行业|综合评分|建议|最新价|技术面|基本面|动量|资金面|筹码|...
    只覆盖入选的 ~20 只/天，样本量小于 signals JSON。
    """
    col_map = {"technical": 7, "fundamental": 8, "momentum": 9,
               "capital_flow": 10, "chip_concentration": 11}
    records = []
    for path in sorted(glob.glob(os.path.join(signals_dir, "report_*.md"))):
        m = REPORT_TABLE_RE.match(os.path.basename(path))
        if not m or m.group(1) < since or m.group(1) in covered_dates:
            continue
        date = m.group(1)
        try:
            text = open(path, encoding="utf-8-sig").read()
        except OSError:
            continue
        sec = re.search(r"## 今日精选(.*?)##", text, re.S)
        if not sec:
            continue
        for line in sec.group(1).splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 12 or not re.match(r"^\d+$", cells[0]) \
                    or not re.match(r"^\d{6}$", cells[1]):
                continue
            try:
                rec = {
                    "date": date, "code": cells[1], "name": cells[2],
                    "sector": cells[3], "source": "report",
                    "total_score": float(cells[4]),
                }
                for k, idx in col_map.items():
                    rec[k] = float(cells[idx])
            except ValueError:
                continue
            records.append(rec)
    return records


def load_records(signals_dir: str, days: int,
                 end_date: Optional[str] = None) -> List[Dict]:
    """加载窗口内全部信号记录：signals JSON 优先，报告 md 兜底补缺日期。"""
    end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.now()
    since = (end - timedelta(days=days)).strftime("%Y-%m-%d")
    records, covered = load_signals_json(signals_dir, since)
    fallback = load_reports_fallback(signals_dir, since, covered)
    return records + fallback


# ============================================================
# 收益计算（网络部分，隔离在 fetch_return_bars）
# ============================================================

def _suffix(code: str) -> str:
    if code.startswith(("60", "68", "90")):
        return code + ".SH"
    if code.startswith(("00", "30", "20")):
        return code + ".SZ"
    return code + ".BJ"


def fetch_return_bars(codes: List[str], start: str, end: str,
                      token_file: str = "config/.tushare_token") -> Dict[str, list]:
    """tushare 前复权日线，返回 {code: [(date, close), ...]}（按日期升序）。"""
    import tushare as ts
    token = open(token_file, encoding="utf-8").read().strip()
    ts.set_token(token)
    bars, failed = {}, []
    for i, code in enumerate(sorted(codes)):
        try:
            df = ts.pro_bar(ts_code=_suffix(code), adj="qfq",
                            start_date=start.replace("-", ""),
                            end_date=end.replace("-", ""))
            if df is not None and not df.empty:
                rows = sorted(zip(df["trade_date"], df["close"]))
                bars[code] = [(f"{d[:4]}-{d[4:6]}-{d[6:]}", float(c)) for d, c in rows]
            else:
                failed.append(code)
        except Exception:
            failed.append(code)
        if i % 20 == 19:
            print(f"     行情拉取 {i + 1}/{len(codes)}")
        time.sleep(0.25)
    if failed:
        print(f"     [WARN] {len(failed)} 只行情拉取失败: {','.join(failed[:10])}")
    return bars


def forward_return(bar_list: List[Tuple[str, float]], date: str, horizon: int) -> Optional[float]:
    """date 当天或之后首个交易日收盘买入，horizon 个交易日后收盘的收益。"""
    after = [(d, c) for d, c in bar_list if d >= date]
    if not after or len(after) <= horizon:
        return None
    entry = after[0][1]
    if entry <= 0:
        return None
    return after[horizon][1] / entry - 1


def attach_returns(records: List[Dict], bars: Dict[str, list],
                   horizons: Tuple[int, ...] = (5, 10)) -> List[Dict]:
    """给每条记录附加 ret5/ret10（无数据的记为 None）。"""
    for r in records:
        bl = bars.get(r["code"])
        for h in horizons:
            r[f"ret{h}"] = forward_return(bl, r["date"], h) if bl else None
    return records


# ============================================================
# IC 计算与权重建议（纯函数）
# ============================================================

def spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    """Spearman 秩相关；样本 <10 或零方差返回 None。"""
    import pandas as pd
    if len(xs) < 10:
        return None
    rx = pd.Series(xs).rank()
    ry = pd.Series(ys).rank()
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(rx.corr(ry))


def compute_ic(records: List[Dict], horizon: int = 5) -> Dict[str, Optional[float]]:
    """各因子对未来 horizon 日收益的 Rank IC。"""
    key = f"ret{horizon}"
    ics = {}
    for factor in FACTOR_KEYS + ("total_score",):
        pairs = [(r[factor], r[key]) for r in records
                 if r.get(factor) is not None and r.get(key) is not None]
        ics[factor] = spearman([p[0] for p in pairs], [p[1] for p in pairs]) if pairs else None
    return ics


def suggest_weights(current: Dict[str, float], ics: Dict[str, Optional[float]],
                    step_cap: float = 0.05, w_min: float = 0.05,
                    w_max: float = 0.50) -> Tuple[Dict[str, float], Optional[str]]:
    """IC 比例目标 + 单步截断的保守权重建议。

    IC<=0 的因子目标权重为 0（不翻负号，只降权）；
    每步最多向目标移动 step_cap；结果夹到 [w_min, w_max] 后归一化。
    返回 (建议权重, 备注)。备注非空表示未给出建议。
    """
    pos = {k: max(0.0, ics.get(k) or 0.0) for k in current}
    total_pos = sum(pos.values())
    if total_pos <= 0:
        return dict(current), "所有因子 IC<=0，建议保持当前权重并人工复核信号质量"
    target = {k: v / total_pos for k, v in pos.items()}

    # 先对每个因子向目标移动（截断到 ±step_cap）
    deltas = {
        k: max(-step_cap, min(step_cap, target[k] - cur))
        for k, cur in current.items()
    }
    # 截断会破坏零和，把超额一侧等比缩回，保证权重和恒为 1、单步上限不被归一化放大
    excess = sum(deltas.values())
    if abs(excess) > 1e-12:
        side = [k for k, dv in deltas.items() if dv != 0 and (dv > 0) == (excess > 0)]
        side_sum = sum(deltas[k] for k in side)
        if abs(side_sum) > 1e-12:
            scale = (side_sum - excess) / side_sum
            for k in side:
                deltas[k] *= scale

    proposed = {k: min(w_max, max(w_min, cur + deltas[k]))
                for k, cur in current.items()}
    s = sum(proposed.values())
    return {k: round(v / s, 4) for k, v in proposed.items()}, None


# ============================================================
# 报告渲染
# ============================================================

def render_report(date: str, days: int, records: List[Dict],
                  ic5: Dict, ic10: Dict, current: Dict[str, float],
                  suggested: Dict[str, float], note: Optional[str]) -> str:
    n5 = sum(1 for r in records if r.get("ret5") is not None)
    src_signals = sum(1 for r in records if r["source"] == "signals")
    src_report = len(records) - src_signals

    def fmt_ic(v):
        return "—" if v is None else f"{v:+.3f}"

    lines = [
        f"# 因子权重 IC 校准报告 - {date}",
        "",
        f"> 窗口: 最近 {days} 天 | 信号记录: {len(records)} 条"
        f"（结构化信号 {src_signals} 条 / 报告表解析 {src_report} 条）"
        f" | 有 5 日收益: {n5} 条",
        "> 本报告仅供人工复核，不会自动修改 config/settings.yaml",
        "",
        "## 因子 IC（Rank IC，越大越好；|IC|<0.02 基本为噪声）",
        "",
        "| 因子 | IC(5日) | IC(10日) | 当前权重 | 建议权重 | 变化 |",
        "|------|---------|----------|----------|----------|------|",
    ]
    for k in FACTOR_KEYS:
        cur = current.get(k, 0.0)
        sug = suggested.get(k, cur)
        lines.append(
            f"| {FACTOR_NAMES[k]} | {fmt_ic(ic5.get(k))} | {fmt_ic(ic10.get(k))}"
            f" | {cur:.0%} | {sug:.0%} | {sug - cur:+.0%} |"
        )
    lines += [
        f"| **综合评分** | {fmt_ic(ic5.get('total_score'))} | {fmt_ic(ic10.get('total_score'))} | — | — | — |",
        "",
    ]
    if note:
        lines += [f"**注意**: {note}", ""]
    lines += [
        "## 复核清单",
        "",
        "- [ ] IC 样本量是否足够（<200 条时 IC 噪声大，建议只看方向不改权重）",
        "- [ ] IC 为负的因子是否连续两期为负（单期为负可能是市场风格扰动）",
        "- [ ] 建议权重与当前权重的最大偏离是否 ≤5 个百分点",
        "- [ ] 确认后手动修改 config/settings.yaml 的 signal_weights.base",
        "",
        "## 局限",
        "",
        "- 信号样本只覆盖通过基本面初筛的候选股，存在选择偏差",
        "- 同一股票连续多日入选会重复计数（窗口重叠）",
        "- 权重建议基于历史 IC 线性外推，市场风格切换时可能失效",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="滚动 IC 校准报告")
    parser.add_argument("--signals-dir", default="_pages-src",
                        help="gh-pages 检出目录（含 signals_*.json / report_*.md）")
    parser.add_argument("--days", type=int, default=60, help="回看窗口天数")
    parser.add_argument("--end-date", default=None, help="窗口截止日 YYYY-MM-DD（默认今天）")
    parser.add_argument("--output", default=None, help="报告输出路径")
    parser.add_argument("--token-file", default="config/.tushare_token")
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--no-fetch", action="store_true", help="离线模式，不拉收益数据")
    args = parser.parse_args()

    end = args.end_date or datetime.now().strftime("%Y-%m-%d")
    start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=args.days)).strftime("%Y-%m-%d")

    print(f"[校准] 窗口 {start} ~ {end}，读取 {args.signals_dir}")
    records = load_records(args.signals_dir, args.days, args.end_date)
    print(f"     信号记录: {len(records)} 条")
    if not records:
        print("[中止] 窗口内无信号数据")
        return

    if not args.no_fetch:
        codes = sorted({r["code"] for r in records})
        # 收益需要 end 之后再留 horizon 个交易日
        bars_end = (datetime.strptime(end, "%Y-%m-%d") + timedelta(days=30)).strftime("%Y-%m-%d")
        bars_end = min(bars_end, datetime.now().strftime("%Y-%m-%d"))
        print(f"     拉取 {len(codes)} 只股票行情...")
        bars = fetch_return_bars(codes, start, bars_end, args.token_file)
        attach_returns(records, bars)
    else:
        print("     [离线模式] 跳过收益计算")

    ic5 = compute_ic(records, 5)
    ic10 = compute_ic(records, 10)

    import yaml
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    current = cfg.get("signal_weights", {}).get("base", {})
    suggested, note = suggest_weights(current, ic5)

    report = render_report(end, args.days, records, ic5, ic10, current, suggested, note)
    output = args.output or os.path.join(
        "reports", "output", f"calibration_{end}.md")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[完成] 校准报告: {output}")
    for k in FACTOR_KEYS:
        v5 = ic5.get(k)
        print(f"     {FACTOR_NAMES[k]}: IC5={'—' if v5 is None else f'{v5:+.3f}'} "
              f"权重 {current.get(k, 0):.0%} -> {suggested.get(k, 0):.0%}")


if __name__ == "__main__":
    main()
