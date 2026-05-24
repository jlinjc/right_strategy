# [LOCAL VERSION DIFF]: 全新加入的檔案。用於最佳化風險參數（例如停損比例、部位大小）的測試腳本。
"""
optimize_risk.py - 單筆風險比例最佳化回測
"""
import sys
import warnings
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from datetime import datetime

from scanner_base import AI_TECH_STOCKS, BENCHMARK
from backtest_engine import Portfolio, RiskManager, PerformanceAnalyzer
from backtest_strategies import ALL_STRATEGIES
from run_full_backtest import download_data

def optimize_risk():
    INITIAL_CAPITAL = 10000
    MAX_POSITIONS = 6
    COMMISSION = 1.0
    SLIPPAGE = 0.0005
    LOOKBACK_PERIOD = '2y'
    MAX_DAILY_LOSS = 0.03
    MAX_SECTOR_POSITIONS = 3
    
    # 1. 下載數據 (只下載一次)
    print("📥 正在下載歷史數據...")
    benchmark_df, stocks_data = download_data(AI_TECH_STOCKS, BENCHMARK, LOOKBACK_PERIOD)
    if benchmark_df is None or not stocks_data:
        return
        
    trading_dates = benchmark_df.index.tolist()
    total_days = len(trading_dates)
    start_idx = 210
    
    # 預先計算好每天的訊號，加速回測
    print("🔍 預先掃描所有策略訊號...")
    all_signals = {}  # {date: [signals]}
    strategy_map = {s.name: s for s in ALL_STRATEGIES}
    
    for day_idx in range(start_idx, total_days):
        current_date = trading_dates[day_idx]
        current_date_d = current_date.date() if hasattr(current_date, 'date') else current_date
        
        signals_today = []
        for ticker, ticker_df in stocks_data.items():
            matching = ticker_df.index[ticker_df.index.date == current_date_d] if hasattr(ticker_df.index[0], 'date') else ticker_df.index[ticker_df.index == current_date]
            if len(matching) == 0: continue
            
            bar_idx = ticker_df.index.get_loc(matching[0])
            for strategy in ALL_STRATEGIES:
                signal = strategy.scan(bar_idx, ticker, ticker_df, benchmark_df)
                if signal:
                    signals_today.append(signal)
        
        signals_today.sort(key=lambda s: s.priority, reverse=True)
        all_signals[current_date_d] = signals_today

    # 2. 迴圈測試 1% ~ 10%
    results = []
    print("\n🚀 開始測試不同的風險比例 (初始資金 $10,000)...")
    
    for risk_pct in range(1, 11):
        risk_per_trade = risk_pct / 100.0
        
        portfolio = Portfolio(initial_capital=INITIAL_CAPITAL, commission_per_trade=COMMISSION, slippage_pct=SLIPPAGE)
        risk_mgr = RiskManager(max_risk_per_trade=risk_per_trade, max_positions=MAX_POSITIONS, 
                               max_daily_loss_pct=MAX_DAILY_LOSS, max_sector_positions=MAX_SECTOR_POSITIONS)
        
        pending_signals = []
        
        for day_idx in range(start_idx, total_days):
            current_date = trading_dates[day_idx]
            current_date_d = current_date.date() if hasattr(current_date, 'date') else current_date
            
            # a. 執行前一天訊號
            for signal in pending_signals:
                if signal.ticker not in stocks_data: continue
                ticker_df = stocks_data[signal.ticker]
                matching = ticker_df.index[ticker_df.index.date == current_date_d] if hasattr(ticker_df.index[0], 'date') else ticker_df.index[ticker_df.index == current_date]
                if len(matching) == 0: continue
                
                today_bar_idx = ticker_df.index.get_loc(matching[0])
                today_open = ticker_df['Open'].iloc[today_bar_idx]
                if pd.isna(today_open) or today_open <= 0: continue
                
                can_trade, _ = risk_mgr.can_trade(portfolio, signal)
                if not can_trade: continue
                
                shares = risk_mgr.calculate_position_size(portfolio, today_open, signal.stop_loss)
                if shares > 0:
                    portfolio.open_position(signal, shares, today_open)
                    
            pending_signals.clear()
            
            # b. 收集價格
            today_prices = {}
            for ticker, ticker_df in stocks_data.items():
                matching = ticker_df.index[ticker_df.index.date == current_date_d] if hasattr(ticker_df.index[0], 'date') else ticker_df.index[ticker_df.index == current_date]
                if len(matching) > 0:
                    bar_idx = ticker_df.index.get_loc(matching[0])
                    today_prices[ticker] = {
                        'Open': float(ticker_df['Open'].iloc[bar_idx]),
                        'High': float(ticker_df['High'].iloc[bar_idx]),
                        'Low': float(ticker_df['Low'].iloc[bar_idx]),
                        'Close': float(ticker_df['Close'].iloc[bar_idx]),
                    }
            
            # c. 檢查出場
            for ticker in list(portfolio.positions.keys()):
                pos = portfolio.positions[ticker]
                strategy = strategy_map.get(pos.strategy)
                if ticker not in stocks_data: continue
                ticker_df = stocks_data[ticker]
                matching = ticker_df.index[ticker_df.index.date == current_date_d] if hasattr(ticker_df.index[0], 'date') else ticker_df.index[ticker_df.index == current_date]
                if len(matching) == 0: continue
                
                bar_idx = ticker_df.index.get_loc(matching[0])
                exit_reason = strategy.check_exit(pos, bar_idx, ticker_df)
                if exit_reason:
                    exit_price = today_prices[ticker]['Close']
                    if '停損' in exit_reason and today_prices[ticker]['Low'] <= pos.stop_loss:
                        exit_price = pos.stop_loss
                    portfolio.close_position(ticker, exit_price, current_date_d, exit_reason)
            
            # d. 每日更新
            portfolio.update_daily(current_date_d, today_prices)
            
            # e. 獲取今天的訊號留到明天執行
            if current_date_d in all_signals:
                pending_signals = list(all_signals[current_date_d])

        # 強制平倉
        last_date = trading_dates[-1]
        last_date_d = last_date.date() if hasattr(last_date, 'date') else last_date
        final_prices = {}
        for ticker, ticker_df in stocks_data.items():
            matching = ticker_df.index[ticker_df.index.date == last_date_d] if hasattr(ticker_df.index[0], 'date') else ticker_df.index[ticker_df.index == last_date]
            if len(matching) > 0:
                bar_idx = ticker_df.index.get_loc(matching[0])
                final_prices[ticker] = {'Close': float(ticker_df['Close'].iloc[bar_idx])}
        portfolio.force_close_all(last_date_d, final_prices)
        
        # 分析
        analyzer = PerformanceAnalyzer(portfolio)
        res = analyzer.analyze()
        if 'error' not in res:
            s = res['summary']
            results.append({
                'Risk': risk_pct,
                'Final_Equity': s['final_equity'],
                'CAGR': s['cagr_pct'],
                'Sharpe': s['sharpe_ratio'],
                'MDD': s['max_drawdown_pct'],
                'Trades': s['total_trades']
            })
            print(f"  ✓ 測試 {risk_pct:2d}% 完成: 淨值 ${s['final_equity']:,.0f} | 總報酬 {s['total_return_pct']:>6.1f}% | MDD {s['max_drawdown_pct']:>6.1f}%")

    print("\n" + "="*65)
    print(" 🎯 單筆風險 (Risk Per Trade) 最佳化報告 (本金 $10,000)")
    print("="*65)
    print(" 風險 |  最終淨值  |  年化報酬  | Sharpe | 最大回撤 (MDD) | 總交易次數")
    print("-" * 65)
    for r in results:
        best_mdd = "⚠️" if r['MDD'] < -25 else "✅"
        best_sharpe = "⭐" if r['Sharpe'] >= 1.5 else ""
        print(f"  {r['Risk']:2d}% | ${r['Final_Equity']:>9,.0f} | {r['CAGR']:>9.1f}% | {r['Sharpe']:>6.2f}{best_sharpe} | {r['MDD']:>10.1f}% {best_mdd} | {r['Trades']:>6d}")
    print("="*65)
    
if __name__ == "__main__":
    optimize_risk()
