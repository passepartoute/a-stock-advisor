"""
新闻/情绪面分析器
为个股提供消息面评分，并在盘前对负面消息进行降级或否决。
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from datetime import datetime, timedelta


class NewsSentimentAnalyzer:
    """
    消息面因子分析器。

    输入：个股新闻、评论情绪、研报评级变化。
    输出：情绪评分 (-1..1)、信号描述、是否建议降级/否决、原因。

    设计原则：
    - 坏消息优先：负面消息触发降级或跳过，避免消息面黑天鹅。
    - 好消息不直接加分：防止噪音和过拟合。
    - 数据缺失时中性通过：不因为拿不到数据而误杀。
    """

    # 默认负面关键词（可在 config/settings.yaml 中覆盖）
    DEFAULT_NEGATIVE_KEYWORDS = [
        # 监管/合规风险
        "立案", "调查", "处罚", "监管", "问询", "警示", "整改",
        # 退市/业绩风险
        "退市", "ST", "亏损", "业绩预亏", "业绩下滑", "营收下降", "净利润下降",
        # 减持/解禁
        "大股东减持", "高管减持", "减持计划", "清仓减持", "解禁",
        # 法律/经营风险
        "合同纠纷", "诉讼", "仲裁", "赔偿", "产品召回", "停产", "整顿", "查封",
        # 债务/评级风险
        "债务违约", "评级下调", "卖出评级", "减持评级",
    ]

    # 否定词白名单：命中负面词但包含这些词时不视为负面
    NEGATION_WORDS = ["不", "无", "未", "没有", "不存在", "澄清", "辟谣"]

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.ns_cfg = self.config.get("news_sentiment", {})
        self.enabled = self.ns_cfg.get("enabled", False)
        self.max_news_per_stock = self.ns_cfg.get("max_news_per_stock", 10)
        self.max_news_age_hours = self.ns_cfg.get("max_news_age_hours", 48)
        self.min_news_count = self.ns_cfg.get("min_news_count", 2)
        self.negative_threshold = self.ns_cfg.get("negative_threshold", -0.5)
        self.downgrade_threshold = self.ns_cfg.get("downgrade_threshold", -0.2)

        veto_cfg = self.ns_cfg.get("veto_rules", {})
        self.veto_enabled = veto_cfg.get("enabled", True)
        self.negative_keywords = veto_cfg.get("keywords", self.DEFAULT_NEGATIVE_KEYWORDS)
        self.instant_veto_keywords = set(veto_cfg.get("instant_veto_keywords", []))
        self.negative_news_count_threshold = veto_cfg.get("negative_news_count_threshold", 2)
        self.veto_on_downgrade_to = set(veto_cfg.get("veto_on_downgrade_to", ["减持", "卖出"]))
        self.veto_on_bearish_comment_threshold = veto_cfg.get("veto_on_bearish_comment_threshold", -0.6)

    def analyze(self, code: str,
                news_df: pd.DataFrame = None,
                comment_data: dict = None,
                rating_data: dict = None) -> Dict:
        """
        对单只股票进行消息面分析。

        Returns:
            {
                "score": float,              # -1.0 ~ 1.0
                "signals": List[str],        # 可读信号
                "action": str,               # "pass" / "downgrade" / "veto"
                "action_reason": str|None,
                "news_count": int,
                "negative_count": int,
                "positive_count": int,
                "details": dict,
            }
        """
        news_df = news_df.copy() if news_df is not None else pd.DataFrame()
        comment_data = comment_data or {}
        rating_data = rating_data or {}

        # 统一列名：兼容 akshare 原始列名（新闻标题/新闻摘要/新闻链接）
        news_df = self._standardize_news_columns(news_df)

        # 按时间过滤：只保留 max_news_age_hours 内的新闻
        news_df = self._filter_news_by_age(news_df)

        details = {
            "news_titles": [],
            "comment": comment_data,
            "rating": rating_data,
        }

        # 1. 新闻打分
        news_score, negative_count, positive_count, matched_titles = self._score_news(news_df)
        details["negative_titles"] = matched_titles[:5]
        details["news_count"] = len(news_df)
        details["news_titles"] = news_df["标题"].tolist()[:10] if not news_df.empty else []

        # 2. 评论情绪打分
        comment_score = self._score_comment(comment_data)

        # 3. 研报评级打分
        rating_score = self._score_rating(rating_data)

        # 4. 综合得分（加权）
        weights = self.ns_cfg.get("score_weights", {"news": 0.5, "comment": 0.3, "rating": 0.2})
        total_score = (
            news_score * weights.get("news", 0.5) +
            comment_score * weights.get("comment", 0.3) +
            rating_score * weights.get("rating", 0.2)
        )
        total_score = round(max(-1.0, min(1.0, total_score)), 3)

        signals = []
        if negative_count > 0:
            signals.append(f"负面新闻{negative_count}条")
        if positive_count > 0:
            signals.append(f"正面新闻{positive_count}条")
        if comment_data:
            sentiment = comment_data.get("sentiment", "")
            if sentiment:
                signals.append(f"情绪{sentiment}")
        if rating_data:
            rating = rating_data.get("rating", "")
            change = rating_data.get("change", "")
            if rating:
                signals.append(f"评级{rating}")
            if change:
                signals.append(f"评级变化{change}")

        # 5. 决策：否决 > 降级 > 通过
        action, action_reason = self._decide_action(
            total_score, negative_count, len(news_df), comment_score, rating_data
        )

        return {
            "score": total_score,
            "signals": signals,
            "action": action,
            "action_reason": action_reason,
            "news_count": len(news_df),
            "negative_count": negative_count,
            "positive_count": positive_count,
            "details": details,
        }

    def _score_news(self, news_df: pd.DataFrame) -> tuple:
        """
        扫描新闻标题，统计正负关键词命中次数。
        返回 (score, negative_count, positive_count, matched_negative_titles)
        """
        if news_df.empty:
            return 0.0, 0, 0, []

        negative_count = 0
        positive_count = 0
        matched_negative_titles = []

        for _, row in news_df.iterrows():
            title = str(row.get("标题", ""))
            summary = str(row.get("摘要", ""))
            content = str(row.get("内容", ""))
            text = f"{title} {summary} {content}"

            # 检查是否包含即时否决关键词
            instant_match = any(kw in text for kw in self.instant_veto_keywords)
            if instant_match:
                negative_count += 2  # 加重权重
                matched_negative_titles.append(title[:40])
                continue

            # 检查普通负面关键词（需排除否定词）
            is_negative = False
            for kw in self.negative_keywords:
                if kw in text:
                    # 简单否定词检查：关键词前面 6 个字内出现否定词则跳过
                    idx = text.find(kw)
                    prefix = text[max(0, idx - 6):idx]
                    if any(neg in prefix for neg in self.NEGATION_WORDS):
                        continue
                    is_negative = True
                    break

            if is_negative:
                negative_count += 1
                matched_negative_titles.append(title[:40])
            else:
                # 简单正面检查（不直接加分，只统计）
                positive_signals = ["增持", "回购", "分红", "业绩预增", "中标", "合作协议"]
                if any(p in text for p in positive_signals):
                    positive_count += 1

        # 得分：负面越多分越低；有正面新闻不直接加分，仅轻微缓冲
        if negative_count == 0:
            score = 0.0
        else:
            # 1条负面 -> -0.3, 2条 -> -0.6, 3条+ -> -0.9
            score = max(-0.9, -0.3 * min(negative_count, 3))

        return score, negative_count, positive_count, matched_negative_titles

    def _standardize_news_columns(self, news_df: pd.DataFrame) -> pd.DataFrame:
        """把 akshare/tushare 原始列名统一为 标题/发布时间/摘要/内容"""
        if news_df.empty:
            return news_df
        rename_map = {}
        if "标题" not in news_df.columns:
            for c in ["title", "新闻标题"]:
                if c in news_df.columns:
                    rename_map[c] = "标题"
                    break
        if "发布时间" not in news_df.columns:
            for c in ["pub_date", "ann_date", "publish_date", "公告日期"]:
                if c in news_df.columns:
                    rename_map[c] = "发布时间"
                    break
        if "摘要" not in news_df.columns:
            for c in ["summary", "新闻摘要"]:
                if c in news_df.columns:
                    rename_map[c] = "摘要"
                    break
        if "内容" not in news_df.columns:
            for c in ["content", "url", "新闻链接"]:
                if c in news_df.columns:
                    rename_map[c] = "内容"
                    break
        if not rename_map:
            return news_df
        df = news_df.rename(columns=rename_map).copy()
        for col in ["标题", "发布时间", "摘要", "内容"]:
            if col not in df.columns:
                df[col] = ""
        return df

    def _filter_news_by_age(self, news_df: pd.DataFrame) -> pd.DataFrame:
        """过滤掉超过 max_news_age_hours 小时的旧新闻。解析失败时保守保留。"""
        if news_df.empty or self.max_news_age_hours <= 0:
            return news_df

        if "发布时间" not in news_df.columns:
            return news_df

        cutoff = datetime.now() - timedelta(hours=self.max_news_age_hours)
        kept = []
        for _, row in news_df.iterrows():
            dt = self._parse_news_time(row.get("发布时间", ""))
            if dt is None or dt >= cutoff:
                kept.append(row)
        return pd.DataFrame(kept, columns=news_df.columns).reset_index(drop=True)

    def _parse_news_time(self, value) -> Optional[datetime]:
        """尝试多种常见格式解析新闻发布时间，失败返回 None。"""
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
        ]
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        # 处理 pandas Timestamp / datetime 对象
        try:
            return pd.to_datetime(text)
        except Exception:
            return None

    def _score_comment(self, comment_data: dict) -> float:
        """将 stock_comment_em 的情绪摘要转换为数值分数。"""
        if not comment_data:
            return 0.0

        sentiment = str(comment_data.get("sentiment", "")).strip()
        score = float(comment_data.get("score", 0) or 0)

        # 如果已有数值分数，直接归一化到 [-1, 1]
        if score != 0:
            return max(-1.0, min(1.0, score))

        # 否则按情绪标签映射
        mapping = {
            "看多": 0.5,
            "看涨": 0.5,
            "强势": 0.4,
            "中性": 0.0,
            "观望": 0.0,
            "看空": -0.6,
            "看跌": -0.6,
            "弱势": -0.4,
        }
        return mapping.get(sentiment, 0.0)

    def _score_rating(self, rating_data: dict) -> float:
        """将研报评级变化转换为分数。"""
        if not rating_data:
            return 0.0

        rating = str(rating_data.get("rating", "")).strip()
        change = str(rating_data.get("change", "")).strip()

        # 评级映射
        rating_score = {
            "买入": 0.4,
            "增持": 0.2,
            "中性": 0.0,
            "持有": 0.0,
            "减持": -0.6,
            "卖出": -0.8,
        }.get(rating, 0.0)

        # 评级变化映射
        change_score = {
            "上调": 0.2,
            "维持": 0.0,
            "下调": -0.3,
            "首次": 0.1,
        }.get(change, 0.0)

        return max(-1.0, min(1.0, rating_score + change_score))

    def _decide_action(self, score: float, negative_count: int,
                       news_count: int, comment_score: float,
                       rating_data: dict) -> tuple:
        """
        根据综合得分决定操作：
        - "veto": 直接跳过挂单
        - "downgrade": 建议等级下调一级
        - "pass": 不处理
        """
        # 即时否决关键词：直接跳过
        if negative_count >= 2:
            return "veto", f"负面新闻{negative_count}条，超过阈值{self.negative_news_count_threshold}"

        # 研报评级下调至减持/卖出
        rating = str(rating_data.get("rating", "")).strip()
        if rating in self.veto_on_downgrade_to:
            return "veto", f"研报评级为'{rating}'"

        # 评论情绪极度看空
        if comment_score <= self.veto_on_bearish_comment_threshold:
            return "veto", f"情绪评分{comment_score:.2f}，极度看空"

        # 综合得分低于降级阈值：降级
        if score <= self.downgrade_threshold:
            return "downgrade", f"消息面评分{score:.2f}，低于降级阈值{self.downgrade_threshold}"

        return "pass", None

    @classmethod
    def batch_analyze(cls, codes: List[str], fetcher,
                      config: dict = None,
                      max_workers: int = 4) -> Dict[str, Dict]:
        """
        批量分析多只股票的消息面。

        Args:
            codes: 候选股票代码列表
            fetcher: DataFetcher 实例
            config: 配置字典
            max_workers: 并发数（限制 API 压力）

        Returns:
            {code: analyze_result}
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import time

        analyzer = cls(config)
        if not analyzer.enabled:
            return {}

        results = {}
        news_source = analyzer.ns_cfg.get("data_source", "auto")

        def _fetch_one(code: str):
            try:
                news = fetcher.get_stock_news(
                    code, limit=analyzer.max_news_per_stock, source=news_source
                )
                comment = fetcher.get_stock_comment(code)
                rating = fetcher.get_broker_rating(code)
                time.sleep(0.2)  # 减轻 API 压力
                return code, analyzer.analyze(code, news_df=news, comment_data=comment, rating_data=rating)
            except Exception as e:
                # 失败时中性通过
                return code, {
                    "score": 0.0,
                    "signals": [f"情绪获取失败: {e}"],
                    "action": "pass",
                    "action_reason": None,
                    "news_count": 0,
                    "negative_count": 0,
                    "positive_count": 0,
                    "details": {},
                }

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_fetch_one, c): c for c in codes}
            for future in as_completed(futures):
                code, result = future.result()
                results[code] = result

        return results


