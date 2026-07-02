"""
Tushare 公告接口单股测试（以 600519 贵州茅台为例）
运行前需配置 tushare token：将 config/.tushare_token.example 复制为 config/.tushare_token 并填入你的 token
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from utils.data_fetcher import DataFetcher
from strategies.news_sentiment import NewsSentimentAnalyzer
import yaml


def load_config(path="config/settings.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    config = load_config()
    fetcher = DataFetcher(data_source="tushare")

    print(f"测试股票: 600519 贵州茅台")
    print(f"数据源: tushare pro.anns_d")
    print("-" * 60)

    # 只取 tushare 官方公告
    news = fetcher.get_stock_news("600519", limit=10, source="tushare")
    print(f"获取公告条数: {len(news)}")
    if news.empty:
        print("未获取到公告。请检查：")
        print("  1. config/.tushare_token 是否存在且有效")
        print("  2. tushare 账户是否有 anns_d 接口权限")
        return

    print("\n前5条公告标题:")
    for i, row in news.head(5).iterrows():
        print(f"  {i+1}. [{row.get('发布时间', '')}] {row.get('标题', '')}")

    analyzer = NewsSentimentAnalyzer(config)
    result = analyzer.analyze("600519", news_df=news)

    print("\n情绪分析结果:")
    print(f"  新闻条数: {result['news_count']}")
    print(f"  负面条数: {result['negative_count']}")
    print(f"  正面条数: {result['positive_count']}")
    print(f"  综合评分: {result['score']}")
    print(f"  信号: {result['signals']}")
    print(f"  建议操作: {result['action']}")
    if result['action_reason']:
        print(f"  原因: {result['action_reason']}")

    if result['details'].get('negative_titles'):
        print("\n命中负面的标题:")
        for t in result['details']['negative_titles']:
            print(f"  - {t}")


if __name__ == "__main__":
    main()
