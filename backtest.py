"""
策略回测模块 - 复盘历史数据，评估策略有效性

用法:
    python backtest.py                    # 回测上个月数据（每周一选股）
    python backtest.py --month 2026-05    # 回测指定月份
    python backtest.py --demo             # 使用模拟数据快速验证
"""
import sys
import os
import yaml
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import warnings

warnings.filterwarnings("ignore")

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.fundamental import FundamentalScreener
from strategies.technical import TechnicalAnalyzer
from strategies.signal_engine_v2 import SignalEngineV2
from strategies.market_regime import MarketRegimeDetector
from strategies.risk_manager import RiskManager


class TushareBacktester:
    """基于tushare的策略回测器"""

    def __init__(self, config_path="config/settings.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        # 初始化 tushare pro
        token_path = os.path.join(os.path.dirname(config_path), ".tushare_token")
        with open(token_path, "r", encoding="utf-8") as f:
            token = f.read().strip()

        import tushare as ts
        self.pro = ts.pro_api(token)

        self.engine = SignalEngineV2(self.config)
        self.regime_detector = MarketRegimeDetector(self.config)
        self.risk_mgr = RiskManager(self.config.get("risk_management"))

        # 缓存
        self._hist_cache = {}
        self._index_cache = None
        self._spot_cache = None
        self._pledge_cache = None
        self._basics_cache = None          # 股票基本信息缓存（P0-1 修复）
        self._daily_basic_cache = {}       # 按日期缓存 daily_basic（P0-1 修复）

        # 交易成本（P0-2 修复）
        self.commission_rate = 0.00015     # 佣金万1.5，买卖双向
        self.stamp_tax_rate = 0.001        # 印花税千1，仅卖出

    def get_pledge_data(self):
        """获取股权质押数据（用于回测过滤）"""
        if self._pledge_cache is not None:
            return self._pledge_cache
        try:
            import akshare as ak
            df = ak.stock_gpzy_pledge_ratio_em()
            if df is not None and not df.empty:
                rename_map = {
                    "股票代码": "代码",
                    "股票简称": "名称",
                    "质押比例": "质押比例",
                }
                actual_rename = {k: v for k, v in rename_map.items() if k in df.columns}
                df = df.rename(columns=actual_rename)
                if "代码" in df.columns:
                    df["代码"] = df["代码"].astype(str).str.strip()
                if "质押比例" in df.columns:
                    df["质押比例"] = pd.to_numeric(df["质押比例"], errors="coerce").fillna(0)
                self._pledge_cache = df
                return df
        except Exception as e:
            print(f"  [WARN] 股权质押数据获取失败: {e}")
        return pd.DataFrame()

    def _get_recent_trade_date_before(self, target_date, days_back=10):
        """获取 target_date 之前最近的交易日（P0-1 修复：消除未来数据泄漏）"""
        target = pd.Timestamp(target_date)
        for i in range(1, days_back + 1):
            d = (target - timedelta(days=i)).strftime("%Y%m%d")
            try:
                df = self.pro.daily(trade_date=d, limit=1)
                if df is not None and not df.empty:
                    return d
            except Exception:
                continue
        #  fallback：直接返回3天前
        return (target - timedelta(days=3)).strftime("%Y%m%d")

    def get_trade_dates(self, start_date, end_date):
        """获取指定范围内的交易日（排除主要节假日）"""
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        dates = pd.bdate_range(start=start, end=end)
        trade_dates = []
        for d in dates:
            # 排除五一假期 (5.1-5.5)
            if d.month == 5 and 1 <= d.day <= 5:
                continue
            # 排除国庆假期 (10.1-10.7)
            if d.month == 10 and 1 <= d.day <= 7:
                continue
            # 排除春节假期 (1月底-2月初，约7天)
            if d.month == 1 and 25 <= d.day <= 31:
                continue
            if d.month == 2 and 1 <= d.day <= 6:
                continue
            trade_dates.append(d)
        return trade_dates

    def get_weekly_dates(self, year, month):
        """获取某月每周的第一个交易日"""
        start_date = f"{year}-{month:02d}-01"
        if month == 12:
            next_month = datetime(year + 1, 1, 1)
        else:
            next_month = datetime(year, month + 1, 1)
        end_date = (next_month - timedelta(days=1)).strftime("%Y-%m-%d")
        return self._get_mondays_in_range(start_date, end_date)

    def get_weekly_dates_range(self, start_date, end_date):
        """获取指定日期范围内每周一的交易日列表"""
        return self._get_mondays_in_range(start_date, end_date)

    def _get_mondays_in_range(self, start_date, end_date):
        """获取日期范围内每周一的交易日"""
        dates = self.get_trade_dates(start_date, end_date)
        weekly = {}
        for d in dates:
            week = d.isocalendar()[1]
            year = d.isocalendar()[0]
            key = f"{year}-W{week:02d}"
            if key not in weekly:
                weekly[key] = d
        return sorted(weekly.values())

    def _get_stock_basics(self):
        """获取股票基本信息（只做一次，缓存）（P0-1 修复）"""
        if self._basics_cache is not None:
            return self._basics_cache
        basics = self.pro.stock_basic(exchange="", list_status="L")
        basics = basics[["symbol", "name", "industry"]].copy()
        basics.rename(columns={"symbol": "代码", "name": "名称", "industry": "所属行业"}, inplace=True)
        self._basics_cache = basics
        return basics

    def _get_daily_basic_cached(self, trade_date):
        """按日期缓存获取 daily_basic（P0-1 修复）"""
        if trade_date in self._daily_basic_cache:
            return self._daily_basic_cache[trade_date]
        daily = self.pro.daily_basic(trade_date=trade_date)
        if daily.empty:
            prev_date = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
            daily = self.pro.daily_basic(trade_date=prev_date)
            if not daily.empty:
                trade_date = prev_date
        self._daily_basic_cache[trade_date] = daily
        return daily

    def _merge_spot(self, basics, daily):
        """合并基本信息和估值数据（P0-1 修复）"""
        if daily.empty:
            return pd.DataFrame()
        daily = daily.copy()
        daily.rename(columns={
            "ts_code": "tscode",
            "close": "收盘价",
            "pe_ttm": "市盈率",
            "pb": "市净率",
            "dv_ttm": "股息率",
            "total_mv": "总市值",
            "circ_mv": "流通市值",
            "turnover_rate": "换手率",
            "pct_chg": "涨跌幅",
            "vol": "成交量",
            "amount": "成交额"
        }, inplace=True, errors="ignore")
        if "tscode" in daily.columns:
            daily["代码"] = daily["tscode"].str.replace(r"\.SH|\.SZ", "", regex=True)
        # 单位转换：万元 -> 元
        if "总市值" in daily.columns:
            daily["总市值"] = pd.to_numeric(daily["总市值"], errors="coerce") * 10000
        if "流通市值" in daily.columns:
            daily["流通市值"] = pd.to_numeric(daily["流通市值"], errors="coerce") * 10000
        df = pd.merge(basics, daily, on="代码", how="inner")
        df = df[df["收盘价"] > 0]
        df = df[df["换手率"] > 0]
        for col in ["振幅", "最高", "最低", "今开", "昨收"]:
            if col not in df.columns:
                df[col] = 0
        return df

    def get_stock_list(self, trade_date=None):
        """
        获取股票列表（合并基础信息+估值数据）
        trade_date: 估值数据日期，默认使用最近的daily_basic日期
        """
        if self._spot_cache is not None:
            return self._spot_cache

        print("  获取股票基础信息...")
        basics = self._get_stock_basics()

        if trade_date is None:
            trade_date = self._get_recent_trade_date()

        print(f"  获取估值数据 (日期: {trade_date})...")
        daily = self._get_daily_basic_cached(trade_date)
        if daily.empty:
            prev_date = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
            daily = self._get_daily_basic_cached(prev_date)
            if not daily.empty:
                trade_date = prev_date
                print(f"  回退到前一交易日: {trade_date}")

        df = self._merge_spot(basics, daily)
        self._spot_cache = df
        return df

    def _get_recent_trade_date(self, days_back=10):
        """获取最近的交易日"""
        for i in range(days_back):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
            df = self.pro.daily(trade_date=d, limit=1)
            if df is not None and not df.empty:
                return d
        return datetime.now().strftime("%Y%m%d")

    def _get_hist_tushare(self, code, start_date, end_date):
        """通过tushare获取历史K线"""
        if code in self._hist_cache:
            return self._hist_cache[code]

        try:
            suffix = ".SH" if code.startswith("6") else ".SZ"
            ts_code = f"{code}{suffix}"
            df = self.pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            if df is not None and not df.empty and len(df) >= 60:
                df.rename(columns={
                    "trade_date": "日期",
                    "open": "开盘",
                    "high": "最高",
                    "low": "最低",
                    "close": "收盘",
                    "vol": "成交量",
                    "amount": "成交额",
                    "pct_chg": "涨跌幅",
                    "pre_close": "昨收"
                }, inplace=True)
                df["日期"] = pd.to_datetime(df["日期"])
                df = df.sort_values("日期").reset_index(drop=True)
                self._hist_cache[code] = df
                return df
        except Exception as e:
            pass

        self._hist_cache[code] = None
        return None

    def apply_sector_limit(self, results: list, max_per_sector: int = 2) -> list:
        """应用单行业持仓数量上限，按评分从高到低选，同行业最多保留N只"""
        if max_per_sector <= 0:
            return results

        sector_counts = {}
        filtered = []
        dropped = []

        for r in results:
            sector = r.get("sector", "")
            if not sector:
                sector = "其他"

            if sector_counts.get(sector, 0) < max_per_sector:
                filtered.append(r)
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
            else:
                dropped.append((r["code"], r["name"], sector, r["total_score"]))

        return filtered, dropped

    def get_index_hist(self, days=400):
        """获取上证指数历史"""
        if self._index_cache is not None:
            return self._index_cache

        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

        try:
            df = self.pro.index_daily(ts_code="000001.SH", start_date=start, end_date=end)
            if df is not None and not df.empty:
                df.rename(columns={
                    "trade_date": "日期",
                    "open": "开盘",
                    "high": "最高",
                    "low": "最低",
                    "close": "收盘",
                    "pct_chg": "涨跌幅"
                }, inplace=True)
                df["日期"] = pd.to_datetime(df["日期"])
                df = df.sort_values("日期").reset_index(drop=True)
                self._index_cache = df
                return df
        except Exception as e:
            print(f"  大盘数据获取失败: {e}")

        return pd.DataFrame()

    def run_backtest(self, backtest_dates, eval_end_date=None, top_n=10,
                     advice_filter=("强烈关注", "关注"), use_weekly_hold=True,
                     stop_loss_mode=None):
        """
        运行回测
        use_weekly_hold: True=逐周持有(买入持有到下一周), False=持有到评估截止日
        """
        if eval_end_date is None:
            eval_end_date = datetime.now()
        eval_ts = pd.Timestamp(eval_end_date)

        # 回测日格式化，并添加下一期日期映射（用于周收益计算）
        bt_info = []
        for i, d in enumerate(backtest_dates):
            next_d = backtest_dates[i + 1] if i + 1 < len(backtest_dates) else eval_end_date
            bt_info.append({
                "date": d,
                "date_str": d.strftime("%Y-%m-%d"),
                "next_date": next_d,
                "next_date_str": next_d.strftime("%Y-%m-%d")
            })

        mode_str = "逐周轮动" if use_weekly_hold else "持有至期末"
        sl_mode = stop_loss_mode or self.config.get("risk_management", {}).get("stop_loss_method", "support_resistance")
        print("=" * 70)
        print("  策略回测报告")
        print("  策略: 多因子选股 v2 (动态权重 + 一票否决 + 信号冲突处理)")
        print(f"  回测周期: {backtest_dates[0].strftime('%Y-%m-%d')} ~ {eval_end_date.strftime('%Y-%m-%d')}")
        print(f"  回测期数: {len(backtest_dates)} 期 ({mode_str})")
        print(f"  止损模式: {sl_mode}")
        print(f"  交易成本: 佣金{self.commission_rate*10000:.1f}‰ + 印花税{self.stamp_tax_rate*100:.1f}%")
        print(f"  评估截止: {eval_ts.strftime('%Y-%m-%d')}")
        print("  数据源: tushare + akshare")
        print("  [注意] 估值数据按回测日独立获取，消除未来数据泄漏 (P0-1修复)")
        print("=" * 70)

        # 1. 获取股票基本信息（只做一次）
        print("\n[1/8] 获取股票基础信息...")
        basics = self._get_stock_basics()
        print(f"  全市场: {len(basics)} 只")

        # 2. 获取股权质押数据
        print("\n[2/8] 获取股权质押数据...")
        pledge_df = self.get_pledge_data()
        if not pledge_df.empty:
            high_pledge = pledge_df[pledge_df["质押比例"] >= 30.0]
            print(f"  发现 {len(high_pledge)} 只高质押股票(>=30%)")
        else:
            print("  [WARN] 股权质押数据获取失败，跳过质押检测")

        # 3. 获取期初估值数据，做宽松筛选获取K线范围（P0-1：用期初数据而非期末）
        print("\n[3/8] 获取期初估值数据（宽松筛选，用于预取K线）...")
        initial_trade_date = self._get_recent_trade_date_before(backtest_dates[0])
        daily_initial = self._get_daily_basic_cached(initial_trade_date)
        spot_initial = self._merge_spot(basics, daily_initial)
        if spot_initial.empty:
            print("  [错误] 无法获取股票列表")
            return None

        # 宽松筛选：只用行业+ST排除（不依赖估值，确保K线覆盖范围足够）
        screener_loose = FundamentalScreener(spot_initial, self.config)
        screener_loose.set_pledge_data(pledge_df)
        # 临时放宽估值条件，扩大K线预取范围
        loose_config = self.config.copy()
        loose_config["valuation"] = {"max_pe": 0, "max_pb": 0, "min_dividend_yield": 0, "exclude_negative_pe": False}
        screener_loose.valuation_cfg = loose_config.get("valuation", {})
        candidates_loose = screener_loose.screen()
        print(f"  宽松筛选候选池: {len(candidates_loose)} 只")

        # 4. 获取历史K线（基于期初宽松候选池）
        print("\n[4/8] 获取历史K线数据...")
        print("  注意: 全市场K线获取需要一定时间（约5-15分钟）")
        print("  可按 Ctrl+C 中断\n")

        start_k = (backtest_dates[0] - timedelta(days=300)).strftime("%Y%m%d")
        end_k = eval_end_date.strftime("%Y%m%d")

        valid_hists = {}
        failed_codes = []
        loose_codes = candidates_loose["代码"].astype(str).tolist()

        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(self._get_hist_tushare, code, start_k, end_k): code
                for code in loose_codes
            }
            completed = 0
            for future in as_completed(futures):
                completed += 1
                code = futures[future]
                if completed % 50 == 0 or completed == len(futures):
                    pct = completed / len(futures) * 100
                    print(f"  进度: {completed}/{len(futures)} ({pct:.0f}%)", end="\r")

                try:
                    hist = future.result()
                    if hist is not None:
                        valid_hists[code] = hist
                    else:
                        failed_codes.append(code)
                except Exception:
                    failed_codes.append(code)

        print(f"\n  成功获取K线: {len(valid_hists)} 只")
        if failed_codes:
            print(f"  获取失败/停牌: {len(failed_codes)} 只")

        # 4.5 预获取财务数据（用于基本面深度评分：PEG/ROE/毛利率等）
        print("\n[4.5/8] 预获取财务数据...")
        financial_data_map = {}
        import threading
        fin_count = [0]
        fin_lock = threading.Lock()
        def _fetch_financial_bt(code):
            try:
                suffix = ".SH" if code.startswith("6") else ".SZ"
                pro = self.pro
                df = pro.fina_indicator(ts_code=f"{code}{suffix}", limit=1)
                if df is not None and not df.empty:
                    r = df.iloc[0]
                    data = {
                        "roe": float(r.get("roe", 0) or 0),
                        "gross_margin": float(r.get("grossprofit_margin", 0) or 0),
                        "net_margin": float(r.get("netprofit_margin", 0) or 0),
                        "revenue_growth": float(r.get("q_sales_yoy", 0) or 0),
                        "profit_growth_yoy": float(r.get("q_netprofit_yoy", 0) or 0),
                        "debt_ratio": float(r.get("debt_to_assets", 0) or 0),
                    }
                    if data.get("roe") or data.get("gross_margin"):
                        with fin_lock:
                            fin_count[0] += 1
                        return code, data
            except Exception:
                pass
            return code, {}
        with ThreadPoolExecutor(max_workers=8) as fin_exec:
            fin_futures = {fin_exec.submit(_fetch_financial_bt, c): c for c in loose_codes}
            for f in as_completed(fin_futures):
                code, data = f.result()
                if data:
                    financial_data_map[code] = data
        print(f"     财务数据: {fin_count[0]}只 (PEG/ROE/毛利率等)")

        # 5. 逐期回测（P0-1：每期独立获取估值数据，消除未来数据泄漏）
        print("\n[5/8] 运行回测分析（每期独立估值）...")
        all_results = {}

        for info in bt_info:
            date_str = info["date_str"]
            bt_ts = pd.Timestamp(info["date"])
            print(f"\n  --- 回测日: {date_str} ---")

            # === v2: 检测当期市场状态，设置动态权重 ===
            sh_hist = self.get_index_hist(days=400)
            sh_bt = sh_hist[sh_hist["日期"] <= bt_ts] if not sh_hist.empty else sh_hist
            if not sh_bt.empty and len(sh_bt) >= 250:
                latest_close = float(sh_bt.iloc[-1]["收盘"])
                sh_ma250 = float(sh_bt["收盘"].iloc[-250:].mean())
                market_env = {
                    "sh_index": latest_close,
                    "sh_ma250": sh_ma250,
                    "above_ma250": latest_close > sh_ma250
                }
            else:
                market_env = {"above_ma250": None}
            weights = self.regime_detector.get_weights_for_env(market_env)
            self.engine.set_weights(weights)

            # 获取该期之前最近的估值数据（P0-1 修复核心）
            period_trade_date = self._get_recent_trade_date_before(bt_ts)
            daily_period = self._get_daily_basic_cached(period_trade_date)
            spot_period = self._merge_spot(basics, daily_period)

            if spot_period.empty:
                print(f"  [WARN] {period_trade_date} 估值数据为空，跳过本期")
                continue

            # 本期严格筛选
            screener = FundamentalScreener(spot_period, self.config)
            screener.set_pledge_data(pledge_df)
            candidates = screener.screen()
            if candidates.empty:
                print(f"  本期基本面筛选后无候选股票")
                continue

            daily_results = []
            skipped = 0

            for _, row in candidates.iterrows():
                code = str(row["代码"])
                if code not in valid_hists:
                    skipped += 1
                    continue

                hist = valid_hists[code]
                hist_bt = hist[hist["日期"] <= bt_ts].copy()
                if len(hist_bt) < 60:
                    skipped += 1
                    continue

                day_rows = hist_bt[hist_bt["日期"] == bt_ts]
                if day_rows.empty:
                    skipped += 1
                    continue

                # 基本面评分（使用本期估值数据 + 财务数据）
                f_score = screener.score(row, financial_data=financial_data_map.get(code))

                # 技术面分析
                tech = TechnicalAnalyzer(hist_bt, self.config.get("technical")).score()

                # 动量分析
                momentum = self.engine.calculate_momentum(hist_bt)

                # 资金面（历史回测无可用的 moneyflow/top_list 数据，仅换手率可用）
                spot_row_data = row.copy() if row is not None else pd.Series()
                capital = self.engine.calculate_capital_flow(
                    code, spot_row_data,
                    moneyflow_df=None, top_list_df=None, top_inst_df=None
                )

                # 综合评分（v2: 含一票否决、信号冲突处理）
                result = self.engine.combine(f_score, tech, momentum, capital)
                result["code"] = code
                result["name"] = str(row.get("名称", ""))
                result["sector"] = str(row.get("所属行业", ""))
                result["entry_price"] = float(day_rows.iloc[-1]["收盘"])
                result["entry_date"] = date_str

                daily_results.append(result)

            daily_results.sort(key=lambda x: x["total_score"], reverse=True)
            filtered = [r for r in daily_results if r["advice"] in advice_filter]

            # 应用单行业持仓数量上限
            max_sector = self.config.get("position_management", {}).get("max_sector_holdings", 2)
            filtered_before = len(filtered)
            filtered, dropped = self.apply_sector_limit(filtered, max_sector)
            if dropped:
                dropped_str = ", ".join([f"{d[0]}({d[2]})" for d in dropped[:3]])
                if len(dropped) > 3:
                    dropped_str += f" 等{len(dropped)}只"
                print(f"  行业限制: 从{filtered_before}只降至{len(filtered)}只 (排除{dropped_str})")

            all_results[date_str] = {
                "all": daily_results,
                "filtered": filtered[:top_n],
                "next_date": info["next_date_str"]
            }

            print(f"  估值日期: {period_trade_date}, 候选: {len(candidates)} 只")
            print(f"  分析: {len(daily_results)} 只 (跳过{skipped})")
            print(f"  符合条件: {len(filtered)} 只, 取前 {min(top_n, len(filtered))} 只")

        # 6. 计算收益率（P0-2 修复：加入交易成本 + P1-6 修复：止损逻辑）
        print(f"\n[6/8] 计算收益率 ({mode_str})...")
        print(f"  成本模型: 买入+{self.commission_rate*10000:.2f}‰, 卖出+{(self.commission_rate+self.stamp_tax_rate)*10000:.2f}‰")
        print(f"  止损模式: {sl_mode}")

        stop_loss_stats = {"triggered": 0, "saved": 0.0}  # 止损统计

        for date_str, results in all_results.items():
            for pick in results["filtered"]:
                code = pick["code"]
                hist = valid_hists.get(code)
                if hist is None:
                    continue

                entry_price = pick["entry_price"]
                # 买入成本价（含佣金）
                entry_cost = entry_price * (1 + self.commission_rate)

                if use_weekly_hold:
                    next_date_str = results.get("next_date", eval_end_date.strftime("%Y-%m-%d"))
                    next_ts = pd.Timestamp(next_date_str)
                    hold_period = hist[(hist["日期"] > pd.Timestamp(date_str)) & (hist["日期"] <= next_ts)]
                else:
                    hold_period = hist[(hist["日期"] > pd.Timestamp(date_str)) & (hist["日期"] <= eval_ts)]

                exit_price = None
                exit_date = None
                stop_triggered = False

                if not hold_period.empty:
                    # P1-6: 止损检查（逐日检查持有期间）
                    stop_loss_price = None
                    if sl_mode == "fixed":
                        stop_loss_price = entry_price * (1 + self.config.get("risk_management", {}).get("stop_loss_pct", -8.0) / 100)
                    elif sl_mode == "support_resistance":
                        # 使用买入日之前的K线计算支撑位
                        hist_before_entry = hist[hist["日期"] <= pd.Timestamp(date_str)]
                        if not hist_before_entry.empty:
                            sr = self.risk_mgr._calculate_support_resistance(hist_before_entry)
                            if sr.get("support"):
                                max_sl = entry_price * (1 + self.config.get("risk_management", {}).get("stop_loss_pct", -8.0) / 100)
                                stop_loss_price = max(sr["support"], max_sl * 0.95)
                            else:
                                stop_loss_price = entry_price * (1 + self.config.get("risk_management", {}).get("stop_loss_pct", -8.0) / 100)

                    if stop_loss_price:
                        # 逐日检查是否触发止损
                        for _, day_row in hold_period.iterrows():
                            day_low = day_row.get("最低", day_row["收盘"])
                            if day_low <= stop_loss_price:
                                exit_price = stop_loss_price
                                exit_date = day_row["日期"]
                                stop_triggered = True
                                stop_loss_stats["triggered"] += 1
                                # 计算如果不止损的亏损（用于统计止损效果）
                                final_row = hold_period.iloc[-1]
                                no_sl_ret = (final_row["收盘"] - entry_price) / entry_price * 100
                                sl_ret = (stop_loss_price - entry_cost) / entry_cost * 100
                                if no_sl_ret < sl_ret:
                                    stop_loss_stats["saved"] += (sl_ret - no_sl_ret)
                                break

                    # 未触发止损，用期末价格退出
                    if not stop_triggered:
                        final_row = hold_period.iloc[-1]
                        exit_price = float(final_row["收盘"])
                        exit_date = final_row["日期"]
                else:
                    # 无持有期数据，用买入价退出（无收益）
                    exit_price = entry_price
                    exit_date = pd.Timestamp(date_str)

                # P0-2: 计算净收益率（扣除交易成本）
                if exit_price:
                    # 卖出收入（扣除佣金+印花税）
                    exit_revenue = exit_price * (1 - self.commission_rate - self.stamp_tax_rate)
                    net_ret = (exit_revenue - entry_cost) / entry_cost * 100
                    pick["exit_price"] = round(exit_price, 2)
                    pick["return_pct"] = round(net_ret, 2)
                    pick["exit_date"] = exit_date.strftime("%Y-%m-%d") if hasattr(exit_date, "strftime") else str(exit_date)
                    pick["stop_triggered"] = stop_triggered
                    if stop_triggered:
                        pick["stop_loss_price"] = round(stop_loss_price, 2)

        # 打印止损统计
        if stop_loss_stats["triggered"] > 0:
            print(f"\n  [止损统计] 触发{stop_loss_stats['triggered']}次, 减少损失约{stop_loss_stats['saved']:.2f}%")

        # 7. 获取大盘基准（逐期对应）
        print("\n[7/8] 获取大盘基准...")
        benchmark_returns = {}
        sh_hist = self.get_index_hist(days=400)
        if not sh_hist.empty:
            for info in bt_info:
                date_str = info["date_str"]
                bt_ts = pd.Timestamp(info["date"])

                if use_weekly_hold:
                    next_ts = pd.Timestamp(info["next_date"])
                    exit_rows = sh_hist[sh_hist["日期"] <= next_ts]
                else:
                    exit_rows = sh_hist[sh_hist["日期"] <= eval_ts]

                entry_rows = sh_hist[sh_hist["日期"] == bt_ts]

                if not entry_rows.empty and not exit_rows.empty:
                    entry = float(entry_rows.iloc[-1]["收盘"])
                    exit_p = float(exit_rows.iloc[-1]["收盘"])
                    benchmark_returns[date_str] = round((exit_p - entry) / entry * 100, 2)
                    print(f"  {date_str} 上证指数: {entry:.2f} -> {exit_p:.2f} ({benchmark_returns[date_str]:+.2f}%)")

        # 8. 生成报告
        print("\n[8/8] 生成回测报告...")
        self._print_report(all_results, benchmark_returns, use_weekly_hold, stop_loss_stats)
        self._save_report(all_results, benchmark_returns, backtest_dates, eval_ts, use_weekly_hold, stop_loss_stats)
        # 使用最后一期的 screener 保存避雷清单（如果有的话）
        last_screener = None
        for info in bt_info:
            date_str = info["date_str"]
            if date_str in all_results and all_results[date_str]["filtered"]:
                # 重新获取最后一期的screener用于保存避雷清单
                last_trade_date = self._get_recent_trade_date_before(info["date"])
                daily_last = self._get_daily_basic_cached(last_trade_date)
                spot_last = self._merge_spot(basics, daily_last)
                if not spot_last.empty:
                    last_screener = FundamentalScreener(spot_last, self.config)
                    last_screener.set_pledge_data(pledge_df)
                    last_screener.screen()
                break
        if last_screener and hasattr(last_screener, 'avoided_pledge_stocks') and last_screener.avoided_pledge_stocks:
            self._save_avoid_list(last_screener.avoided_pledge_stocks, backtest_dates[0].strftime("%Y%m%d"))

        return all_results, benchmark_returns

    def _print_factor_ic(self, all_results):
        """Phase 4: 因子IC分析 — 各因子得分与收益的秩相关系数"""
        # 收集所有有收益数据的股票（因子分+收益率）
        records = []
        for date_str, results in all_results.items():
            # 从 filtered 收集（有 return_pct），从 all 获取因子明细
            filtered = {p["code"]: p for p in results.get("filtered", [])}
            daily = {p["code"]: p for p in results.get("all", []) if not p.get("veto")}
            for code, pick in filtered.items():
                if code in daily and "return_pct" in pick:
                    details = daily[code].get("details", {})
                    records.append({
                        "fundamental": details.get("fundamental", {}).get("score", 0),
                        "technical": details.get("technical", {}).get("score", 0),
                        "momentum": details.get("momentum", {}).get("score", 0),
                        "capital_flow": details.get("capital_flow", {}).get("score", 0),
                        "total_score": daily[code].get("total_score", 0),
                        "return": pick["return_pct"]
                    })

        if len(records) < 10:
            return

        records.sort(key=lambda x: x["return"])
        n = len(records)
        ranks_ret = {i: np.searchsorted(np.sort([r["return"] for r in records]),
                      records[i]["return"]) for i in range(n)}

        print(f"\n  {'='*70}")
        print("  >> 因子 IC 分析 (Rank IC)")
        print(f"  {'='*70}")
        print(f"  样本数: {n} 只 (有收益率数据的精选标的)")
        print(f"  {'因子':<16} {'Rank IC':<10} {'IC方向':<10} {'有效性'}")
        print(f"  {'─'*50}")

        factor_names = {
            "fundamental": "基本面因子",
            "technical": "技术面因子",
            "momentum": "动量因子",
            "capital_flow": "资金面因子",
            "total_score": "综合评分",
        }

        ic_results = {}
        for factor_key, factor_label in factor_names.items():
            scores = np.array([r[factor_key] for r in records])
            returns = np.array([r["return"] for r in records])
            if np.std(scores) > 0 and np.std(returns) > 0:
                # Rank IC: Spearman correlation between factor score and return
                rank_ic = np.corrcoef(
                    np.argsort(np.argsort(scores)),
                    np.argsort(np.argsort(returns))
                )[0, 1]
                ic_results[factor_key] = rank_ic
                if abs(rank_ic) > 0.2:
                    effectiveness = "[显著]"
                elif abs(rank_ic) > 0.1:
                    effectiveness = "[一般]"
                elif abs(rank_ic) > 0.05:
                    effectiveness = "[微弱]"
                else:
                    effectiveness = "[无效]"
                print(f"  {factor_label:<16} {rank_ic:+.4f}    {direction:<10} {effectiveness}")

        # IC 汇总建议
        if ic_results:
            valid_ic = {k: v for k, v in ic_results.items() if abs(v) > 0.05}
            if valid_ic:
                best_factor = max(valid_ic, key=lambda k: abs(valid_ic[k]))
                worst_factor = min(valid_ic, key=lambda k: abs(valid_ic[k]))
                factor_labels_cn = {
                    "fundamental": "基本面因子",
                    "technical": "技术面因子",
                    "momentum": "动量因子",
                    "capital_flow": "资金面因子",
                    "total_score": "综合评分",
                }
                print(f"\n  [IC 建议] 最强因子: {factor_labels_cn.get(best_factor, best_factor)} ({ic_results[best_factor]:+.3f})")
                if abs(ic_results[worst_factor]) < 0.05:
                    print(f"  [IC 建议] {factor_labels_cn.get(worst_factor, worst_factor)} 相关性极弱，可考虑降低权重")

    def _print_report(self, all_results, benchmark_returns, use_weekly_hold=True, stop_loss_stats=None):
        """打印回测报告（含累积收益、最大回撤等）"""
        print("\n" + "=" * 70)
        print("  >> 回测结果详细报告")
        hold_str = "(逐周轮动)" if use_weekly_hold else "(持有至期末)"
        print(f"  {hold_str}")
        if stop_loss_stats and stop_loss_stats.get("triggered", 0) > 0:
            print(f"  止损触发: {stop_loss_stats['triggered']}次, 减少损失约{stop_loss_stats.get('saved', 0):.2f}%")
        print("=" * 70)

        total_picks = 0
        total_positive = 0
        total_return = 0
        max_gain = -999
        max_loss = 999
        best_pick = None
        worst_pick = None

        # 逐期记录组合收益（用于计算累积收益和最大回撤）
        period_returns = []  # [(date_str, strategy_return, bench_return), ...]

        for date_str in sorted(all_results.keys()):
            picks = all_results[date_str]["filtered"]
            if not picks:
                continue

            bench_ret = benchmark_returns.get(date_str, "N/A")
            next_date = all_results[date_str].get("next_date", "评估截止")

            print(f"\n{'─'*70}")
            if use_weekly_hold:
                print(f"【回测日: {date_str} | 持有至: {next_date}】")
            else:
                print(f"【回测日: {date_str} | 持有至今日】")
            if isinstance(bench_ret, (int, float)):
                print(f"  上证指数同期收益: {bench_ret:+.2f}%")
            print()

            print(f"  {'排':<3} {'代码':<8} {'名称':<8} {'行业':<8} {'评分':<8} {'买入价':<10} {'卖出价':<10} {'收益率':<10}")
            print(f"  {'─'*65}")

            for i, pick in enumerate(picks, 1):
                ret = pick.get("return_pct", 0)
                entry = pick.get("entry_price", 0)
                exit_p = pick.get("exit_price", 0)
                name = pick["name"][:6] if pick["name"] else ""
                sector = pick.get("sector", "")[:6]

                ret_str = f"{ret:+.2f}%"
                if ret >= 10:
                    ret_marker = "***"
                elif ret > 0:
                    ret_marker = "(+)"
                elif ret > -5:
                    ret_marker = "(-)"
                else:
                    ret_marker = "!!!"

                print(f"  {i:<3} {pick['code']:<8} {name:<8} {sector:<8} "
                      f"{pick['total_score']:<8} {entry:<10.2f} {exit_p:<10.2f} {ret_str:<8} {ret_marker}")

                total_picks += 1
                if ret > 0:
                    total_positive += 1
                total_return += ret

                if ret > max_gain:
                    max_gain = ret
                    best_pick = pick
                if ret < max_loss:
                    max_loss = ret
                    worst_pick = pick

            # 记录本期组合收益（ATR波动率加权）
            if picks:
                # 构建 code -> ATR 映射
                all_picks = all_results[date_str].get("all", [])
                atr_map = {}
                for ap in all_picks:
                    code = ap.get("code", "")
                    atr = ap.get("details", {}).get("technical", {}).get("details", {}).get("atr")
                    if atr and atr > 0:
                        atr_map[code] = atr

                if atr_map and len(atr_map) >= len(picks) * 0.5:
                    # ATR倒数加权：波动率越低的股票仓位越高
                    inv_atr = {c: 1.0 / atr_map.get(c, 1) for c in [p["code"] for p in picks]}
                    total_inv = sum(inv_atr.values())
                    if total_inv > 0:
                        avg = sum(
                            p.get("return_pct", 0) * inv_atr.get(p["code"], 0) / total_inv
                            for p in picks
                        )
                    else:
                        avg = sum(p.get("return_pct", 0) for p in picks) / len(picks)
                else:
                    avg = sum(p.get("return_pct", 0) for p in picks) / len(picks)

                bench = benchmark_returns.get(date_str, 0)
                if not isinstance(bench, (int, float)):
                    bench = 0
                period_returns.append((date_str, avg, bench))

        # ====== 汇总统计 ======
        print(f"\n{'='*70}")
        print("  >> 总体统计")
        print(f"{'='*70}")

        if total_picks > 0:
            avg_return = total_return / total_picks
            win_rate = total_positive / total_picks * 100

            print(f"  总选股次数:    {total_picks} 次")
            print(f"  盈利次数:      {total_positive} 次")
            print(f"  亏损次数:      {total_picks - total_positive} 次")
            print(f"  胜率:          {win_rate:.1f}%")
            print(f"  平均收益率:    {avg_return:+.2f}%")
            print(f"  最高收益:      {max_gain:+.2f}%  ({best_pick['code']} {best_pick['name']} @ {best_pick['entry_date']})")
            print(f"  最大亏损:      {max_loss:+.2f}%  ({worst_pick['code']} {worst_pick['name']} @ {worst_pick['entry_date']})")

            # 累积收益和最大回撤
            if period_returns and use_weekly_hold:
                print(f"\n  {'='*70}")
                print("  >> 累积收益曲线")
                print(f"  {'='*70}")
                print(f"  {'期数':<6} {'回测日':<12} {'策略收益':<12} {'累积收益':<12} {'上证指数':<12} {'大盘累积':<12}")
                print(f"  {'─'*68}")

                cum_strategy = 1.0
                cum_bench = 1.0
                max_cum = 1.0
                max_drawdown = 0
                peak_date = ""
                trough_date = ""

                for i, (date_str, strat_ret, bench_ret) in enumerate(period_returns, 1):
                    cum_strategy *= (1 + strat_ret / 100)
                    cum_bench *= (1 + bench_ret / 100)
                    cum_strat_pct = (cum_strategy - 1) * 100
                    cum_bench_pct = (cum_bench - 1) * 100

                    # 最大回撤计算
                    if cum_strategy > max_cum:
                        max_cum = cum_strategy
                        peak_date = date_str
                    dd = (max_cum - cum_strategy) / max_cum * 100
                    if dd > max_drawdown:
                        max_drawdown = dd
                        trough_date = date_str

                    marker = "*" if strat_ret > bench_ret else ""
                    print(f"  {i:<6} {date_str:<12} {strat_ret:>+10.2f}% {cum_strat_pct:>+10.2f}% "
                          f"{bench_ret:>+10.2f}% {cum_bench_pct:>+10.2f}% {marker}")

                print(f"  {'─'*68}")
                final_cum = (cum_strategy - 1) * 100
                final_bench_cum = (cum_bench - 1) * 100
                excess_cum = final_cum - final_bench_cum
                print(f"  {'合计':<6} {'':<12} {'':<12} {final_cum:>+10.2f}% {'':<12} {final_bench_cum:>+10.2f}%")

                print(f"\n  {'='*70}")
                print("  >> 风险指标")
                print(f"  {'='*70}")
                print(f"  策略累积收益:   {final_cum:+.2f}%")
                print(f"  上证累积收益:   {final_bench_cum:+.2f}%")
                print(f"  超额累积收益:   {excess_cum:+.2f}%")
                print(f"  最大回撤:       {max_drawdown:.2f}% (峰值: {peak_date}, 谷值: {trough_date})")

                # === Phase 4: 全面风险指标体系 ===
                if len(period_returns) > 1:
                    rets = np.array([r[1] for r in period_returns])
                    bench_rets = np.array([r[2] for r in period_returns])
                    avg_weekly = np.mean(rets)
                    std_weekly = np.std(rets, ddof=1)
                    rf_weekly = 2.5 / 52  # 无风险利率年化2.5%

                    # 夏普比率
                    if std_weekly > 0:
                        sharpe = (avg_weekly * 52 - 2.5) / (std_weekly * np.sqrt(52))
                        print(f"  夏普比率(近似): {sharpe:.2f}")
                    print(f"  收益波动率(周): {std_weekly:.2f}%")

                    # === Sortino 比率 (仅用下行波动率) ===
                    downside = rets[rets < 0]
                    if len(downside) > 1:
                        std_downside = np.std(downside, ddof=1)
                        if std_downside > 0:
                            sortino = (avg_weekly * 52 - 2.5) / (std_downside * np.sqrt(52))
                            print(f"  Sortino比率:     {sortino:.2f}")
                    else:
                        print(f"  Sortino比率:     N/A (下行周期不足)")

                    # === Calmar 比率 (年化收益/最大回撤) ===
                    if max_drawdown > 0:
                        calmar = (avg_weekly * 52) / max_drawdown
                        print(f"  Calmar比率:      {calmar:.2f}")

                    # === Alpha / Beta / 信息比率 (vs 上证指数) ===
                    if len(bench_rets) > 1 and np.std(bench_rets, ddof=1) > 0:
                        cov = np.cov(rets, bench_rets, ddof=1)[0, 1]
                        bench_var = np.var(bench_rets, ddof=1)
                        beta = cov / bench_var
                        alpha = (avg_weekly - rf_weekly) - beta * (np.mean(bench_rets) - rf_weekly)
                        print(f"  Alpha (周):      {alpha:+.4f}%")
                        print(f"  Beta:            {beta:.2f}")
                        # 信息比率
                        tracking_error = np.std(rets - bench_rets, ddof=1)
                        if tracking_error > 0:
                            info_ratio = (avg_weekly - np.mean(bench_rets)) * 52 / (tracking_error * np.sqrt(52))
                            print(f"  跟踪误差(周):    {tracking_error:.2f}%")
                            print(f"  信息比率:        {info_ratio:.2f}")

                    # === 盈亏比 (Profit/Loss Ratio) ===
                    wins = rets[rets > 0]
                    losses = np.abs(rets[rets < 0])
                    if len(wins) > 0 and len(losses) > 0:
                        avg_win = np.mean(wins)
                        avg_loss = np.mean(losses)
                        pl_ratio = avg_win / avg_loss if avg_loss > 0 else 0
                        print(f"  盈亏比 (P/L):    {pl_ratio:.2f} ({avg_win:+.2f}% / -{avg_loss:.2f}%)")

                    # === 跑赢大盘占比 ===
                    beat_count = sum(1 for r, b in zip(rets, bench_rets) if r > b)
                    beat_pct = beat_count / len(rets) * 100
                    print(f"  跑赢大盘期数:    {beat_count}/{len(rets)} ({beat_pct:.0f}%)")

            # 各期对比
            print(f"\n  {'='*70}")
            print("  >> 各期策略 vs 大盘对比")
            print(f"  {'='*70}")
            print(f"  {'回测日':<12} {'选股数':<8} {'策略平均':<12} {'上证指数':<12} {'超额收益':<12}")
            print(f"  {'─'*56}")

            total_strategy = 0
            total_bench = 0
            count = 0

            for date_str in sorted(all_results.keys()):
                picks = all_results[date_str]["filtered"]
                if not picks:
                    continue
                avg = sum(p.get("return_pct", 0) for p in picks) / len(picks)
                bench = benchmark_returns.get(date_str)

                total_strategy += avg
                total_bench += bench if isinstance(bench, (int, float)) else 0
                count += 1

                if isinstance(bench, (int, float)):
                    excess = avg - bench
                    excess_str = f"{excess:+.2f}%"
                    excess_marker = "[+]" if excess > 0 else "[-]"
                else:
                    excess_str = "N/A"
                    excess_marker = ""

                bench_str = f"{bench:>+10.2f}%" if isinstance(bench, (int, float)) else f"{'N/A':>10} "
                print(f"  {date_str:<12} {len(picks):<8} {avg:>+10.2f}% "
                      f"{bench_str} "
                      f"{excess_str:>10} {excess_marker}")

            if count > 0:
                print(f"  {'─'*56}")
                avg_strategy = total_strategy / count
                avg_bench = total_bench / count
                avg_excess = avg_strategy - avg_bench
                print(f"  {'平均':<12} {'':<8} {avg_strategy:>+10.2f}% {avg_bench:>+10.2f}% {avg_excess:>+10.2f}%")

            # === Phase 4: 因子 IC 分析 ===
            self._print_factor_ic(all_results)

            # === Phase 4: 多维度策略评价 ===
            print(f"\n{'='*70}")
            print("  >> 策略评价")
            print(f"{'='*70}")

            grade_scores = []

            # 1. 绝对收益评分
            if use_weekly_hold and period_returns:
                if final_cum > 15:
                    grade_scores.append(("绝对收益", 10, f"{final_cum:+.1f}%"))
                elif final_cum > 5:
                    grade_scores.append(("绝对收益", 7, f"{final_cum:+.1f}%"))
                elif final_cum > 0:
                    grade_scores.append(("绝对收益", 5, f"{final_cum:+.1f}%"))
                elif final_cum > -5:
                    grade_scores.append(("绝对收益", 3, f"{final_cum:+.1f}%"))
                else:
                    grade_scores.append(("绝对收益", 1, f"{final_cum:+.1f}%"))

                # 2. 超额收益评分
                if excess_cum > 15:
                    grade_scores.append(("超额收益", 10, f"{excess_cum:+.1f}%"))
                elif excess_cum > 5:
                    grade_scores.append(("超额收益", 7, f"{excess_cum:+.1f}%"))
                elif excess_cum > 0:
                    grade_scores.append(("超额收益", 5, f"{excess_cum:+.1f}%"))
                else:
                    grade_scores.append(("超额收益", 2, f"{excess_cum:+.1f}%"))

                # 3. 夏普/Sortino 评分
                if 'sortino' in dir() and sortino > 1.5:
                    grade_scores.append(("风险调整", 10, f"Sortino {sortino:.1f}"))
                elif 'sortino' in dir() and sortino > 0.5:
                    grade_scores.append(("风险调整", 7, f"Sortino {sortino:.1f}"))
                elif 'sharpe' in dir() and sharpe > 1:
                    grade_scores.append(("风险调整", 6, f"Sharpe {sharpe:.1f}"))
                elif 'sharpe' in dir() and sharpe > 0:
                    grade_scores.append(("风险调整", 4, f"Sharpe {sharpe:.1f}"))
                else:
                    grade_scores.append(("风险调整", 2, "Sharpe<0"))
            else:
                if avg_return > 5:
                    grade_scores.append(("平均收益", 8, f"{avg_return:+.1f}%"))
                elif avg_return > 0:
                    grade_scores.append(("平均收益", 5, f"{avg_return:+.1f}%"))
                else:
                    grade_scores.append(("平均收益", 2, f"{avg_return:+.1f}%"))

            # 4. 胜率评分
            if win_rate >= 55:
                grade_scores.append(("胜率", 8, f"{win_rate:.0f}%"))
            elif win_rate >= 50:
                grade_scores.append(("胜率", 6, f"{win_rate:.0f}%"))
            elif win_rate >= 45:
                grade_scores.append(("胜率", 4, f"{win_rate:.0f}%"))
            else:
                grade_scores.append(("胜率", 2, f"{win_rate:.0f}%"))

            # 5. 回撤控制评分
            if use_weekly_hold and period_returns:
                if max_drawdown < 5:
                    grade_scores.append(("回撤控制", 10, f"{max_drawdown:.1f}%"))
                elif max_drawdown < 10:
                    grade_scores.append(("回撤控制", 7, f"{max_drawdown:.1f}%"))
                elif max_drawdown < 15:
                    grade_scores.append(("回撤控制", 5, f"{max_drawdown:.1f}%"))
                elif max_drawdown < 20:
                    grade_scores.append(("回撤控制", 3, f"{max_drawdown:.1f}%"))
                else:
                    grade_scores.append(("回撤控制", 1, f"{max_drawdown:.1f}%"))

            # 综合评级
            total_score = sum(s[1] for s in grade_scores)
            max_score = sum(10 for _ in grade_scores)
            grade = "A" if total_score / max_score > 0.8 else "B" if total_score / max_score > 0.65 else "C" if total_score / max_score > 0.5 else "D" if total_score / max_score > 0.35 else "E"

            for dim, score, detail in grade_scores:
                bar = "#" * score + "-" * (10 - score)
                print(f"  [{bar}] {dim}: {detail} ({score}/10)")
            print(f"\n  综合评级: [{grade}] ({total_score}/{max_score})")

            # 优化建议
            print(f"\n  [*] 优化建议:")
            if win_rate < 50:
                print("  - 胜率偏低，可考虑收紧买入条件（如只选'强烈关注'）")
            if use_weekly_hold and period_returns and max_drawdown > 15:
                print(f"  - 最大回撤达 {max_drawdown:.1f}%，建议控制仓位或增加止盈")
            if max_loss < -15:
                print(f"  - 最大单股亏损达 {max_loss:.1f}%，建议严格执行止损")
            if period_returns and final_cum < final_bench_cum:
                print("  - 策略跑输大盘，资金面因子在回测中被忽略，实盘可能不同")
            if period_returns and final_cum > 0:
                print("  - 策略整体正收益，权重配置基本合理")
            if 'beta' in dir() and beta < 0.5:
                print(f"  - Beta={beta:.1f}，策略与大盘关联度低，适合分散配置")
            elif 'beta' in dir() and beta > 1.2:
                print(f"  - Beta={beta:.1f}，策略杠杆效应明显，下行风险较大")

        else:
            print("  回测期内未选出符合条件的股票")

        print(f"\n{'='*70}")

    def _save_report(self, all_results, benchmark_returns, backtest_dates, eval_ts,
                     use_weekly_hold=True, stop_loss_stats=None):
        """保存回测报告为 Markdown 文件（含累积收益统计）"""
        output_dir = "reports/output"
        os.makedirs(output_dir, exist_ok=True)

        date_str = backtest_dates[0].strftime("%Y%m%d") if backtest_dates else "unknown"
        hold_str = "逐周轮动" if use_weekly_hold else "持有至期末"
        filepath = os.path.join(output_dir, f"backtest_{date_str}.md")

        period_label = f"{backtest_dates[0].strftime('%Y-%m-%d')} ~ {eval_ts.strftime('%Y-%m-%d')}"
        sl_mode = self.config.get("risk_management", {}).get("stop_loss_method", "support_resistance")

        lines = [
            f"# 策略回测报告 - {period_label}",
            "",
            f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"> 策略: 多因子选股 v2 (动态权重 + 一票否决 + 信号冲突 + 股权质押避雷)",
            f"> 回测模式: {hold_str}",
            f"> 交易成本: 佣金万{self.commission_rate*10000:.1f} + 印花税千{self.stamp_tax_rate*10:.1f}",
            f"> 止损模式: {sl_mode}",
            f"> 评估截止: {eval_ts.strftime('%Y-%m-%d')}",
            f"> 数据源: tushare + akshare",
            f"> **P0-1修复**: 每期独立获取估值数据，消除未来数据泄漏",
            "",
            "## 回测设置",
            "",
            f"- **回测周期**: {period_label}",
            f"- **回测期数**: {len(backtest_dates)} 期",
            f"- **回测日期**: {', '.join(d.strftime('%Y-%m-%d') for d in backtest_dates)}",
            f"- **选股条件**: 强烈关注 / 关注",
            f"- **交易成本**: 佣金{self.commission_rate*10000:.2f}‰(买卖双向) + 印花税{self.stamp_tax_rate*100:.1f}%(仅卖出)",
            f"- **止损模式**: {sl_mode}",
            "",
            "## 详细结果",
            "",
        ]

        total_picks = 0
        total_positive = 0
        total_return = 0
        max_gain = -999
        max_loss = 999
        best_pick = None
        worst_pick = None
        period_returns = []

        for date_str in sorted(all_results.keys()):
            picks = all_results[date_str]["filtered"]
            if not picks:
                continue

            bench_ret = benchmark_returns.get(date_str, "N/A")
            next_date = all_results[date_str].get("next_date", eval_ts.strftime("%Y-%m-%d"))
            lines.append(f"### {date_str} -> {next_date}")
            lines.append("")
            if isinstance(bench_ret, (int, float)):
                lines.append(f"上证指数同期: {bench_ret:+.2f}%")
            lines.append("")
            lines.append("| 排名 | 代码 | 名称 | 行业 | 评分 | 买入价 | 卖出价 | 收益率 |")
            lines.append("|------|------|------|------|------|--------|--------|--------|")

            for i, pick in enumerate(picks, 1):
                ret = pick.get("return_pct", 0)
                entry = pick.get("entry_price", 0)
                exit_p = pick.get("exit_price", 0)

                lines.append(
                    f"| {i} | {pick['code']} | {pick['name']} | {pick.get('sector', '')} | "
                    f"{pick['total_score']} | {entry:.2f} | {exit_p:.2f} | {ret:+.2f}% |"
                )

                total_picks += 1
                if ret > 0:
                    total_positive += 1
                total_return += ret

                if ret > max_gain:
                    max_gain = ret
                    best_pick = pick
                if ret < max_loss:
                    max_loss = ret
                    worst_pick = pick

            if picks:
                avg = sum(p.get("return_pct", 0) for p in picks) / len(picks)
                bench = benchmark_returns.get(date_str, 0)
                if not isinstance(bench, (int, float)):
                    bench = 0
                period_returns.append((date_str, avg, bench))

            lines.append("")

        # 汇总
        if total_picks > 0:
            avg_return = total_return / total_picks
            win_rate = total_positive / total_picks * 100

            lines.extend([
                "## 汇总统计",
                "",
                f"| 指标 | 数值 |",
                f"|------|------|",
                f"| 总选股次数 | {total_picks} |",
                f"| 盈利次数 | {total_positive} |",
                f"| 亏损次数 | {total_picks - total_positive} |",
                f"| 胜率 | {win_rate:.1f}% |",
                f"| 平均收益率 | {avg_return:+.2f}% |",
                f"| 最高收益 | {max_gain:+.2f}% ({best_pick['code']} {best_pick['name']}) |",
                f"| 最大亏损 | {max_loss:+.2f}% ({worst_pick['code']} {worst_pick['name']}) |",
                "",
            ])

            # 累积收益统计
            if use_weekly_hold and period_returns:
                lines.extend([
                    "## 累积收益曲线",
                    "",
                    "| 期数 | 回测日 | 策略收益 | 累积收益 | 上证指数 | 大盘累积 |",
                    "|------|--------|----------|----------|----------|----------|",
                ])

                cum_strategy = 1.0
                cum_bench = 1.0
                max_cum = 1.0
                max_drawdown = 0
                peak_date = ""
                trough_date = ""

                for i, (date_str, strat_ret, bench_ret) in enumerate(period_returns, 1):
                    cum_strategy *= (1 + strat_ret / 100)
                    cum_bench *= (1 + bench_ret / 100)
                    cum_strat_pct = (cum_strategy - 1) * 100
                    cum_bench_pct = (cum_bench - 1) * 100

                    if cum_strategy > max_cum:
                        max_cum = cum_strategy
                        peak_date = date_str
                    dd = (max_cum - cum_strategy) / max_cum * 100
                    if dd > max_drawdown:
                        max_drawdown = dd
                        trough_date = date_str

                    lines.append(
                        f"| {i} | {date_str} | {strat_ret:+.2f}% | {cum_strat_pct:+.2f}% | "
                        f"{bench_ret:+.2f}% | {cum_bench_pct:+.2f}% |"
                    )

                final_cum = (cum_strategy - 1) * 100
                final_bench_cum = (cum_bench - 1) * 100
                excess_cum = final_cum - final_bench_cum

                lines.extend([
                    "",
                    "## 风险指标",
                    "",
                    f"| 指标 | 数值 |",
                    f"|------|------|",
                    f"| 策略累积收益 | {final_cum:+.2f}% |",
                    f"| 上证累积收益 | {final_bench_cum:+.2f}% |",
                    f"| 超额累积收益 | {excess_cum:+.2f}% |",
                    f"| 最大回撤 | {max_drawdown:.2f}% |",
                ])

                if len(period_returns) > 1:
                    rets = np.array([r[1] for r in period_returns])
                    bench_rets = np.array([r[2] for r in period_returns])
                    avg_weekly = np.mean(rets)
                    std_weekly = np.std(rets, ddof=1)

                    lines.append(f"| 收益波动率(周) | {std_weekly:.2f}% |")

                    # 夏普比率
                    if std_weekly > 0:
                        sharpe = (avg_weekly * 52 - 2.5) / (std_weekly * np.sqrt(52))
                        lines.append(f"| 夏普比率 | {sharpe:.2f} |")

                    # Sortino
                    downside = rets[rets < 0]
                    if len(downside) > 1:
                        std_downside = np.std(downside, ddof=1)
                        if std_downside > 0:
                            sortino = (avg_weekly * 52 - 2.5) / (std_downside * np.sqrt(52))
                            lines.append(f"| Sortino比率 | {sortino:.2f} |")

                    # Calmar
                    if max_drawdown > 0:
                        calmar = (avg_weekly * 52) / max_drawdown
                        lines.append(f"| Calmar比率 | {calmar:.2f} |")

                    # Alpha / Beta / 信息比率
                    if np.std(bench_rets, ddof=1) > 0:
                        cov = np.cov(rets, bench_rets, ddof=1)[0, 1]
                        bench_var = np.var(bench_rets, ddof=1)
                        beta = cov / bench_var
                        alpha_w = (avg_weekly - 2.5/52) - beta * (np.mean(bench_rets) - 2.5/52)
                        lines.append(f"| Alpha (周) | {alpha_w:+.4f}% |")
                        lines.append(f"| Beta | {beta:.2f} |")
                        tracking_error = np.std(rets - bench_rets, ddof=1)
                        if tracking_error > 0:
                            info_ratio = (avg_weekly - np.mean(bench_rets)) * 52 / (tracking_error * np.sqrt(52))
                            lines.append(f"| 跟踪误差(周) | {tracking_error:.2f}% |")
                            lines.append(f"| 信息比率 | {info_ratio:.2f} |")

                    # 盈亏比
                    wins = rets[rets > 0]
                    losses = np.abs(rets[rets < 0])
                    if len(wins) > 0 and len(losses) > 0:
                        avg_win = np.mean(wins)
                        avg_loss = np.mean(losses)
                        pl_ratio = avg_win / avg_loss if avg_loss > 0 else 0
                        lines.append(f"| 盈亏比 (P/L) | {pl_ratio:.2f} |")

                lines.append("")

            lines.extend([
                "## 各期对比",
                "",
                "| 回测日 | 选股数 | 策略平均 | 上证指数 | 超额收益 |",
                "|--------|--------|----------|----------|----------|",
            ])

            for date_str in sorted(all_results.keys()):
                picks = all_results[date_str]["filtered"]
                if picks:
                    avg = sum(p.get("return_pct", 0) for p in picks) / len(picks)
                    bench = benchmark_returns.get(date_str, "N/A")
                    if isinstance(bench, (int, float)):
                        excess = avg - bench
                        lines.append(f"| {date_str} | {len(picks)} | {avg:+.2f}% | {bench:+.2f}% | {excess:+.2f}% |")
                    else:
                        lines.append(f"| {date_str} | {len(picks)} | {avg:+.2f}% | N/A | N/A |")

            lines.append("")

        lines.extend([
            "---",
            "",
            "**免责声明**: 以上回测基于历史数据，不代表未来表现。资金面数据在历史回测中未纳入，股权质押数据使用最新数据近似，实际结果可能有所不同。",
        ])

        content = "\n".join(lines)
        with open(filepath, "w", encoding="utf-8-sig") as f:
            f.write(content)

        print(f"\n  >> 回测报告已保存: {filepath}")

    def _save_avoid_list(self, avoided_stocks, date_str):
        """保存股权质押避雷清单"""
        output_dir = "reports/output"
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, f"avoid_list_{date_str}.md")

        lines = [
            f"# 股权质押避雷清单 - {date_str}",
            "",
            f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"> 共 {len(avoided_stocks)} 只高质押股票",
            "",
            "| 代码 | 名称 | 质押比例 | 风险等级 |",
            "|------|------|----------|----------|",
        ]

        for a in avoided_stocks:
            lines.append(
                f"| {a.get('code', '')} | {a.get('name', '')} | "
                f"{a.get('ratio', 0):.1f}% | {a.get('level', '')} |"
            )

        content = "\n".join(lines)
        with open(filepath, "w", encoding="utf-8-sig") as f:
            f.write(content)
        print(f"  >> 避雷清单已保存: {filepath}")


