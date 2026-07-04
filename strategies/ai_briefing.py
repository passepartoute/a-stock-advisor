"""
AI 财经简报解读器

职责：
1. 从 DataFetcher 获取当天实时财经简报（财联社等）。
2. 调用 LLM 一次，提取结构化宏观信号。
3. 把结果交给 AIFactorAdjuster 映射到选股因子。

设计原则：
- 每天只调一次 LLM，控制成本。
- LLM 失败或返回非法 JSON 时返回 None，由 main.py 降级到纯量化流程。
- 提示词要求只输出合法 JSON，便于解析。
"""
import json
import re
from typing import Dict, Any, Optional
from datetime import datetime


# AI 输出结构化 Schema（也会写入 prompt）
AI_BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {
        "macro_sentiment": {
            "type": "number",
            "description": "整体宏观情绪，范围 -1.0(极度悲观) 到 1.0(极度乐观)，0 为中性"
        },
        "hot_sectors": {
            "type": "array",
            "items": {"type": "string"},
            "description": "简报中反复提及、表现强势或受政策支持的 A 股行业，如 ['半导体', '电力']"
        },
        "cold_sectors": {
            "type": "array",
            "items": {"type": "string"},
            "description": "简报中表现弱势、受利空压制或资金流出的 A 股行业，如 ['房地产', '教育']"
        },
        "policy_themes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "政策主题或产业趋势关键词，如 ['新质生产力', '设备更新', '低空经济']"
        },
        "risk_events": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可能对市场或特定行业造成压制的风险事件，如 ['中美关税', '地缘冲突', '监管收紧']"
        },
        "style_bias": {
            "type": "string",
            "enum": ["growth", "value", "defensive", "momentum", "neutral"],
            "description": "简报暗示的市场风格偏好"
        },
        "stock_mentions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码，如 600519"},
                    "name": {"type": "string", "description": "股票简称"},
                    "sentiment": {"type": "number", "description": "情绪 -1.0(负面) 到 1.0(正面)"},
                    "context": {"type": "string", "description": "提及上下文摘要，50字以内"}
                },
                "required": ["code", "name", "sentiment"]
            },
            "description": "简报中明确提到且带有情绪倾向的个股"
        },
        "veto_keywords": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可能触发回避的风险关键词"
        },
        "confidence": {
            "type": "number",
            "description": "AI 对本次解读的信心 0.0-1.0"
        },
        "raw_summary": {
            "type": "string",
            "description": "给投资者看的自然语言摘要，200字以内"
        }
    },
    "required": [
        "macro_sentiment", "hot_sectors", "cold_sectors", "policy_themes",
        "risk_events", "style_bias", "stock_mentions", "veto_keywords",
        "confidence", "raw_summary"
    ]
}


