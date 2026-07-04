"""
AI 简报 + 真实行情数据演示脚本

说明：
- 使用真实 A 股行情数据（akshare/tushare）。
- AI 简报层使用固定模拟信号，不调用真实 LLM，便于无 API key 时验证流程。
- 运行后会生成 report_YYYY-MM-DD.md 到 reports/output/。

如需使用真实 Kimi/Claude/OpenAI，请：
1. 将 config/.llm_api_key.example 复制为 config/.llm_api_key
2. 填入你的 API key
3. 直接运行 python main.py
"""
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])

from strategies.ai_briefing import AIBriefingAnalyzer
import main


MOCK_AI_SIGNALS = {
    "macro_sentiment": 0.5,
    "hot_sectors": ["半导体", "电力", "通信设备"],
    "cold_sectors": ["房地产", "教育"],
    "policy_themes": ["新质生产力", "设备更新"],
    "risk_events": ["中美关税"],
    "style_bias": "growth",
    "stock_mentions": [
        {"code": "600519", "name": "贵州茅台", "sentiment": 0.3, "context": "业绩稳健"},
        {"code": "000001", "name": "平安银行", "sentiment": -0.3, "context": "息差承压"},
    ],
    "veto_keywords": [],
    "confidence": 0.75,
    "raw_summary": "模拟：成长风格占优，半导体/电力受政策关注，房地产承压。",
}


class MockAIBriefingAnalyzer(AIBriefingAnalyzer):
    def analyze(self, fetcher):
        return self._normalize(MOCK_AI_SIGNALS.copy())


def patched_load_config(path="config/settings.yaml"):
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["data_source"] = "auto"  # 无 tushare token 时自动降级到 akshare
    cfg.setdefault("ai_briefing", {})["enabled"] = True
    return cfg


if __name__ == "__main__":
    # 用模拟 AI 分析器替换真实分析器，并允许数据源自动降级
    main.AIBriefingAnalyzer = MockAIBriefingAnalyzer
    main.load_config = patched_load_config
    print("[DEMO] 使用真实行情数据 + 模拟 AI 信号运行选股流程\n")
    main.main(use_mock=False)
