import akshare as ak
import pandas as pd
import numpy as np
from typing import Optional
from datetime import datetime, timedelta
import time
import os
import warnings
warnings.filterwarnings("ignore")

class DataFetcher:
    """
    多数据源封装：akshare(主) -> tushare(备) -> mock(兜底)
    自动降级，任一数据源可用即可运行
    """

    def __init__(self, cache_dir="data", data_source="auto", tushare_token=None):
        self.cache_dir = cache_dir
        self.data_source = data_source  # "auto" | "akshare" | "tushare" | "mock"
        self._tushare_token = tushare_token or self._load_tushare_token()
        self._tushare_pro = None
        self._northbound_cache = None
        self._margin_cache = None
        self._financial_cache = {}
        self._last_trade_date = None
        # 资金面数据缓存
        self._moneyflow_cache = None
        self._top_list_cache = None
        self._top_inst_cache = None
        self._market_capital_cache = None
        # 股权质押数据缓存
        self._pledge_cache = None
        self._suspend_cache = None
        self._limit_list_cache = None
        self._holder_cache = {}
        # 新闻/情绪数据缓存
        self._news_cache = {}
        self._comment_cache = {}
        self._rating_cache = {}
        # 每日财经简报缓存
        self._daily_briefing_cache = None
        # A/H 溢价与可转债数据缓存
        self._ah_premium_cache = None
        self._cb_data_cache = None
        # 筹码分布数据缓存
        self._chip_cache = None

    def _load_tushare_token(self):
        """从本地文件加载 tushare token"""
        token_path = os.path.join(os.path.dirname(__file__), "..", "config", ".tushare_token")
        token_path = os.path.abspath(token_path)
        if os.path.exists(token_path):
            with open(token_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        return None

    def _get_tushare_pro(self):
        """延迟初始化 tushare pro"""
        if self._tushare_pro is None and self._tushare_token:
            try:
                import tushare as ts
                self._tushare_pro = ts.pro_api(self._tushare_token)
            except Exception as e:
                print(f"[WARN] tushare 初始化失败: {e}")
        return self._tushare_pro

    def _get_last_trade_date(self):
        """获取最近交易日（通过 tushare daily 接口验证）"""
        if self._last_trade_date:
            return self._last_trade_date
        pro = self._get_tushare_pro()
        if pro:
            try:
                # 从最近 10 天倒推，找到有数据的交易日
                for i in range(10):
                    d = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
                    df = pro.daily(trade_date=d, limit=1)
                    if df is not None and not df.empty:
                        self._last_trade_date = d
                        return d
            except Exception:
                pass
        # fallback: 昨天
        self._last_trade_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        return self._last_trade_date

    # ==================== 股票列表 ====================

    def get_stock_list(self, use_mock: bool = False):
        """获取 A 股所有股票列表，含完整字段"""
        if use_mock or self.data_source == "mock":
            return self._get_mock_stock_list()

        if self.data_source in ("auto", "akshare"):
            df = self._get_stock_list_akshare()
            if not df.empty:
                print("     [数据源] akshare")
                return df
            print("     [WARN] akshare 获取失败，尝试 tushare...")

        if self.data_source in ("auto", "tushare"):
            df = self._get_stock_list_tushare()
            if not df.empty:
                print("     [数据源] tushare")
                return df
            print("     [WARN] tushare 获取失败")

        print("     [WARN] 所有数据源均失败，使用模拟数据")
        return self._get_mock_stock_list()

    def _get_stock_list_akshare(self):
        """akshare 获取股票列表"""
        try:
            df = ak.stock_zh_a_spot_em()
            keep_cols = [
                "代码", "名称", "所属行业",
                "总市值", "流通市值",
                "市盈率-动态", "市净率", "股息率",
                "涨跌幅", "换手率", "振幅", "量比",
                "最高", "最低", "今开", "昨收", "最新价",
                "成交量", "成交额"
            ]
            cols = [c for c in keep_cols if c in df.columns]
            df = df[cols].copy()
            rename_map = {
                "市盈率-动态": "市盈率",
                "最新价": "收盘价"
            }
            df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
            if "成交量" in df.columns:
                df = df[df["成交量"] > 0]
            if "收盘价" in df.columns:
                df = df[df["收盘价"] > 0]
            return df.reset_index(drop=True)
        except Exception as e:
            return pd.DataFrame()

    def _get_stock_list_tushare(self):
        """tushare 获取股票列表"""
        pro = self._get_tushare_pro()
        if not pro:
            return pd.DataFrame()
        try:
            # 1. 获取基础信息
            basics = pro.stock_basic(exchange="", list_status="L")
            basics = basics[["ts_code", "name", "industry"]].copy()
            basics.rename(columns={"ts_code": "代码", "name": "名称", "industry": "所属行业"}, inplace=True)
            basics["代码"] = basics["代码"].str.replace(r"\.SH|\.SZ", "", regex=True)

            # 2. 获取每日指标 (daily_basic: close/pe_ttm/pb/dv_ttm/total_mv/circ_mv/turnover_rate)
            trade_date = self._get_last_trade_date()
            daily = pro.daily_basic(trade_date=trade_date)
            daily.rename(columns={
                "ts_code": "代码",
                "close": "收盘价",
                "pe_ttm": "市盈率",
                "pb": "市净率",
                "dv_ttm": "股息率",
                "total_mv": "总市值",
                "circ_mv": "流通市值",
                "turnover_rate": "换手率",
                "volume_ratio": "量比",
                "pct_chg": "涨跌幅"
            }, inplace=True)
            daily["代码"] = daily["代码"].str.replace(r"\.SH|\.SZ", "", regex=True)
            # 单位转换：total_mv 是万元 -> 元
            if "总市值" in daily.columns:
                daily["总市值"] = daily["总市值"] * 10000
            if "流通市值" in daily.columns:
                daily["流通市值"] = daily["流通市值"] * 10000
            # 股息率 tushare 是百分比数值(如3.5表示3.5%)，代码统一按百分比处理
            if "股息率" in daily.columns:
                daily["股息率"] = daily["股息率"]  # 保持原样，配置文件中阈值也按此单位

            # 3. 合并
            df = pd.merge(basics, daily, on="代码", how="inner")
            # 清洗：排除停牌/未交易（收盘价<=0 或 换手率为0）
            if "收盘价" in df.columns:
                df = df[df["收盘价"] > 0]
            if "换手率" in df.columns:
                df = df[df["换手率"] > 0]

            # 4. 保留统一列名（daily_basic 无成交量/成交额，用空值占位）
            keep = ["代码", "名称", "所属行业", "总市值", "流通市值",
                    "市盈率", "市净率", "股息率", "涨跌幅", "换手率", "量比", "收盘价"]
            available = [c for c in keep if c in df.columns]
            result = df[available].copy()
            # 补充策略可能需要的字段（用0占位）
            for col in ["成交量", "成交额", "振幅", "最高", "最低", "今开", "昨收", "量比"]:
                if col not in result.columns:
                    result[col] = 0
            return result.reset_index(drop=True)
        except Exception as e:
            return pd.DataFrame()

    def _get_mock_stock_list(self):
        """模拟数据"""
        mock_path = os.path.join(self.cache_dir, "mock_spot.csv")
        if os.path.exists(mock_path):
            return pd.read_csv(mock_path, encoding="utf-8-sig")
        from utils.mock_data import generate_mock_spot
        df = generate_mock_spot(200)
        os.makedirs(self.cache_dir, exist_ok=True)
        df.to_csv(mock_path, index=False, encoding="utf-8-sig")
        return df

    # ==================== 历史 K 线 ====================

    def get_hist_data(self, symbol: str, period: str = "daily", days: int = 300, use_mock: bool = False):
        if use_mock or self.data_source == "mock":
            return self._get_mock_hist(days)

        if self.data_source in ("auto", "akshare"):
            df = self._get_hist_data_akshare(symbol, period, days)
            if not df.empty:
                return df

        if self.data_source in ("auto", "tushare"):
            df = self._get_hist_data_tushare(symbol, days)
            if not df.empty:
                return df

        return self._get_mock_hist(days)

    def _get_hist_data_akshare(self, symbol: str, period: str, days: int):
        if symbol.startswith("sh") or symbol.startswith("sz"):
            symbol = symbol[2:]
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol, period=period,
                start_date=start, end_date=end, adjust="qfq"
            )
            if df is not None and not df.empty:
                df["日期"] = pd.to_datetime(df["日期"])
                df = df.sort_values("日期").reset_index(drop=True)
            return df
        except Exception:
            return pd.DataFrame()

    def _get_hist_data_tushare(self, symbol: str, days: int):
        pro = self._get_tushare_pro()
        if not pro:
            return pd.DataFrame()
        try:
            if symbol.startswith("sh") or symbol.startswith("sz"):
                symbol = symbol[2:]
            suffix = ".SH" if symbol.startswith("6") else ".SZ"
            ts_code = f"{symbol}{suffix}"
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
            df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
            if df is not None and not df.empty:
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
            return df
        except Exception:
            return pd.DataFrame()

    def _get_mock_hist(self, days: int):
        from utils.mock_data import generate_mock_hist
        trend = np.random.choice(["up", "down", "sideways"], p=[0.4, 0.3, 0.3])
        return generate_mock_hist(days=days, trend=trend)

    # ==================== 指数 ====================

    def get_index_hist(self, symbol: str = "000001", days: int = 300):
        if self.data_source in ("auto", "akshare"):
            df = self._get_hist_data_akshare(symbol, "daily", days)
            if not df.empty:
                return df
        if self.data_source in ("auto", "tushare"):
            pro = self._get_tushare_pro()
            if pro:
                try:
                    ts_code = "000001.SH" if symbol == "000001" else f"{symbol}.SH"
                    end = datetime.now().strftime("%Y%m%d")
                    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
                    df = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
                    if df is not None and not df.empty:
                        df.rename(columns={
                            "trade_date": "日期",
                            "open": "开盘",
                            "high": "最高",
                            "low": "最低",
                            "close": "收盘",
                            "vol": "成交量",
                            "amount": "成交额",
                            "pct_chg": "涨跌幅"
                        }, inplace=True)
                        df["日期"] = pd.to_datetime(df["日期"])
                        df = df.sort_values("日期").reset_index(drop=True)
                    return df
                except Exception:
                    pass
        return pd.DataFrame()

    # ==================== 财务数据 ====================

    def get_financial_indicators(self, symbol: str) -> dict:
        if symbol in self._financial_cache:
            return self._financial_cache[symbol]

        result = {}
        try:
            if symbol.startswith("sh") or symbol.startswith("sz"):
                symbol = symbol[2:]
            df = ak.stock_financial_analysis_indicator(symbol=symbol)
            if df is not None and not df.empty:
                latest = df.iloc[0]
                result = {
                    "roe": float(latest.get("净资产收益率", 0) or 0),
                    "gross_margin": float(latest.get("销售毛利率", 0) or 0),
                    "net_margin": float(latest.get("销售净利率", 0) or 0),
                    "revenue_growth": float(latest.get("营业收入增长率", 0) or 0),
                    "profit_growth_yoy": float(latest.get("净利润增长率", 0) or 0),
                    "debt_ratio": float(latest.get("资产负债率", 0) or 0),
                }
        except Exception:
            pass

        if not result:
            pro = self._get_tushare_pro()
            if pro:
                try:
                    suffix = ".SH" if symbol.startswith("6") else ".SZ"
                    df = pro.fina_indicator(ts_code=f"{symbol}{suffix}", limit=1)
                    if df is not None and not df.empty:
                        r = df.iloc[0]
                        result = {
                            "roe": float(r.get("roe", 0) or 0),
                            "gross_margin": float(r.get("grossprofit_margin", 0) or 0),
                            "net_margin": float(r.get("netprofit_margin", 0) or 0),
                            "revenue_growth": float(r.get("q_sales_yoy", 0) or 0),
                            "profit_growth_yoy": float(r.get("q_netprofit_yoy", 0) or 0),
                            "debt_ratio": float(r.get("debt_to_assets", 0) or 0),
                        }
                except Exception:
                    pass

        self._financial_cache[symbol] = result
        return result

    # ==================== 北向/融资融券 ====================

    def get_northbound_holding(self) -> pd.DataFrame:
        if self._northbound_cache is not None:
            return self._northbound_cache
        try:
            df = ak.stock_hsgt_stock_em()
            self._northbound_cache = df
            return df
        except Exception:
            return pd.DataFrame()

    def get_margin_data(self) -> pd.DataFrame:
        if self._margin_cache is not None:
            return self._margin_cache
        try:
            df = ak.stock_margin_detail_em()
            self._margin_cache = df
            return df
        except Exception:
            return pd.DataFrame()

    def get_daily_spot(self) -> pd.DataFrame:
        return self.get_stock_list()

    # ==================== 资金面高级数据 (tushare) ====================

    def get_moneyflow_data(self, codes: list, trade_date: str = None) -> pd.DataFrame:
        """获取个股资金流向数据 (moneyflow)"""
        if self._moneyflow_cache is not None:
            return self._moneyflow_cache
        pro = self._get_tushare_pro()
        if not pro:
            return pd.DataFrame()
        try:
            trade_date = trade_date or self._get_last_trade_date()
            # tushare moneyflow 不支持批量代码，需逐个查询或按日期查
            # 策略：先按日期查当日全部，再过滤
            df = pro.moneyflow(trade_date=trade_date)
            if df is not None and not df.empty:
                df["代码"] = df["ts_code"].str.replace(r"\.SH|\.SZ", "", regex=True)
                # 计算关键指标
                # 主力净流入 = 大单 + 特大单买入 - 卖出
                df["主力净流入"] = (
                    df.get("buy_lg_amount", 0) + df.get("buy_elg_amount", 0)
                    - df.get("sell_lg_amount", 0) - df.get("sell_elg_amount", 0)
                )
                # 散户净流出 = 小单买入 - 小单卖出（负值表示散户在卖）
                df["散户净流出"] = df.get("buy_sm_amount", 0) - df.get("sell_sm_amount", 0)
                # 净流入占比（用买入金额合计估算成交额）
                total_turnover = (
                    df.get("buy_sm_amount", 0) + df.get("buy_md_amount", 0)
                    + df.get("buy_lg_amount", 0) + df.get("buy_elg_amount", 0)
                )
                df["净流入占比"] = df["主力净流入"] / total_turnover.replace(0, np.nan)
                # 过滤
                if codes:
                    df = df[df["代码"].isin(codes)]
                self._moneyflow_cache = df
                return df
        except Exception as e:
            print(f"     [WARN] 资金流向获取失败: {e}")
        return pd.DataFrame()

    def get_top_list_data(self, trade_date: str = None) -> pd.DataFrame:
        """获取当日龙虎榜 (top_list)"""
        if self._top_list_cache is not None:
            return self._top_list_cache
        pro = self._get_tushare_pro()
        if not pro:
            return pd.DataFrame()
        try:
            trade_date = trade_date or self._get_last_trade_date()
            df = pro.top_list(trade_date=trade_date)
            if df is not None and not df.empty:
                df["代码"] = df["ts_code"].str.replace(r"\.SH|\.SZ", "", regex=True)
                # 机构席位净买入（l_buy = 机构买入, l_sell = 机构卖出）
                df["机构净买入"] = df.get("l_buy", 0) - df.get("l_sell", 0)
                self._top_list_cache = df
                return df
        except Exception as e:
            print(f"     [WARN] 龙虎榜获取失败: {e}")
        return pd.DataFrame()

    def get_top_inst_data(self, trade_date: str = None) -> pd.DataFrame:
        """获取龙虎榜机构席位明细 (top_inst)"""
        if self._top_inst_cache is not None:
            return self._top_inst_cache
        pro = self._get_tushare_pro()
        if not pro:
            return pd.DataFrame()
        try:
            trade_date = trade_date or self._get_last_trade_date()
            df = pro.top_inst(trade_date=trade_date)
            if df is not None and not df.empty:
                df["代码"] = df["ts_code"].str.replace(r"\.SH|\.SZ", "", regex=True)
                # side=0 买入, side=1 卖出; 聚合每个股票的机构净买入
                inst_buy = df[df.get("side", 1) == 0].groupby("代码")["net_buy"].sum()
                inst_sell = df[df.get("side", 1) == 1].groupby("代码")["net_buy"].sum().abs()
                summary = pd.DataFrame({
                    "机构买入": inst_buy,
                    "机构卖出": inst_sell
                }).fillna(0)
                summary["机构净买入"] = summary["机构买入"] - summary["机构卖出"]
                summary = summary.reset_index()
                self._top_inst_cache = summary
                return summary
        except Exception as e:
            print(f"     [WARN] 龙虎榜机构明细获取失败: {e}")
        return pd.DataFrame()

    # ==================== 停牌/涨跌停/股东人数 (Phase 3) ====================

    def get_suspend_stocks(self) -> set:
        """获取当前停牌的股票代码集合 (pro.suspend_daily)"""
        if self._suspend_cache is not None:
            return self._suspend_cache
        result = set()
        pro = self._get_tushare_pro()
        if pro:
            try:
                df = pro.suspend_daily(suspend_type="S")
                if df is not None and not df.empty:
                    codes = df["ts_code"].str.replace(r"\.SH|\.SZ", "", regex=True)
                    result = set(codes.tolist())
            except Exception:
                pass
        self._suspend_cache = result
        return result

    def get_limit_list_data(self) -> pd.DataFrame:
        """获取当日涨跌停股票 (pro.limit_list)"""
        if self._limit_list_cache is not None:
            return self._limit_list_cache
        result = pd.DataFrame()
        pro = self._get_tushare_pro()
        if pro:
            try:
                trade_date = self._get_last_trade_date()
                # U=涨停 D=跌停
                df = pro.limit_list(trade_date=trade_date, limit_type="D")
                if df is not None and not df.empty:
                    df["代码"] = df["ts_code"].str.replace(r"\.SH|\.SZ", "", regex=True)
                    result = df
                self._limit_list_cache = result
            except Exception:
                pass
        return result

    def get_holder_trend(self, symbol: str) -> dict:
        """获取股东人数变化趋势 (pro.stk_holdernumber)
        返回: {"trend": "concentrate"|"disperse"|"stable", "change_pct": float}
        股东人数下降 = 筹码集中 = 利好
        """
        if symbol in self._holder_cache:
            return self._holder_cache[symbol]
        result = {"trend": "stable", "change_pct": 0}
        pro = self._get_tushare_pro()
        if pro:
            try:
                suffix = ".SH" if symbol.startswith("6") else ".SZ"
                df = pro.stk_holdernumber(ts_code=f"{symbol}{suffix}", limit=3)
                if df is not None and not df.empty and len(df) >= 2:
                    current = float(df.iloc[0]["holder_num"])
                    previous = float(df.iloc[1]["holder_num"])
                    if previous > 0:
                        change_pct = (current - previous) / previous * 100
                        result["change_pct"] = round(change_pct, 2)
                        if change_pct < -5:
                            result["trend"] = "concentrate"
                        elif change_pct > 10:
                            result["trend"] = "disperse"
            except Exception:
                pass
        self._holder_cache[symbol] = result
        return result

    # ==================== 股权质押数据 ====================

    def get_pledge_ratio_data(self) -> pd.DataFrame:
        """
        获取A股股权质押比例数据 (akshare)
        返回: DataFrame[代码, 名称, 质押比例, 质押股数, 质押市值, 笔数]
        """
        if self._pledge_cache is not None:
            return self._pledge_cache

        if self.data_source == "mock":
            return pd.DataFrame()

        try:
            # akshare 接口：获取股票质押比例
            df = ak.stock_gpzy_pledge_ratio_em()
            if df is not None and not df.empty:
                # 统一列名
                rename_map = {
                    "股票代码": "代码",
                    "股票简称": "名称",
                    "质押比例": "质押比例",
                    "质押股数": "质押股数",
                    "质押市值": "质押市值",
                    "笔数": "笔数",
                }
                # 只保留存在的列
                actual_rename = {k: v for k, v in rename_map.items() if k in df.columns}
                df = df.rename(columns=actual_rename)
                # 确保代码列为字符串
                if "代码" in df.columns:
                    df["代码"] = df["代码"].astype(str).str.strip()
                # 确保质押比例为数值
                if "质押比例" in df.columns:
                    df["质押比例"] = pd.to_numeric(df["质押比例"], errors="coerce").fillna(0)
                self._pledge_cache = df
                return df
        except Exception as e:
            print(f"     [WARN] 股权质押数据获取失败: {e}")
        return pd.DataFrame()

    def get_high_pledge_stocks(self, threshold: float = 30.0) -> pd.DataFrame:
        """
        获取高质押比例股票列表（避雷清单）
        threshold: 质押比例阈值（%），默认30%
        返回: DataFrame[代码, 名称, 质押比例]
        """
        df = self.get_pledge_ratio_data()
        if df.empty or "质押比例" not in df.columns:
            return pd.DataFrame()
        high = df[df["质押比例"] >= threshold].copy()
        if not high.empty and "代码" in high.columns:
            high = high.sort_values("质押比例", ascending=False).reset_index(drop=True)
        return high

    # ==================== A/H 溢价数据 ====================

    def get_ah_premium_data(self, use_mock: bool = False) -> pd.DataFrame:
        """
        获取 A/H 股溢价数据（akshare 主，mock 兜底）
        返回: DataFrame[代码, 名称, A股价格, H股价格, AH溢价率]
        """
        if self._ah_premium_cache is not None:
            return self._ah_premium_cache

        if use_mock or self.data_source == "mock":
            return self._get_mock_ah_premium_data()

        try:
            df = ak.stock_zh_ah_spot_em()
            if df is not None and not df.empty:
                # 列名映射（不同 akshare 版本字段名可能不同）
                rename_map = {}
                code_candidates = ["代码", "股票代码", "code", "symbol"]
                name_candidates = ["名称", "股票简称", "name"]
                a_price_candidates = ["A股最新价", "A股价格", "a_price", "最新价"]
                h_price_candidates = ["H股最新价", "H股价格", "h_price"]
                premium_candidates = ["溢价率", "AH溢价率", "A股/H股", "溢价", "premium"]

                for c in code_candidates:
                    if c in df.columns and "代码" not in df.columns and "代码" not in rename_map.values():
                        rename_map[c] = "代码"
                        break
                for c in name_candidates:
                    if c in df.columns and "名称" not in df.columns and "名称" not in rename_map.values():
                        rename_map[c] = "名称"
                        break
                for c in a_price_candidates:
                    if c in df.columns and "A股价格" not in df.columns and "A股价格" not in rename_map.values():
                        rename_map[c] = "A股价格"
                        break
                for c in h_price_candidates:
                    if c in df.columns and "H股价格" not in df.columns and "H股价格" not in rename_map.values():
                        rename_map[c] = "H股价格"
                        break
                for c in premium_candidates:
                    if c in df.columns and "AH溢价率" not in df.columns and "AH溢价率" not in rename_map.values():
                        rename_map[c] = "AH溢价率"
                        break

                df = df.rename(columns=rename_map)
                # 如果溢价率列名为 A股/H股 比值，需转换为溢价率百分比
                if "AH溢价率" not in df.columns and "A股价格" in df.columns and "H股价格" in df.columns:
                    df["AH溢价率"] = (df["A股价格"] / df["H股价格"].replace(0, np.nan) - 1) * 100

                keep = [c for c in ["代码", "名称", "A股价格", "H股价格", "AH溢价率"] if c in df.columns]
                if not keep or "代码" not in keep or "AH溢价率" not in keep:
                    print("     [WARN] A/H 溢价数据字段缺失，跳过")
                    self._ah_premium_cache = pd.DataFrame()
                    return self._ah_premium_cache

                df = df[keep].copy()
                df["代码"] = df["代码"].astype(str).str.strip()
                df["AH溢价率"] = pd.to_numeric(df["AH溢价率"], errors="coerce").fillna(0)
                self._ah_premium_cache = df
                return df
        except Exception as e:
            print(f"     [WARN] A/H 溢价数据获取失败: {e}")
            # Fallback：通过 H 股 spot + A 股 spot 按名称匹配估算
            try:
                return self._get_ah_premium_fallback()
            except Exception as fallback_e:
                print(f"     [WARN] A/H 溢价 fallback 也失败: {fallback_e}")

        self._ah_premium_cache = pd.DataFrame()
        return self._ah_premium_cache

    def _get_ah_premium_fallback(self) -> pd.DataFrame:
        """
        当 stock_zh_ah_spot_em 不可用时，用 H 股 spot 与 A 股 spot 按名称匹配估算 A/H 溢价。
        返回 DataFrame[代码, 名称, A股价格, H股价格, AH溢价率]
        """
        # H 股数据
        h_df = ak.stock_zh_ah_spot()
        if h_df is None or h_df.empty or "名称" not in h_df.columns or "最新价" not in h_df.columns:
            return pd.DataFrame()

        # A 股数据（复用已有接口）
        a_df = self.get_stock_list()
        if a_df is None or a_df.empty or "名称" not in a_df.columns or "收盘价" not in a_df.columns:
            return pd.DataFrame()

        # 构建 A 股名称索引（去掉常见后缀，便于匹配）
        a_names = {}
        for _, row in a_df.iterrows():
            name = str(row.get("名称", "")).strip()
            code = str(row.get("代码", "")).strip()
            price = float(row.get("收盘价", 0) or 0)
            if name and code and price > 0:
                a_names[name] = (code, price)
                # 也去后缀版本
                for suffix in ["A", "B", "股份", "集团", "控股", "有限"]:
                    clean = name.rstrip(suffix)
                    if clean and clean not in a_names:
                        a_names[clean] = (code, price)

        results = []
        for _, row in h_df.iterrows():
            h_name = str(row.get("名称", "")).strip()
            h_price = float(row.get("最新价", 0) or 0)
            if not h_name or h_price <= 0:
                continue

            # 尝试直接匹配或去掉 H 股后缀后匹配
            matched = None
            if h_name in a_names:
                matched = a_names[h_name]
            else:
                for suffix in ["股份", "集团", "控股", "有限", "公司", "企业"]:
                    clean = h_name.rstrip(suffix)
                    if clean and clean in a_names:
                        matched = a_names[clean]
                        break
                if not matched:
                    # 尝试 A 股名称包含 H 股简称（去掉股份）
                    clean = h_name.replace("股份", "").replace("集团", "").replace("控股", "").strip()
                    for a_name, (code, price) in a_names.items():
                        if clean and (clean in a_name or a_name in clean):
                            matched = (code, price)
                            break

            if matched:
                a_code, a_price = matched
                premium = (a_price / h_price - 1) * 100
                results.append({
                    "代码": a_code,
                    "名称": h_name,
                    "A股价格": round(a_price, 2),
                    "H股价格": round(h_price, 2),
                    "AH溢价率": round(premium, 2)
                })

        if not results:
            return pd.DataFrame()

        df = pd.DataFrame(results)
        self._ah_premium_cache = df
        return df

    def _get_mock_ah_premium_data(self) -> pd.DataFrame:
        """模拟 A/H 溢价数据"""
        mock_path = os.path.join(self.cache_dir, "mock_ah_premium.csv")
        if os.path.exists(mock_path):
            return pd.read_csv(mock_path, encoding="utf-8-sig")
        from utils.mock_data import generate_mock_ah_premium
        df = generate_mock_ah_premium()
        os.makedirs(self.cache_dir, exist_ok=True)
        df.to_csv(mock_path, index=False, encoding="utf-8-sig")
        return df

    # ==================== 可转债数据 ====================

    def get_cb_data(self, use_mock: bool = False) -> pd.DataFrame:
        """
        获取可转债数据（akshare 主，mock 兜底）
        返回: DataFrame[正股代码, 转债代码, 转债名称, 转股溢价率, 转股价值, 转债价格, 转债成交额]
        """
        if self._cb_data_cache is not None:
            return self._cb_data_cache

        if use_mock or self.data_source == "mock":
            return self._get_mock_cb_data()

        try:
            # 优先使用 bond_zh_cov，包含正股代码、转股价值、转股溢价率等完整字段
            df = ak.bond_zh_cov()
            if df is not None and not df.empty:
                rename_map = {
                    "债券代码": "转债代码",
                    "债券简称": "转债名称",
                    "正股代码": "正股代码",
                    "正股价": "正股价格",
                    "转股价": "转股价",
                    "转股价值": "转股价值",
                    "债现价": "转债价格",
                    "转股溢价率": "转股溢价率",
                }
                actual_rename = {k: v for k, v in rename_map.items() if k in df.columns}
                df = df.rename(columns=actual_rename)
                keep = [c for c in ["正股代码", "转债代码", "转债名称", "转股溢价率", "转股价值", "转债价格", "转股价"] if c in df.columns]
                if "正股代码" in keep and "转股溢价率" in keep:
                    df = df[keep].copy()
                    df["正股代码"] = df["正股代码"].astype(str).str.strip()
                    for col in ["转股溢价率", "转股价值", "转债价格", "转股价"]:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
                    self._cb_data_cache = df
                    return df

            # 兜底：bond_zh_hs_cov_spot 仅含基本行情
            df = ak.bond_zh_hs_cov_spot()
            if df is not None and not df.empty:
                rename_map = {}
                stock_code_candidates = ["正股代码", "股票代码", "正股代码", "underlying_code"]
                bond_code_candidates = ["代码", "债券代码", "bond_code", "code"]
                bond_name_candidates = ["名称", "债券简称", "bond_name", "name"]
                premium_candidates = ["转股溢价率", "溢价率", "conversion_premium"]
                value_candidates = ["转股价值", "conversion_value"]
                price_candidates = ["最新价", "收盘价", "转债价格", "price"]
                amount_candidates = ["成交额", "转债成交额", "amount"]

                for c in stock_code_candidates:
                    if c in df.columns and "正股代码" not in df.columns and "正股代码" not in rename_map.values():
                        rename_map[c] = "正股代码"
                        break
                for c in bond_code_candidates:
                    if c in df.columns and "转债代码" not in df.columns and "转债代码" not in rename_map.values():
                        rename_map[c] = "转债代码"
                        break
                for c in bond_name_candidates:
                    if c in df.columns and "转债名称" not in df.columns and "转债名称" not in rename_map.values():
                        rename_map[c] = "转债名称"
                        break
                for c in premium_candidates:
                    if c in df.columns and "转股溢价率" not in df.columns and "转股溢价率" not in rename_map.values():
                        rename_map[c] = "转股溢价率"
                        break
                for c in value_candidates:
                    if c in df.columns and "转股价值" not in df.columns and "转股价值" not in rename_map.values():
                        rename_map[c] = "转股价值"
                        break
                for c in price_candidates:
                    if c in df.columns and "转债价格" not in df.columns and "转债价格" not in rename_map.values():
                        rename_map[c] = "转债价格"
                        break
                for c in amount_candidates:
                    if c in df.columns and "转债成交额" not in df.columns and "转债成交额" not in rename_map.values():
                        rename_map[c] = "转债成交额"
                        break

                df = df.rename(columns=rename_map)
                keep = [c for c in ["正股代码", "转债代码", "转债名称", "转股溢价率", "转股价值", "转债价格", "转债成交额"]
                        if c in df.columns]
                if not keep or "正股代码" not in keep or "转股溢价率" not in keep:
                    print("     [WARN] 可转债数据字段缺失，跳过")
                    self._cb_data_cache = pd.DataFrame()
                    return self._cb_data_cache

                df = df[keep].copy()
                df["正股代码"] = df["正股代码"].astype(str).str.strip()
                for col in ["转股溢价率", "转股价值", "转债价格", "转债成交额"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
                self._cb_data_cache = df
                return df
        except Exception as e:
            print(f"     [WARN] 可转债数据获取失败: {e}")

        self._cb_data_cache = pd.DataFrame()
        return self._cb_data_cache

    def _get_mock_cb_data(self) -> pd.DataFrame:
        """模拟可转债数据"""
        mock_path = os.path.join(self.cache_dir, "mock_cb_data.csv")
        if os.path.exists(mock_path):
            return pd.read_csv(mock_path, encoding="utf-8-sig")
        from utils.mock_data import generate_mock_cb_data
        df = generate_mock_cb_data()
        os.makedirs(self.cache_dir, exist_ok=True)
        df.to_csv(mock_path, index=False, encoding="utf-8-sig")
        return df

    # ==================== 筹码分布数据 ====================

    def get_chip_distribution_data(self, codes: list = None, use_mock: bool = False) -> pd.DataFrame:
        """
        获取个股筹码分布数据（akshare stock_cyq_em）
        返回: DataFrame[代码, 名称, 90%集中度, 70%集中度, 平均成本, 获利比例]
        """
        if self._chip_cache is not None:
            return self._chip_cache

        if use_mock or self.data_source == "mock":
            return self._get_mock_chip_data()

        if self.data_source in ("auto", "akshare"):
            try:
                results = []
                target_codes = codes or []

                def _fetch_one(symbol: str):
                    try:
                        df = ak.stock_cyq_em(symbol=symbol, adjust="")
                        if df is not None and not df.empty:
                            latest = df.iloc[-1]
                            return {
                                "代码": str(symbol).strip(),
                                "名称": "",
                                "90%集中度": float(latest.get("90集中度", 0) or 0) * 100,
                                "70%集中度": float(latest.get("70集中度", 0) or 0) * 100,
                                "平均成本": float(latest.get("平均成本", 0) or 0),
                                "获利比例": float(latest.get("获利比例", 0) or 0) * 100,
                            }
                    except Exception:
                        pass
                    return None

                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=6) as executor:
                    futures = {executor.submit(_fetch_one, c): c for c in target_codes}
                    for future in futures:
                        r = future.result()
                        if r:
                            results.append(r)

                if results:
                    df = pd.DataFrame(results)
                    self._chip_cache = df
                    return df
            except Exception as e:
                print(f"     [WARN] 筹码分布数据获取失败: {e}")

        return pd.DataFrame()

    def _get_mock_chip_data(self) -> pd.DataFrame:
        """模拟筹码分布数据"""
        mock_path = os.path.join(self.cache_dir, "mock_chip_data.csv")
        if os.path.exists(mock_path):
            return pd.read_csv(mock_path, encoding="utf-8-sig")
        from utils.mock_data import generate_mock_chip_data
        df = generate_mock_chip_data()
        os.makedirs(self.cache_dir, exist_ok=True)
        df.to_csv(mock_path, index=False, encoding="utf-8-sig")
        return df

    # ==================== 新闻/情绪数据 ====================

    def get_stock_news(self, symbol: str, limit: int = 10,
                        source: str = None) -> pd.DataFrame:
        """
        获取个股最新新闻与公告。

        支持双数据源互补：
        - akshare (stock_news_em): 财经媒体新闻，偏市场热点/突发事件
        - tushare (pro.anns_d):   交易所/巨潮官方公告，偏合规/业绩/重大事项

        Args:
            symbol: 股票代码（自动去掉 sh/sz 前缀）
            limit:  返回条数上限
            source: "akshare" / "tushare" / "auto"，None 则使用 self.data_source

        返回: DataFrame[标题, 发布时间, 摘要, 内容]
        """
        if symbol.startswith("sh") or symbol.startswith("sz"):
            symbol = symbol[2:]
        if symbol in self._news_cache:
            return self._news_cache[symbol]

        if self.data_source == "mock" or source == "mock":
            self._news_cache[symbol] = pd.DataFrame()
            return pd.DataFrame()

        # 未指定时，优先跟随全局数据源；auto 表示双源合并
        if source is None:
            source = self.data_source

        dfs = []
        if source in ("auto", "akshare"):
            try:
                df_ak = ak.stock_news_em(symbol=symbol)
                if df_ak is not None and not df_ak.empty:
                    dfs.append(df_ak)
            except Exception as e:
                print(f"     [WARN] akshare 新闻获取失败 {symbol}: {e}")

        if source in ("auto", "tushare"):
            try:
                df_ts = self._get_stock_news_tushare(symbol, limit=limit)
                if df_ts is not None and not df_ts.empty:
                    dfs.append(df_ts)
            except Exception as e:
                print(f"     [WARN] tushare 公告获取失败 {symbol}: {e}")

        if dfs:
            df = pd.concat(dfs, ignore_index=True)
            df = self._standardize_news_df(df)
            df = df.head(limit)
        else:
            df = pd.DataFrame()

        self._news_cache[symbol] = df
        return df

    def _get_stock_news_tushare(self, symbol: str, limit: int = 10) -> pd.DataFrame:
        """通过 tushare pro.anns_d / pro.anns 获取上市公司公告"""
        pro = self._get_tushare_pro()
        if not pro:
            return pd.DataFrame()

        try:
            suffix = ".SH" if symbol.startswith("6") else ".SZ"
            ts_code = f"{symbol}{suffix}"

            # 优先新版 anns_d，失败回退旧版 anns
            df = pd.DataFrame()
            try:
                df = pro.anns_d(ts_code=ts_code, limit=limit)
            except Exception:
                pass
            if df is None or df.empty:
                df = pro.anns(ts_code=ts_code, limit=limit)
            if df is None or df.empty:
                return pd.DataFrame()

            # 列名映射（防御式：不同版本字段名可能不同）
            rename_map = {}
            if "title" in df.columns and "标题" not in df.columns:
                rename_map["title"] = "标题"
            date_col = None
            for c in ["ann_date", "publish_date", "公告日期"]:
                if c in df.columns and "发布时间" not in df.columns and "发布时间" not in rename_map.values():
                    date_col = c
                    break
            if date_col:
                rename_map[date_col] = "发布时间"
            if "url" in df.columns and "摘要" not in df.columns:
                rename_map["url"] = "摘要"
            if "ann_type" in df.columns and "内容" not in df.columns:
                rename_map["ann_type"] = "内容"
            if "content" in df.columns and "内容" not in df.columns:
                rename_map["content"] = "内容"

            result = df.rename(columns=rename_map).copy()
            for col in ["标题", "发布时间", "摘要", "内容"]:
                if col not in result.columns:
                    result[col] = ""
            return result[["标题", "发布时间", "摘要", "内容"]]
        except Exception as e:
            print(f"     [WARN] tushare 公告解析失败 {symbol}: {e}")
            return pd.DataFrame()

    def _standardize_news_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """把不同来源的新闻 DataFrame 统一为 标题/发布时间/摘要/内容 四列"""
        if df is None or df.empty:
            return pd.DataFrame()

        rename_map = {}
        # 标题列
        if "标题" not in df.columns:
            for c in ["title", "新闻标题"]:
                if c in df.columns:
                    rename_map[c] = "标题"
                    break
        # 发布时间列
        if "发布时间" not in df.columns:
            for c in ["pub_date", "ann_date", "publish_date", "公告日期"]:
                if c in df.columns:
                    rename_map[c] = "发布时间"
                    break
        # 摘要列
        if "摘要" not in df.columns:
            for c in ["summary", "新闻摘要"]:
                if c in df.columns:
                    rename_map[c] = "摘要"
                    break
            if "摘要" not in df.columns and "内容" not in df.columns and "content" in df.columns:
                rename_map["content"] = "摘要"
        # 内容/链接列
        if "内容" not in df.columns:
            for c in ["content", "url", "新闻链接"]:
                if c in df.columns:
                    rename_map[c] = "内容"
                    break

        df = df.rename(columns=rename_map)
        for col in ["标题", "发布时间", "摘要", "内容"]:
            if col not in df.columns:
                df[col] = ""
        return df[["标题", "发布时间", "摘要", "内容"]]

    def get_stock_comment(self, symbol: str) -> dict:
        """
        获取个股评论/情绪摘要 (akshare stock_comment_em)
        返回: {"sentiment": str, "score": float}
        """
        if symbol.startswith("sh") or symbol.startswith("sz"):
            symbol = symbol[2:]
        if symbol in self._comment_cache:
            return self._comment_cache[symbol]

        result = {}
        if self.data_source == "mock":
            self._comment_cache[symbol] = result
            return result

        try:
            # stock_comment_em 返回全市场数据，需按代码过滤
            if not hasattr(self, '_comment_all'):
                self._comment_all = ak.stock_comment_em()
            df = self._comment_all
            if df is not None and not df.empty and "代码" in df.columns:
                row = df[df["代码"].astype(str).str.strip() == symbol]
                if not row.empty:
                    latest = row.iloc[0]
                    # 综合得分 0-100，映射到 -1..1
                    score_100 = float(latest.get("综合得分", 50) or 50)
                    normalized = (score_100 - 50) / 50  # -1..1
                    # 情绪标签
                    if normalized > 0.2:
                        sentiment = "看多"
                    elif normalized < -0.2:
                        sentiment = "看空"
                    else:
                        sentiment = "中性"
                    result = {
                        "sentiment": sentiment,
                        "score": round(normalized, 3)
                    }
        except Exception as e:
            print(f"     [WARN] 评论情绪获取失败 {symbol}: {e}")

        self._comment_cache[symbol] = result
        return result

    def get_broker_rating(self, symbol: str) -> dict:
        """
        获取个股最新研报评级 (akshare stock_research_report_em)
        返回: {"rating": str, "change": str, "org": str, "title": str, "date": str}
        """
        if symbol.startswith("sh") or symbol.startswith("sz"):
            symbol = symbol[2:]
        if symbol in self._rating_cache:
            return self._rating_cache[symbol]

        result = {}
        if self.data_source == "mock":
            self._rating_cache[symbol] = result
            return result

        try:
            df = ak.stock_research_report_em(symbol=symbol)
            if df is not None and not df.empty:
                latest = df.iloc[0]
                rating = str(latest.get("东财评级", ""))
                result = {
                    "rating": rating,
                    "change": "",  # 该接口无变化字段
                    "org": str(latest.get("机构", "")),
                    "title": str(latest.get("报告名称", "")),
                    "date": str(latest.get("日期", "")),
                }
        except Exception as e:
            print(f"     [WARN] 研报评级获取失败 {symbol}: {e}")

        self._rating_cache[symbol] = result
        return result

    def get_market_capital_env(self) -> dict:
        """获取大盘资金面环境：北向资金 + 融资融券"""
        if self._market_capital_cache is not None:
            return self._market_capital_cache
        env = {
            "north_money": None,
            "margin_rzye": None,
            "notes": []
        }
        pro = self._get_tushare_pro()
        if not pro:
            return env
        trade_date = self._get_last_trade_date()
        # 北向资金
        try:
            df = pro.moneyflow_hsgt(trade_date=trade_date)
            if df is not None and not df.empty:
                north = df.iloc[0].get("north_money", 0)
                env["north_money"] = round(north / 1e4, 2)  # 转为亿
                if north > 0:
                    env["notes"].append(f"北向净流入 {env['north_money']:.1f} 亿")
                elif north < 0:
                    env["notes"].append(f"北向净流出 {abs(env['north_money']):.1f} 亿")
                else:
                    env["notes"].append("北向资金平衡")
        except Exception:
            pass
        # 融资融券
        try:
            df = pro.margin(trade_date=trade_date)
            if df is not None and not df.empty:
                rzye = df["rzye"].sum() if "rzye" in df.columns else 0
                env["margin_rzye"] = round(rzye / 1e8, 2)  # 转为亿
                env["notes"].append(f"融资余额 {env['margin_rzye']:.0f} 亿")
        except Exception:
            pass
        self._market_capital_cache = env
        return env

    # ==================== 每日财经简报 ====================

    def get_daily_briefing(self, config: dict = None) -> pd.DataFrame:
        """
        获取当天实时财经简报，主源财联社，失败时按配置降级。

        返回: DataFrame[时间, 来源, 标题, 内容]
        """
        if self._daily_briefing_cache is not None:
            return self._daily_briefing_cache

        cfg = config or {}
        ai_cfg = cfg.get("ai_briefing", {})
        ns_cfg = ai_cfg.get("news_sources", {})
        max_age_hours = ns_cfg.get("max_age_hours", 24)
        max_items = ns_cfg.get("max_items_per_source", 200)
        min_items = ns_cfg.get("min_items_threshold", 20)
        max_chars = ns_cfg.get("max_briefing_chars", 50000)

        primary = ns_cfg.get("primary", "cls_alerts")
        fallbacks = ns_cfg.get("fallbacks", [])

        all_records = []

        # 主源
        primary_records = self._fetch_briefing_source(primary, max_items=max_items)
        all_records.extend(primary_records)
        if len(all_records) >= min_items:
            df = self._build_briefing_df(all_records, max_age_hours, max_chars)
            self._daily_briefing_cache = df
            return df

        # 降级源
        for src in fallbacks:
            fallback_records = self._fetch_briefing_source(src, max_items=max_items)
            all_records.extend(fallback_records)
            if len(all_records) >= min_items:
                break

        df = self._build_briefing_df(all_records, max_age_hours, max_chars)
        self._daily_briefing_cache = df
        return df

    def _fetch_briefing_source(self, source: str, max_items: int = 200) -> list:
        """根据 source 名称抓取简报，返回记录列表"""
        try:
            if source == "cls_alerts":
                return self._fetch_cls_alerts(max_items)
            if source == "em_global":
                return self._fetch_em_global(max_items)
            if source == "sina_global":
                return self._fetch_sina_global(max_items)
            if source == "ths_live":
                return self._fetch_ths_live(max_items)
            if source == "futu_global":
                return self._fetch_futu_global(max_items)
        except Exception as e:
            print(f"     [WARN] 简报来源 {source} 获取失败: {e}")
        return []

    def _fetch_cls_alerts(self, max_items: int = 200) -> list:
        """财联社 A 股快讯（akshare 旧接口已下线，保留兼容）"""
        if not hasattr(ak, "stock_zh_a_alerts_cls"):
            return []
        df = ak.stock_zh_a_alerts_cls()
        return self._normalize_briefing_df(df, source="cls", content_col="快讯信息",
                                           time_col="时间", max_items=max_items)

    def _fetch_em_global(self, max_items: int = 200) -> list:
        """东方财富全球快讯"""
        df = ak.stock_info_global_em()
        return self._normalize_briefing_df(df, source="em", content_col="摘要",
                                           title_col="标题", time_col="发布时间",
                                           max_items=max_items)

    def _fetch_sina_global(self, max_items: int = 200) -> list:
        """新浪财经全球快讯"""
        df = ak.stock_info_global_sina()
        return self._normalize_briefing_df(df, source="sina", content_col="内容",
                                           time_col="时间", max_items=max_items)

    def _fetch_ths_live(self, max_items: int = 200) -> list:
        """同花顺财经直播"""
        df = ak.stock_info_global_ths()
        return self._normalize_briefing_df(df, source="ths", content_col="内容",
                                           title_col="标题", time_col="发布时间",
                                           max_items=max_items)

    def _fetch_futu_global(self, max_items: int = 200) -> list:
        """富途牛牛全球快讯"""
        df = ak.stock_info_global_futu()
        return self._normalize_briefing_df(df, source="futu", content_col="内容",
                                           title_col="标题", time_col="发布时间",
                                           max_items=max_items)

    def _normalize_briefing_df(self, df: pd.DataFrame, source: str,
                               content_col: str = None, title_col: str = None,
                               time_col: str = None, max_items: int = 200) -> list:
        """把不同来源简报统一为记录列表"""
        if df is None or df.empty:
            return []

        records = []
        for _, row in df.head(max_items).iterrows():
            content = ""
            if content_col and content_col in row:
                content = str(row[content_col] or "")
            title = ""
            if title_col and title_col in row:
                title = str(row[title_col] or "")
            if not content and title:
                content = title
            if not content:
                continue

            time_val = ""
            if time_col and time_col in row:
                time_val = str(row[time_col] or "")

            # 去重：相同内容跳过
            if any(r["内容"] == content for r in records):
                continue

            records.append({
                "时间": time_val,
                "来源": source,
                "标题": title,
                "内容": content,
            })
        return records

    def _build_briefing_df(self, records: list, max_age_hours: int,
                           max_chars: int) -> pd.DataFrame:
        """构建最终简报 DataFrame，过滤时间并限制总长度"""
        if not records:
            return pd.DataFrame(columns=["时间", "来源", "标题", "内容"])

        df = pd.DataFrame(records)

        # 时间过滤
        if max_age_hours > 0 and "时间" in df.columns:
            cutoff = datetime.now() - timedelta(hours=max_age_hours)
            kept = []
            for _, row in df.iterrows():
                dt = self._parse_briefing_time(row.get("时间", ""))
                if dt is None or dt >= cutoff:
                    kept.append(row)
            df = pd.DataFrame(kept, columns=df.columns).reset_index(drop=True)

        # 限制总字符数，优先保留最新（假设 DataFrame 已按时间倒序或正序）
        total_chars = df["内容"].astype(str).str.len().sum() if "内容" in df.columns else 0
        if total_chars > max_chars and not df.empty:
            kept = []
            current = 0
            # 优先保留时间更近的（按索引靠后的一般更晚）
            for _, row in df.iterrows():
                text = str(row.get("内容", ""))
                if current + len(text) <= max_chars:
                    kept.append(row)
                    current += len(text)
                else:
                    break
            df = pd.DataFrame(kept, columns=df.columns).reset_index(drop=True)

        return df

    def _parse_briefing_time(self, value) -> Optional[datetime]:
        """解析简报时间，支持多种格式"""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        text = str(value).strip()
        if not text or text.lower() in ("nan", "none", ""):
            return None

        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y%m%d%H%M%S",
            "%Y%m%d%H%M",
            "%Y%m%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y/%m/%d",
            "%H:%M:%S",
            "%H:%M",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(text, fmt)
                # 只有时间的补齐今天日期
                if fmt in ("%H:%M:%S", "%H:%M"):
                    now = datetime.now()
                    dt = dt.replace(year=now.year, month=now.month, day=now.day)
                return dt
            except ValueError:
                continue
        try:
            return pd.to_datetime(text)
        except Exception:
            return None
