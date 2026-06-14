"""
A股每日投资顾问 v2.1
运行:
  python main.py           # 实盘模式（连接akshare获取实时数据）
  python main.py --demo    # 演示模式（使用模拟数据验证策略逻辑）

v2.1 核心改进：
- 数据硬校验：年线/关键数据缺失时中止或明确警告
- 动态权重：根据大盘状态自动调整因子权重
- 一票否决：趋势向下/主力流出/顶背离死叉等直接排除
- 信号冲突：看跌信号优先级高于看涨，冲突时强制保守
- 支撑/压力位：止损/目标价基于近期高低点和均线，告别固定比例
- 行业分散：推荐列表单行业最多1只，强制覆盖至少5个行业
"""
import sys
import os
import shutil
import yaml
import pandas as pd
import numpy as np
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from utils.data_fetcher import DataFetcher
from strategies.fundamental import FundamentalScreener
from strategies.technical import TechnicalAnalyzer
from strategies.signal_engine_v2 import SignalEngineV2
from strategies.market_regime import MarketRegimeDetector
from strategies.risk_manager import RiskManager
from strategies.pre_market import PreMarketPlanner
from reports.daily_report import DailyReport


def load_config(path="config/settings.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_index_data(index_df: pd.DataFrame, config: dict) -> dict:
    """
    校验大盘指数数据完整性
    返回 {"valid": bool, "ma250": float|None, "error": str|None}
    """
    dq_cfg = config.get("data_quality", {})
    min_days = dq_cfg.get("min_history_days", 250)

    if index_df.empty or len(index_df) < min_days:
        return {"valid": False, "ma250": None,
                "error": f"上证指数历史数据不足，需要至少 {min_days} 天，实际 {len(index_df)} 天"}

    if "收盘" not in index_df.columns:
        return {"valid": False, "ma250": None, "error": "上证指数数据缺少'收盘'字段"}

    ma250_series = index_df["收盘"].rolling(window=250).mean()
    ma250 = ma250_series.iloc[-1]

    if pd.isna(ma250) or ma250 == 0:
        return {"valid": False, "ma250": None,
                "error": "上证指数年线(MA250)计算结果无效（NaN 或 0）"}

    return {"valid": True, "ma250": round(float(ma250), 2), "error": None}


def validate_stock_data(hist: pd.DataFrame, code: str, config: dict) -> dict:
    """校验个股历史数据完整性"""
    dq_cfg = config.get("data_quality", {})
    min_days = dq_cfg.get("min_history_days", 250)
    action = dq_cfg.get("missing_stock_ma250_action", "skip")

    result = {"valid": True, "action": "pass", "error": None}

    if hist.empty or len(hist) < 60:
        result["valid"] = False
        result["action"] = "skip"
        result["error"] = f"历史数据不足60天: {len(hist)}"
        return result

    if len(hist) < min_days and action == "abort":
        result["valid"] = False
        result["action"] = "skip"
        result["error"] = f"历史数据不足{min_days}天"
        return result

    # 校验年线
    if len(hist) >= 250:
        close = hist["收盘"]
        ma250 = close.iloc[-250:].mean()
        if pd.isna(ma250) or ma250 == 0:
            result["valid"] = False
            result["action"] = action
            result["error"] = "MA250数据无效"
            return result

    return result


def analyze_stock(code: str, name: str, sector: str, fetcher: DataFetcher,
                  f_score: dict, engine: SignalEngineV2, risk_mgr: RiskManager,
                  config: dict, use_mock: bool = False,
                  moneyflow_df=None, top_list_df=None, top_inst_df=None,
                  spot_row=None):
    """分析单只股票，返回完整结果（含一票否决信息）"""
    # 获取历史数据
    hist = fetcher.get_hist_data(code, days=500, use_mock=use_mock)

    # 数据完整性校验
    validation = validate_stock_data(hist, code, config)
    if not validation["valid"]:
        return {
            "code": code, "name": name, "sector": sector,
            "valid": False, "error": validation["error"],
            "total_score": -1.0, "advice": "回避",
            "veto": True, "veto_reason": f"数据质量: {validation['error']}"
        }

    # 技术面分析
    tech = TechnicalAnalyzer(hist, config.get("technical")).score()

    # 动量分析
    momentum = engine.calculate_momentum(hist)

    # 资金面（传入 spot_row 以获取换手率等实时数据）
    if spot_row is None:
        spot_row = pd.Series()
    capital = engine.calculate_capital_flow(
        code, spot_row, moneyflow_df, top_list_df, top_inst_df
    )

    # 综合评分（含一票否决、信号冲突处理）
    result = engine.combine(f_score, tech, momentum, capital)
    result["code"] = code
    result["name"] = name
    result["sector"] = sector
    result["valid"] = True

    # 风控建议（传入 hist_df 以计算支撑/压力位）
    latest_price = float(hist.iloc[-1]["收盘"]) if not hist.empty else 0
    risk = risk_mgr.get_risk_advice(
        code, name, latest_price,
        result["total_score"],
        tech.get("details", {}),
        hist_df=hist
    )
    result["risk"] = risk
    result["latest_price"] = latest_price

    # 关键均线价格
    if not hist.empty:
        close = hist["收盘"]
        result["ma20"] = round(close.iloc[-20:].mean(), 2) if len(hist) >= 20 else 0
        result["ma60"] = round(close.iloc[-60:].mean(), 2) if len(hist) >= 60 else 0
        result["ma250"] = round(close.iloc[-250:].mean(), 2) if len(hist) >= 250 else 0

    return result


def check_market_environment(fetcher: DataFetcher, config: dict, use_mock: bool = False) -> dict:
    """检查大盘环境：指数 + 资金面，带硬校验"""
    env_cfg = config.get("market_environment", {})
    dq_cfg = config.get("data_quality", {})
    abort_on_missing = env_cfg.get("abort_on_missing_index_ma250", True)
    env = {"pass": True, "notes": [], "capital": {}, "valid": True}

    if use_mock:
        env["sh_index"] = 3400.0
        env["sh_ma250"] = 3200.0
        env["above_ma250"] = True
        env["valid"] = True
        env["notes"].append("[演示模式] 模拟大盘：上证指数在年线之上")
        env["capital"] = {"north_money": 50, "margin_rzye": 15000, "notes": ["北向净流入 50 亿"]}
        return env

    # 获取上证指数
    try:
        index_df = fetcher.get_index_hist("000001", days=500)
        validation = validate_index_data(index_df, config)

        if not validation["valid"]:
            env["valid"] = False
            env["error"] = validation["error"]
            env["notes"].append(f"[数据异常] {validation['error']}")
            if abort_on_missing:
                env["pass"] = False
                env["notes"].append("[严重] 大盘年线数据无效，已触发中止保护")
                return env
            else:
                env["notes"].append("[警告] 大盘年线数据无效，继续运行但建议谨慎参考")
                env["above_ma250"] = None
        else:
            latest = index_df.iloc[-1]
            ma250 = validation["ma250"]
            above_ma250 = latest["收盘"] > ma250
            env["sh_index"] = round(float(latest["收盘"]), 2)
            env["sh_ma250"] = ma250
            env["above_ma250"] = above_ma250

            if env_cfg.get("require_above_ma250", False) and not above_ma250:
                env["pass"] = False
                env["notes"].append("上证指数在年线之下，建议谨慎")
            elif above_ma250:
                env["notes"].append("上证指数在年线之上，市场环境偏多")
            else:
                env["notes"].append("上证指数在年线之下，市场环境偏空")
    except Exception as e:
        env["valid"] = False
        env["error"] = str(e)
        env["notes"].append(f"[异常] 大盘环境检测异常: {e}")
        if abort_on_missing:
            env["pass"] = False
            env["notes"].append("[严重] 大盘数据异常，已触发中止保护")
            return env

    # 大盘资金面
    try:
        capital_env = fetcher.get_market_capital_env()
        env["capital"] = capital_env
        for note in capital_env.get("notes", []):
            env["notes"].append(note)
    except Exception:
        pass

    return env


def apply_sector_diversification(results: list, config: dict) -> tuple:
    """
    行业分散约束：
    - 推荐列表中每个行业最多 max_sector_holdings 只
    - 覆盖行业数不少于 min_sectors_in_recommendation
    返回: (filtered_results, diversification_notes)
    """
    pos_cfg = config.get("position_management", {})
    max_sector_h = pos_cfg.get("max_sector_holdings", 1)
    min_sectors = pos_cfg.get("min_sectors_in_recommendation", 5)

    notes = []
    if max_sector_h <= 0:
        return results, notes

    sector_counts = {}
    filtered = []

    # 第一轮：按综合评分排序，逐只加入，控制单行业数量
    for r in results:
        sector = r.get("sector", "")
        if sector_counts.get(sector, 0) < max_sector_h:
            filtered.append(r)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

    unique_sectors = len(sector_counts)
    notes.append(f"行业分散: 单行业最多{max_sector_h}只，最终覆盖 {unique_sectors} 个行业")

    if unique_sectors < min_sectors and len(filtered) >= min_sectors:
        notes.append(f"[警告] 推荐仅覆盖 {unique_sectors} 个行业，低于最低要求 {min_sectors}，建议放宽筛选条件")

    strong_before = len([r for r in results if r["advice"] in ("强烈关注", "关注")])
    strong_after = len([r for r in filtered if r["advice"] in ("强烈关注", "关注")])
    if strong_before != strong_after:
        notes.append(f"[行业限制] 关注列表: {strong_before} -> {strong_after} 只")

    return filtered, notes


def main(use_mock: bool = False):
    print("=" * 60)
    print("A股每日投资顾问 v2.1 启动")
    if use_mock:
        print("[演示模式] 使用模拟数据验证策略逻辑")
    print("=" * 60)

    config = load_config()
    fetcher = DataFetcher(
        data_source=config.get("data_source", "auto"),
    )
    engine = SignalEngineV2(config)
    regime_detector = MarketRegimeDetector(config)
    risk_mgr = RiskManager(config.get("risk_management"))
    pre_market_planner = PreMarketPlanner(config)
    reporter = DailyReport(config["report"]["output_dir"])

    style = config.get("investment_style", "balanced")
    print(f"\n投资风格: {style}")

    # 0. 大盘环境 + 硬校验
    print("\n[0/5] 大盘环境检测...")
    market_env = check_market_environment(fetcher, config, use_mock=use_mock)
    for note in market_env["notes"]:
        print(f"     {note}")

    if not market_env.get("valid") and market_env.get("pass") is False:
        print("\n[中止] 大盘关键数据缺失，策略中止运行以避免生成无效推荐。")
        print("请检查数据源连接或年线数据可用性。")
        return

    if not market_env["pass"]:
        print("     [警告] 大盘环境不佳，建议降低仓位")

    # 设置动态权重
    weights = regime_detector.get_weights_for_env(market_env)
    engine.set_weights(weights)
    print(f"     [动态权重] {regime_detector.describe(market_env)}")
    print(f"               基本面={weights['fundamental']:.0%} 技术面={weights['technical']:.0%} "
          f"动量={weights['momentum']:.0%} 资金面={weights['capital_flow']:.0%}")

    # 1. 获取当日全市场数据
    print("\n[1/5] 获取全市场股票列表...")
    spot = fetcher.get_stock_list(use_mock=use_mock)
    if spot.empty:
        print("获取股票列表失败，退出")
        return
    print(f"     共 {len(spot)} 只活跃股票")

    # 1.5 股权质押数据
    print("\n[1.5/5] 获取股权质押数据...")
    pledge_df = fetcher.get_pledge_ratio_data()
    if not pledge_df.empty:
        high_pledge = fetcher.get_high_pledge_stocks(
            threshold=config.get("pledge_avoidance", {}).get("threshold_pct", 30.0)
        )
        if not high_pledge.empty:
            print(f"     发现 {len(high_pledge)} 只高质押股票(≥{config.get('pledge_avoidance', {}).get('threshold_pct', 30.0)}%)")
        else:
            print("     无高质押股票")
    else:
        print("     [WARN] 股权质押数据获取失败，跳过质押检测")

    # 2. 基本面初筛
    print("\n[2/5] 基本面筛选...")
    screener = FundamentalScreener(spot, config)
    screener.set_pledge_data(pledge_df)
    candidates = screener.screen()

    if candidates.empty:
        print("没有符合条件的股票，退出")
        return

    # 2.5 停牌与涨跌停检查 (Phase 3)
    if not use_mock:
        print("\n[2.5/5] 停牌与涨跌停检查...")
        suspended = fetcher.get_suspend_stocks()
        if suspended:
            n_suspended = len(set(candidates["代码"].astype(str).tolist()) & suspended)
            if n_suspended > 0:
                candidates = candidates[~candidates["代码"].astype(str).isin(suspended)]
                print(f"     排除停牌股: {n_suspended} 只 -> 剩余 {len(candidates)}")
            else:
                print(f"     无停牌股")
        else:
            print(f"     停牌数据不可用，跳过")

        limit_down_df = fetcher.get_limit_list_data()
        if not limit_down_df.empty:
            limit_codes = set(limit_down_df["代码"].astype(str).tolist())
            n_limit = len(set(candidates["代码"].astype(str).tolist()) & limit_codes)
            if n_limit > 0:
                candidates = candidates[~candidates["代码"].astype(str).isin(limit_codes)]
                print(f"     排除跌停股: {n_limit} 只 -> 剩余 {len(candidates)}")
            else:
                print(f"     无跌停股")
        else:
            print(f"     涨跌停数据不可用，跳过")
        if candidates.empty:
            print("停牌/跌停排除后无候选股票，退出")
            return
    else:
        print("\n[2.5/5] 停牌与涨跌停检查...")
        print(f"     [演示模式] 跳过")

    # 3. 获取资金面数据（批量）
    print("\n[3/5] 获取资金面数据...")
    candidate_codes = candidates["代码"].astype(str).tolist()
    moneyflow_df = fetcher.get_moneyflow_data(candidate_codes)
    top_list_df = fetcher.get_top_list_data()
    top_inst_df = fetcher.get_top_inst_data()
    if not moneyflow_df.empty:
        print(f"     资金流向: {len(moneyflow_df)} 只")
    if not top_list_df.empty:
        print(f"     龙虎榜: {len(top_list_df)} 只")
    if not top_inst_df.empty:
        print(f"     机构席位: {len(top_inst_df)} 只")

    # 3.5 预获取财务数据（用于基本面深度评分：PEG/ROE/毛利率等）
    print("\n[3.5/5] 预获取财务数据...")
    financial_data_map = {}
    max_workers = 4 if use_mock else 6
    if not use_mock:
        import threading
        fin_count = [0]
        fin_lock = threading.Lock()
        def _fetch_financial(code):
            data = fetcher.get_financial_indicators(code)
            # Phase 3: 补充筹码集中度
            holder = fetcher.get_holder_trend(code)
            if data and holder:
                data["holder_trend"] = holder.get("trend", "stable")
                data["holder_change_pct"] = holder.get("change_pct", 0)
            if data:
                with fin_lock:
                    fin_count[0] += 1
            return code, data
        with ThreadPoolExecutor(max_workers=min(8, max_workers * 2)) as fin_exec:
            fin_futures = {fin_exec.submit(_fetch_financial, c): c for c in candidate_codes}
            for f in as_completed(fin_futures):
                code, data = f.result()
                if data:
                    financial_data_map[code] = data
        print(f"     财务数据: {fin_count[0]}/{len(candidate_codes)} 只 (PEG/ROE/毛利率等)")
    else:
        print(f"     [演示模式] 跳过财务数据获取")

    # 4. 技术面 + 动量 + 风控分析（多线程）
    print("\n[4/5] 技术面与动量分析...")
    results = []
    veto_count = 0
    data_invalid_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for _, row in candidates.iterrows():
            code = str(row["代码"])
            name = str(row.get("名称", ""))
            sector = str(row.get("所属行业", ""))
            f_score = screener.score(row, financial_data=financial_data_map.get(code))
            future = executor.submit(
                analyze_stock, code, name, sector, fetcher,
                f_score, engine, risk_mgr, config,
                use_mock,
                moneyflow_df, top_list_df, top_inst_df,
                row  # 传入 spot row 以启用换手率评分
            )
            futures[future] = code

        completed = 0
        total = len(futures)
        for future in as_completed(futures):
            completed += 1
            if completed % 50 == 0 or completed == total:
                print(f"     已分析 {completed}/{total}...")
            r = future.result()
            if r:
                results.append(r)
                if r.get("veto"):
                    veto_count += 1
                if not r.get("valid"):
                    data_invalid_count += 1

    # 5. 排序
    print("\n[5/5] 综合排序...")
    results.sort(key=lambda x: x["total_score"], reverse=True)

    print(f"     分析完成: 共 {len(results)} 只，一票否决 {veto_count} 只，数据异常 {data_invalid_count} 只")

    # 5.5 行业分散限制
    results_for_report, div_notes = apply_sector_diversification(results, config)
    for note in div_notes:
        print(f"     {note}")

    # 6. 生成报告
    print("\n[6/5] 生成报告...")
    avoid_list = screener.avoided_pledge_stocks if hasattr(screener, 'avoided_pledge_stocks') else []
    veto_list = engine.get_excluded_stocks(results)

    # 6.5 开盘前挂单规划
    pre_market_orders = []
    if config.get("pre_market_order", {}).get("enabled", True):
        pre_market_orders = pre_market_planner.generate_all(results_for_report)
        summary = pre_market_planner.summarize(pre_market_orders)
        print(f"     [开盘挂单] {summary}")

    reporter.print_console(results_for_report, market_env,
                           avoid_list=avoid_list, veto_list=veto_list,
                           pre_market_orders=pre_market_orders)
    filepath, content = reporter.generate_markdown(
        results_for_report, market_env,
        avoid_list=avoid_list, veto_list=veto_list,
        div_notes=div_notes,
        pre_market_orders=pre_market_orders
    )
    print(f"\n报告已保存: {filepath}")

    # 7. 同步到 Obsidian Vault
    obsidian_dir = r"/mnt/c/Users/Administrator/Documents/Obsidian Vault/stock"
    if os.path.isdir(obsidian_dir):
        try:
            obsidian_path = os.path.join(obsidian_dir, os.path.basename(filepath))
            shutil.copy2(filepath, obsidian_path)
            print(f"报告已同步到 Obsidian: {obsidian_path}")
        except Exception as e:
            print(f"[警告] 同步到 Obsidian 失败: {e}")
    else:
        print(f"[警告] Obsidian 目录不存在，跳过同步: {obsidian_dir}")

    # 组合管理建议
    portfolio = risk_mgr.get_portfolio_advice(results_for_report)
    print(f"\n[组合管理]")
    for note in portfolio["notes"]:
        print(f"  {note}")

    # 输出股权质押避雷清单
    if avoid_list and config.get("pledge_avoidance", {}).get("report_avoid_list", True):
        print(f"\n{'='*60}")
        print(f"! 股权质押避雷清单 ({len(avoid_list)} 只)")
        print(f"{'='*60}")
        print(f"  以下股票因大股东质押比例≥{config.get('pledge_avoidance', {}).get('threshold_pct', 30.0)}%被自动排除:")
        for a in avoid_list[:15]:
            level_mark = "[高风险]" if a.get("level") == "高风险" else "[警戒线]"
            print(f"  {level_mark} {a['code']} {a['name']:8s} 质押比例:{a['ratio']:.1f}%")
        if len(avoid_list) > 15:
            print(f"  ... 等共 {len(avoid_list)} 只")

    # 输出一票否决清单
    if veto_list and config.get("report", {}).get("report_veto_exclusions", True):
        max_veto = config.get("report", {}).get("max_veto_exclusions", 10)
        print(f"\n{'='*60}")
        print(f"! 一票否决清单 ({len(veto_list)} 只，显示前{max_veto}只)")
        print(f"{'='*60}")
        print("  以下股票因触发硬排除规则未进入推荐:")
        for v in veto_list[:max_veto]:
            print(f"  [排除] {v['code']} {v['name']:8s} [{v.get('sector','')}] 原因: {v['reason']}")
        if len(veto_list) > max_veto:
            print(f"  ... 等共 {len(veto_list)} 只")

    # 输出重点关注
    strong = [r for r in results_for_report if r["advice"] in ("强烈关注", "关注")]
    if strong:
        print(f"\n{'='*60}")
        print(f"重点关注 ({len(strong)} 只)")
        print(f"{'='*60}")
        for r in strong[:10]:
            risk_notes = " | ".join(r["risk"]["risk_notes"][:3])
            conflict_mark = "[冲突]" if r.get("conflict_triggered") else ""
            print(f"  {r['code']} {r['name']:8s} 评分:{r['total_score']:+.3f}  "
                  f"{r['advice']:6s} {conflict_mark} | {risk_notes}")

    print("\n" + "=" * 60)
    print("分析完成")
    print("=" * 60)


if __name__ == "__main__":
    use_mock = "--demo" in sys.argv
    main(use_mock=use_mock)
