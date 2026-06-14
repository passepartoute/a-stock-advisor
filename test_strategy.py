"""快速策略验证脚本"""
import sys, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

import yaml
from utils.data_fetcher import DataFetcher
from strategies.fundamental import FundamentalScreener
from strategies.technical import TechnicalAnalyzer
from strategies.signal_engine_v2 import SignalEngineV2
from strategies.risk_manager import RiskManager

def main():
    with open('config/settings.yaml', 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)

    fetcher = DataFetcher()
    engine = SignalEngineV2(cfg)
    risk_mgr = RiskManager(cfg.get('risk_management'))

    print('测试1: 获取股票列表...')
    spot = fetcher.get_stock_list()
    print(f'  获取到 {len(spot)} 只股票')
    if spot.empty:
        print('  [错误] 无法获取股票列表，检查网络')
        return

    print('\n测试2: 基本面筛选...')
    screener = FundamentalScreener(spot, cfg)
    candidates = screener.screen()
    print(f'  通过筛选: {len(candidates)} 只')

    print('\n测试3: 单股分析...')
    test_codes = ['600519', '000001', '600036']
    for code in test_codes:
        print(f'\n  --- {code} ---')
        hist = fetcher.get_hist_data(code, days=300)
        if hist.empty:
            print(f'    无历史数据')
            continue

        row = spot[spot['代码'] == code]
        if row.empty:
            print(f'    不在列表中')
            continue

        f_score = screener.score(row.iloc[0])
        print(f'    基本面: score={f_score["score"]}, signals={f_score["signals"]}')

        tech = TechnicalAnalyzer(hist, cfg.get('technical')).score()
        print(f'    技术面: score={tech["score"]}, signals={tech["signals"]}')

        momentum = engine.calculate_momentum(hist)
        print(f'    动量: score={momentum["score"]}, r5={momentum["r5"]}%')

        result = engine.combine(f_score, tech, momentum)
        print(f'    综合: score={result["total_score"]}, advice={result["advice"]}')

        risk = risk_mgr.get_risk_advice(code, '', hist.iloc[-1]['收盘'], result['total_score'], tech.get('details', {}))
        print(f'    风控: 止损={risk["stop_loss_price"]}, 目标={risk["target_price"]}, 仓位={risk["position_pct"]}%')

    print('\n全部测试通过')

if __name__ == '__main__':
    main()