def apply_sentiment_action(result: dict, sentiment_result: dict,
                           downgrade_map: dict = None) -> dict:
    """
    根据消息面分析结果对股票建议等级进行降级或否决。

    Args:
        result: 单只股票分析结果（含 advice, code 等）
        sentiment_result: NewsSentimentAnalyzer.analyze() 输出
        downgrade_map: 降级映射，如 {"强烈关注": "关注", "关注": "轻度关注", ...}

    Returns:
        修改后的 result（若否决则 advice 变为 "回避" 并添加原因）
    """
    if not sentiment_result:
        return result

    action = sentiment_result.get("action", "pass")
    if action == "pass":
        return result

    result = result.copy()
    result["sentiment"] = sentiment_result

    if action == "veto":
        result["advice"] = "回避"
        result["veto"] = True
        result["veto_reason"] = f"消息面: {sentiment_result.get('action_reason', '')}"
        result["total_score"] = -1.0
        return result

    if action == "downgrade":
        advice_rank = {
            "强烈关注": 5, "关注": 4, "轻度关注": 3,
            "观望": 2, "谨慎": 1, "回避": 0
        }
        rank_to_advice = {v: k for k, v in advice_rank.items()}
        current = result.get("advice", "观望")
        current_rank = advice_rank.get(current, 2)

        # 优先使用配置中的降级映射
        if downgrade_map and current in downgrade_map:
            new_advice = downgrade_map[current]
        else:
            new_advice = rank_to_advice.get(max(0, current_rank - 1), "观望")

        result["advice"] = new_advice
        result["sentiment_downgraded"] = True
        result["sentiment_reason"] = sentiment_result.get("action_reason", "")
        return result

    return result