class AIBriefingAnalyzer:
    """AI 财经简报分析器"""

    def __init__(self, config: dict = None, llm_client=None):
        self.config = config or {}
        self.ai_cfg = self.config.get("ai_briefing", {})
        self.llm_client = llm_client

    def analyze(self, fetcher) -> Optional[Dict[str, Any]]:
        """
        主入口：获取简报并调用 LLM 提取信号。

        Returns:
            成功返回结构化 dict；失败返回 None。
        """
        if not self.ai_cfg.get("enabled", False):
            return None

        try:
            briefing_df = fetcher.get_daily_briefing(self.config)
            if briefing_df is None or briefing_df.empty:
                print("     [WARN] 未获取到当日财经简报，跳过 AI 解读")
                return None

            briefing_text = self._format_briefing(briefing_df)
            if not briefing_text:
                return None

            prompt = self._build_prompt(briefing_text)
            system = self._build_system_prompt()

            # 懒加载 LLM 客户端
            client = self.llm_client or self._create_llm_client()
            if client is None:
                print("     [WARN] LLM 客户端初始化失败，跳过 AI 解读")
                return None

            ai_cfg = self.config.get("ai_briefing", {})
            max_tokens = ai_cfg.get("max_tokens", 4096)
            temperature = ai_cfg.get("temperature", 0.3)
            max_retries = ai_cfg.get("max_retries", 2)

            parsed = None
            last_raw = None
            for attempt in range(max_retries + 1):
                # 不通过 output_schema 让 LLMClient 解析，直接取原始文本自己处理，便于重试和调试
                raw = client.complete(
                    prompt=prompt,
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if raw:
                    try:
                        parsed = self._extract_json(raw)
                        if parsed is not None:
                            break
                    except Exception as e:
                        print(f"     [WARN] 第 {attempt+1} 次解析 JSON 失败: {e}")
                    print(f"     [WARN] 第 {attempt+1} 次 LLM 返回非合法 JSON，长度={len(raw)}")
                    last_raw = raw
                else:
                    print(f"     [WARN] 第 {attempt+1} 次 LLM 返回为空")
                if attempt < max_retries:
                    print(f"     [INFO] 正在第 {attempt+2} 次重试...")

            if parsed is None:
                print(f"     [WARN] AI 简报分析未返回有效信号，使用默认权重")
                if last_raw:
                    print(f"              最后原始内容: {last_raw[:200]!r}")
                return None

            return self._normalize(parsed)
        except Exception as e:
            print(f"     [WARN] AI 简报分析异常: {e}")
            return None

    def _create_llm_client(self):
        """根据配置创建 LLMClient"""
        try:
            from utils.llm_client import LLMClient
            return LLMClient.from_config(self.config)
        except Exception as e:
            print(f"     [WARN] 创建 LLMClient 失败: {e}")
            return None

    def _format_briefing(self, briefing_df) -> str:
        """把简报 DataFrame 拼接成带序号的文本"""
        lines = []
        total = 0
        max_chars = self.ai_cfg.get("news_sources", {}).get("max_briefing_chars", 50000)
        for i, row in briefing_df.iterrows():
            time_str = str(row.get("时间", "")).strip()
            source = str(row.get("来源", "")).strip()
            title = str(row.get("标题", "")).strip()
            content = str(row.get("内容", "")).strip()
            if not content:
                continue
            parts = []
            if time_str:
                parts.append(time_str)
            if source:
                parts.append(f"[{source}]")
            if title and title != content:
                parts.append(title)
            parts.append(content)
            line = f"{i+1}. " + " ".join(parts)
            if total + len(line) > max_chars:
                break
            lines.append(line)
            total += len(line) + 1
        return "\n".join(lines)

    def _build_system_prompt(self) -> str:
        return (
            "你是一位资深的 A 股量化策略研究员。任务：阅读当天的实时财经简报，"
            "提取对选股有指导意义的宏观信号。输出必须是合法 JSON，不要 Markdown 代码块，"
            "不要解释说明。行业名称请使用 A 股常见行业简称（如 '半导体'、'电力'、'白酒'），"
            "便于与股票的行业字段做子串匹配。"
        )

    def _build_prompt(self, briefing_text: str) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        return (
            f"以下是 {today} 的实时财经简报（财联社等来源），请从中提取结构化投资信号。\n\n"
            f"【简报内容】\n{briefing_text}\n\n"
            "请输出一个 JSON 对象，字段含义如下：\n"
            "- macro_sentiment: 整体宏观情绪，范围 -1.0 到 1.0\n"
            "- hot_sectors: 热门/强势行业列表\n"
            "- cold_sectors: 弱势/受利空行业列表\n"
            "- policy_themes: 政策/产业主题关键词列表\n"
            "- risk_events: 风险事件关键词列表\n"
            "- style_bias: 市场风格偏好，取值 growth/value/defensive/momentum/neutral\n"
            "- stock_mentions: 明确提及的个股，每项含 code/name/sentiment/context\n"
            "- veto_keywords: 可能触发回避的风险关键词\n"
            "- confidence: 你对解读结果的信心 0.0-1.0\n"
            "- raw_summary: 给投资者看的自然语言摘要，200字以内\n\n"
            "注意：只输出合法 JSON，不要任何其他内容。"
        )

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        """从 LLM 返回文本中提取 JSON 对象"""
        if not text:
            return None
        text = text.strip()
        # 去掉 Markdown 代码块
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试找第一个 { 和最后一个 }
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start:end+1])
                except json.JSONDecodeError:
                    pass
        return None

    def _normalize(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
        """规范化 LLM 输出，补全缺失字段并做简单校验"""
        result = {
            "macro_sentiment": 0.0,
            "hot_sectors": [],
            "cold_sectors": [],
            "policy_themes": [],
            "risk_events": [],
            "style_bias": "neutral",
            "stock_mentions": [],
            "veto_keywords": [],
            "confidence": 0.0,
            "raw_summary": "",
        }
        for key in result:
            if key in parsed and parsed[key] is not None:
                result[key] = parsed[key]

        # 类型与范围修正
        try:
            result["macro_sentiment"] = float(result["macro_sentiment"])
        except Exception:
            result["macro_sentiment"] = 0.0
        result["macro_sentiment"] = max(-1.0, min(1.0, result["macro_sentiment"]))

        result["style_bias"] = str(result.get("style_bias", "neutral")).lower()
        if result["style_bias"] not in ("growth", "value", "defensive", "momentum", "neutral"):
            result["style_bias"] = "neutral"

        # 确保列表项为字符串
        for k in ("hot_sectors", "cold_sectors", "policy_themes", "risk_events", "veto_keywords"):
            result[k] = [str(x) for x in result.get(k, []) if x]

        # 个股提及规范化
        mentions = []
        for m in result.get("stock_mentions", []):
            if not isinstance(m, dict):
                continue
            code = str(m.get("code", "")).strip()
            name = str(m.get("name", "")).strip()
            if not code:
                continue
            try:
                sentiment = float(m.get("sentiment", 0))
            except Exception:
                sentiment = 0.0
            mentions.append({
                "code": code,
                "name": name,
                "sentiment": max(-1.0, min(1.0, sentiment)),
                "context": str(m.get("context", ""))[:80],
            })
        result["stock_mentions"] = mentions

        try:
            result["confidence"] = float(result.get("confidence", 0))
        except Exception:
            result["confidence"] = 0.0
        result["confidence"] = max(0.0, min(1.0, result["confidence"]))

        result["raw_summary"] = str(result.get("raw_summary", ""))[:500]

        return result