def run_demo_backtest():
    """使用模拟数据快速运行回测"""
    print("=" * 70)
    print("  [演示模式] 使用模拟数据验证回测逻辑")
    print("=" * 70)

    from utils.mock_data import generate_mock_spot, generate_mock_hist

    with open("config/settings.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    print("\n生成模拟数据...")
    np.random.seed(42)

    n_stocks = 200
    spot = generate_mock_spot(n_stocks)

    stock_hists = {}
    trends = ["up", "down", "sideways"]
    for i in range(n_stocks):
        code = spot.iloc[i]["代码"]
        trend = np.random.choice(trends, p=[0.45, 0.30, 0.25])
        hist = generate_mock_hist(days=300, trend=trend)
        stock_hists[code] = hist

    print(f"  生成 {n_stocks} 只模拟股票")

    screener = FundamentalScreener(spot, config)
    candidates = screener.screen()
    print(f"  通过基本面筛选: {len(candidates)} 只")

    backtest_dates = pd.to_datetime(["2026-05-06", "2026-05-13", "2026-05-20", "2026-05-27"])
    eval_end = datetime(2026, 6, 6)
    engine = SignalEngineV2(config)

    all_results = {}
    for bt_date in backtest_dates:
        date_str = bt_date.strftime("%Y-%m-%d")
        print(f"\n  --- 回测日: {date_str} ---")

        daily_results = []
        for _, row in candidates.iterrows():
            code = str(row["代码"])
            if code not in stock_hists:
                continue

            hist = stock_hists[code]
            hist_bt = hist[hist["日期"] <= bt_date].copy()
            if len(hist_bt) < 60:
                continue

            f_score = screener.score(row, financial_data=None)  # demo: 无财务数据
            tech = TechnicalAnalyzer(hist_bt, config.get("technical")).score()
            momentum = engine.calculate_momentum(hist_bt)
            capital = engine.calculate_capital_flow(code, row,
                moneyflow_df=None, top_list_df=None, top_inst_df=None)
            result = engine.combine(f_score, tech, momentum, capital)
            result["code"] = code
            result["name"] = str(row.get("名称", ""))
            result["sector"] = str(row.get("所属行业", ""))
            result["entry_price"] = float(hist_bt.iloc[-1]["收盘"])
            result["entry_date"] = date_str

            daily_results.append(result)

        daily_results.sort(key=lambda x: x["total_score"], reverse=True)
        filtered = [r for r in daily_results if r["advice"] in ("强烈关注", "关注")]

        for pick in filtered[:10]:
            code = pick["code"]
            hist = stock_hists[code]
            exit_rows = hist[hist["日期"] <= eval_end]
            if not exit_rows.empty:
                entry = pick["entry_price"]
                exit_p = float(exit_rows.iloc[-1]["收盘"])
                pick["exit_price"] = round(exit_p, 2)
                pick["return_pct"] = round((exit_p - entry) / entry * 100, 2)

        all_results[date_str] = {"all": daily_results, "filtered": filtered[:10]}
        print(f"  分析: {len(daily_results)} 只, 符合条件: {len(filtered)} 只")

    # 打印报告
    print("\n" + "=" * 70)
    print("  [演示模式] 回测结果")
    print("=" * 70)

    total_picks = 0
    total_positive = 0
    total_return = 0

    for date_str in sorted(all_results.keys()):
        picks = all_results[date_str]["filtered"]
        if not picks:
            continue

        print(f"\n【{date_str}】")
        for i, pick in enumerate(picks, 1):
            ret = pick.get("return_pct", 0)
            print(f"  {i}. {pick['code']} {pick['name'][:8]} 评分:{pick['total_score']} 收益:{ret:+.2f}%")
            total_picks += 1
            if ret > 0:
                total_positive += 1
            total_return += ret

    if total_picks > 0:
        print(f"\n{'='*70}")
        print(f"  演示模式统计")
        print(f"  总选股: {total_picks} 次")
        print(f"  胜率: {total_positive/total_picks*100:.1f}%")
        print(f"  平均收益: {total_return/total_picks:+.2f}%")
        print(f"{'='*70}")
        print("\n  [*] 演示模式使用随机生成的模拟数据，结果仅供逻辑验证")
        print("  要获取真实回测结果，请运行: python backtest.py")


def main():
    parser = argparse.ArgumentParser(description="策略回测工具")
    parser.add_argument("--month", type=str, default=None,
                        help="回测月份, 如 2026-05 (默认: 上个月)")
    parser.add_argument("--period", type=str, default=None,
                        help="回测周期, 如 3m=三个月, 1m=一个月 (与--month互斥)")
    parser.add_argument("--end", type=str, default=None,
                        help="评估结束日期, 如 2026-06-06 (默认: 今天)")
    parser.add_argument("--top", type=int, default=10,
                        help="每次选股展示前N只 (默认: 10)")
    parser.add_argument("--hold-mode", type=str, default="weekly",
                        choices=["weekly", "hold"],
                        help="收益计算模式: weekly=逐周轮动(默认), hold=持有至期末")
    parser.add_argument("--stop-loss-mode", type=str, default=None,
                        choices=["fixed", "support_resistance", "none"],
                        help="止损模式: fixed=固定比例(默认-8%%), support_resistance=支撑位, none=不止损")
    parser.add_argument("--demo", action="store_true",
                        help="使用模拟数据快速验证回测逻辑")
    args = parser.parse_args()

    if args.demo:
        run_demo_backtest()
        return

    # 评估截止日期
    if args.end:
        eval_end = datetime.strptime(args.end, "%Y-%m-%d")
    else:
        eval_end = datetime.now()

    # 创建回测器
    bt = TushareBacktester()

    # 确定回测日期
    if args.period:
        # 按周期回测
        period_str = args.period.lower()
        if period_str.endswith("m"):
            months = int(period_str[:-1])
        elif period_str.endswith("month") or period_str.endswith("months"):
            months = int(period_str.rstrip("month").rstrip("months"))
        else:
            months = int(period_str)

        # 从今天往前推 N 个月
        end_date = eval_end.strftime("%Y-%m-%d")
        start = eval_end - timedelta(days=months * 30)
        # 找到三个月前的月份的第一天
        if months >= eval_end.month:
            start_year = eval_end.year - 1 - (months - eval_end.month) // 12
            start_month = 12 - ((months - eval_end.month) % 12)
        else:
            start_year = eval_end.year
            start_month = eval_end.month - months

        start_date = f"{start_year}-{start_month:02d}-01"

        print(f"\n{'='*70}")
        print(f"  回测周期: {months}个月")
        print(f"  日期范围: {start_date} ~ {end_date}")
        print(f"  评估截止: {eval_end.strftime('%Y-%m-%d')}")
        print(f"{'='*70}")

        weekly_dates = bt.get_weekly_dates_range(start_date, end_date)

    elif args.month:
        # 按月回测（原有逻辑）
        year, month = map(int, args.month.split("-"))
        weekly_dates = bt.get_weekly_dates(year, month)
        print(f"\n{'='*70}")
        print(f"  回测月份: {year}年{month}月")
        print(f"  回测日期: {[d.strftime('%Y-%m-%d') for d in weekly_dates]}")
        print(f"  评估截止: {eval_end.strftime('%Y-%m-%d')}")
        print(f"{'='*70}")

    else:
        # 默认回测上个月
        today = datetime.now()
        if today.month == 1:
            year, month = today.year - 1, 12
        else:
            year, month = today.year, today.month - 1
        weekly_dates = bt.get_weekly_dates(year, month)
        print(f"\n{'='*70}")
        print(f"  回测月份: {year}年{month}月")
        print(f"  回测日期: {[d.strftime('%Y-%m-%d') for d in weekly_dates]}")
        print(f"  评估截止: {eval_end.strftime('%Y-%m-%d')}")
        print(f"{'='*70}")

    if not weekly_dates:
        print("错误: 无法确定回测日期")
        return

    print(f"  共 {len(weekly_dates)} 个回测日")

    use_weekly = args.hold_mode == "weekly"
    sl_mode = args.stop_loss_mode

    # 运行回测
    try:
        bt.run_backtest(weekly_dates, eval_end, top_n=args.top,
                        use_weekly_hold=use_weekly, stop_loss_mode=sl_mode)
    except KeyboardInterrupt:
        print("\n\n用户中断回测")
    except Exception as e:
        print(f"\n回测出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
