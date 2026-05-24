# [LOCAL VERSION DIFF]: 全新加入的檔案。執行完整歷史回測流程並輸出報表與績效統計的進入點腳本。
"""
run_full_backtest.py - Anti-Gravity 完整回測執行器
=====================================================
下載 2 年歷史數據，逐日模擬所有策略的交易，
輸出完整績效報告和 JSON 供 Dashboard 讀取。

用法:
  python run_full_backtest.py
"""

import os
import sys
import json
import warnings

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime

from scanner_base import AI_TECH_STOCKS, BENCHMARK, DASHBOARD_DIR
from backtest_engine import Portfolio, RiskManager, PerformanceAnalyzer, Signal
from backtest_strategies import ALL_STRATEGIES


# ============================================================
# 設定
# ============================================================
INITIAL_CAPITAL = 100_000       # 初始資金
RISK_PER_TRADE = 0.01           # 每筆風險 1%
MAX_POSITIONS = 6               # 最大同時持倉
COMMISSION = 1.0                # 每筆手續費 $1
SLIPPAGE = 0.0005               # 滑點 0.05%
LOOKBACK_PERIOD = '2y'          # 回測期間 2 年
MAX_DAILY_LOSS = 0.03           # 日最大虧損 3%
MAX_SECTOR_POSITIONS = 3        # 單一板塊最大 3 檔


def download_data(tickers: list, benchmark: str, period: str = '2y') -> tuple:
    """下載所有股票的日線數據"""
    all_tickers = [benchmark] + tickers
    print(f"  📥 正在下載 {len(all_tickers)} 檔股票 {period} 日線數據...")

    tickers_str = " ".join(all_tickers)
    df = yf.download(tickers_str, period=period, interval="1d",
                     progress=False, group_by='ticker')

    if df.empty:
        print("  ❌ 下載失敗！")
        return None, {}

    result = {}
    failed = []
    for ticker in all_tickers:
        try:
            if ticker in df.columns.levels[0]:
                ticker_df = df[ticker].dropna(how='all').copy()
                if not ticker_df.empty and len(ticker_df) > 60:
                    result[ticker] = ticker_df
                else:
                    failed.append(ticker)
            else:
                failed.append(ticker)
        except Exception:
            failed.append(ticker)

    if failed:
        print(f"  ⚠️ {len(failed)} 檔數據不足或失敗，已跳過: {', '.join(failed[:10])}")

    benchmark_df = result.pop(benchmark, None)
    print(f"  ✅ 成功載入 {len(result)} 檔股票 + 基準 {benchmark}")

    if benchmark_df is not None:
        date_range = f"{benchmark_df.index[0].strftime('%Y-%m-%d')} ~ {benchmark_df.index[-1].strftime('%Y-%m-%d')}"
        print(f"  📅 數據區間: {date_range} ({len(benchmark_df)} 個交易日)")

    return benchmark_df, result


def run_backtest():
    """主回測引擎"""
    print("\n" + "=" * 70)
    print("  Anti-Gravity 完整回測引擎")
    print("=" * 70)
    print(f"\n⚙️ 回測配置")
    print(f"  初始資金:        ${INITIAL_CAPITAL:,.0f}")
    print(f"  單筆風險:        {RISK_PER_TRADE*100:.1f}%")
    print(f"  最大同時持倉:    {MAX_POSITIONS}")
    print(f"  手續費:          ${COMMISSION}/筆")
    print(f"  滑點:            {SLIPPAGE*100:.2f}%")
    print(f"  回測區間:        {LOOKBACK_PERIOD}")
    print(f"  策略數:          {len(ALL_STRATEGIES)}")
    for s in ALL_STRATEGIES:
        print(f"    → {s.name}")
    print()

    # --- 1. 下載數據 ---
    benchmark_df, stocks_data = download_data(AI_TECH_STOCKS, BENCHMARK, LOOKBACK_PERIOD)
    if benchmark_df is None or not stocks_data:
        print("❌ 無法取得數據，回測中止。")
        return None

    # --- 2. 建立回測環境 ---
    portfolio = Portfolio(
        initial_capital=INITIAL_CAPITAL,
        commission_per_trade=COMMISSION,
        slippage_pct=SLIPPAGE,
    )
    risk_mgr = RiskManager(
        max_risk_per_trade=RISK_PER_TRADE,
        max_positions=MAX_POSITIONS,
        max_daily_loss_pct=MAX_DAILY_LOSS,
        max_sector_positions=MAX_SECTOR_POSITIONS,
    )

    # 建立策略 → 使用哪個 check_exit 的對照表
    strategy_map = {s.name: s for s in ALL_STRATEGIES}

    # --- 3. 取得所有交易日（以 benchmark 為準）---
    trading_dates = benchmark_df.index.tolist()
    total_days = len(trading_dates)

    # 從第 210 天開始（確保有足夠數據計算 200MA）
    start_idx = 210
    if start_idx >= total_days:
        print("❌ 數據天數不足（需至少 210 天），回測中止。")
        return None

    print(f"\n🚀 開始逐日模擬...")
    print(f"  模擬區間: {trading_dates[start_idx].strftime('%Y-%m-%d')} ~ {trading_dates[-1].strftime('%Y-%m-%d')}")
    print(f"  模擬天數: {total_days - start_idx}")
    print()

    # --- 4. 逐日模擬 ---
    pending_signals = []  # 等待次日執行的訊號
    signals_generated = 0
    signals_rejected = 0
    progress_interval = max(1, (total_days - start_idx) // 20)  # 每 5% 印一次進度

    for day_idx in range(start_idx, total_days):
        current_date = trading_dates[day_idx]
        current_date_d = current_date.date() if hasattr(current_date, 'date') else current_date

        # --- 4a. 執行前一天產生的待處理訊號 ---
        for signal in pending_signals:
            if signal.ticker not in stocks_data:
                continue

            ticker_df = stocks_data[signal.ticker]
            # 找到「今天」在這檔股票數據中的位置
            matching = ticker_df.index[ticker_df.index.date == current_date_d] \
                if hasattr(ticker_df.index[0], 'date') else \
                ticker_df.index[ticker_df.index == current_date]

            if len(matching) == 0:
                continue  # 今天沒有數據（停牌？）

            today_bar_idx = ticker_df.index.get_loc(matching[0])
            today_open = ticker_df['Open'].iloc[today_bar_idx]

            if pd.isna(today_open) or today_open <= 0:
                continue

            # 風控檢查
            can_trade, reject_reason = risk_mgr.can_trade(portfolio, signal)
            if not can_trade:
                signals_rejected += 1
                continue

            # 計算倉位
            shares = risk_mgr.calculate_position_size(
                portfolio, today_open, signal.stop_loss
            )
            if shares <= 0:
                signals_rejected += 1
                continue

            # 建倉
            pos = portfolio.open_position(signal, shares, today_open)
            if pos:
                signals_generated += 1

        pending_signals.clear()

        # --- 4b. 收集今天的價格 ---
        today_prices = {}
        for ticker, ticker_df in stocks_data.items():
            matching = ticker_df.index[ticker_df.index.date == current_date_d] \
                if hasattr(ticker_df.index[0], 'date') else \
                ticker_df.index[ticker_df.index == current_date]

            if len(matching) > 0:
                bar_idx = ticker_df.index.get_loc(matching[0])
                today_prices[ticker] = {
                    'Open': float(ticker_df['Open'].iloc[bar_idx]),
                    'High': float(ticker_df['High'].iloc[bar_idx]),
                    'Low': float(ticker_df['Low'].iloc[bar_idx]),
                    'Close': float(ticker_df['Close'].iloc[bar_idx]),
                    'Volume': float(ticker_df['Volume'].iloc[bar_idx]),
                }

        # --- 4c. 檢查持倉的出場條件 ---
        for ticker in list(portfolio.positions.keys()):
            pos = portfolio.positions[ticker]
            strategy = strategy_map.get(pos.strategy)
            if not strategy:
                continue

            if ticker not in stocks_data:
                continue

            ticker_df = stocks_data[ticker]
            matching = ticker_df.index[ticker_df.index.date == current_date_d] \
                if hasattr(ticker_df.index[0], 'date') else \
                ticker_df.index[ticker_df.index == current_date]

            if len(matching) == 0:
                continue

            bar_idx = ticker_df.index.get_loc(matching[0])
            exit_reason = strategy.check_exit(pos, bar_idx, ticker_df)

            if exit_reason:
                exit_price = today_prices[ticker]['Close']
                # 如果是停損觸發，用停損價而非收盤價
                if '停損' in exit_reason and today_prices[ticker]['Low'] <= pos.stop_loss:
                    exit_price = pos.stop_loss

                portfolio.close_position(ticker, exit_price,
                                         current_date_d, exit_reason)

        # --- 4d. 更新所有持倉 ---
        portfolio.update_daily(current_date_d, today_prices)

        # --- 4e. 收盤後掃描新訊號（隔天執行）---
        new_signals = []
        for ticker, ticker_df in stocks_data.items():
            matching = ticker_df.index[ticker_df.index.date == current_date_d] \
                if hasattr(ticker_df.index[0], 'date') else \
                ticker_df.index[ticker_df.index == current_date]

            if len(matching) == 0:
                continue

            bar_idx = ticker_df.index.get_loc(matching[0])

            for strategy in ALL_STRATEGIES:
                signal = strategy.scan(bar_idx, ticker, ticker_df, benchmark_df)
                if signal:
                    new_signals.append(signal)

        # 排序：優先級高的先執行
        new_signals.sort(key=lambda s: s.priority, reverse=True)
        pending_signals = new_signals

        # --- 進度顯示 ---
        progress = day_idx - start_idx
        if progress % progress_interval == 0:
            pct = progress / (total_days - start_idx) * 100
            eq = portfolio.equity
            n_pos = portfolio.num_positions
            n_trades = len(portfolio.closed_trades)
            print(f"  [{pct:5.1f}%] {current_date_d} | 淨值: ${eq:>10,.2f} | "
                  f"持倉: {n_pos} | 已平倉: {n_trades}")

    # --- 5. 回測結束，強制平倉 ---
    last_date = trading_dates[-1]
    last_date_d = last_date.date() if hasattr(last_date, 'date') else last_date

    # 收集最後一天的價格
    final_prices = {}
    for ticker, ticker_df in stocks_data.items():
        matching = ticker_df.index[ticker_df.index.date == last_date_d] \
            if hasattr(ticker_df.index[0], 'date') else \
            ticker_df.index[ticker_df.index == last_date]
        if len(matching) > 0:
            bar_idx = ticker_df.index.get_loc(matching[0])
            final_prices[ticker] = {
                'Open': float(ticker_df['Open'].iloc[bar_idx]),
                'High': float(ticker_df['High'].iloc[bar_idx]),
                'Low': float(ticker_df['Low'].iloc[bar_idx]),
                'Close': float(ticker_df['Close'].iloc[bar_idx]),
            }

    remaining = list(portfolio.positions.keys())
    if remaining:
        print(f"\n  ⏹️ 回測結束，強制平倉 {len(remaining)} 檔: {', '.join(remaining)}")
        portfolio.force_close_all(last_date_d, final_prices)

    print(f"\n  📊 訊號統計: 產生 {signals_generated} 筆成交, {signals_rejected} 筆被風控拒絕")

    # --- 6. 績效分析 ---
    analyzer = PerformanceAnalyzer(portfolio)
    report = analyzer.print_report()

    # --- 7. 輸出 JSON ---
    if report and 'error' not in report:
        output = {
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'config': {
                'initial_capital': INITIAL_CAPITAL,
                'risk_per_trade': RISK_PER_TRADE,
                'max_positions': MAX_POSITIONS,
                'commission': COMMISSION,
                'slippage': SLIPPAGE,
                'lookback': LOOKBACK_PERIOD,
                'strategies': [s.name for s in ALL_STRATEGIES],
                'tickers_count': len(stocks_data),
            },
            **report,
        }

        os.makedirs(DASHBOARD_DIR, exist_ok=True)
        filepath = os.path.join(DASHBOARD_DIR, 'full_backtest.json')
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2,
                      default=lambda x: str(x) if not isinstance(x, (int, float, bool, type(None))) else x)

        print(f"\n  💾 回測結果已寫入: {filepath}")
        print(f"     (共 {len(report.get('trades', []))} 筆交易, "
              f"{len(report.get('equity_curve', []))} 天淨值曲線)")
    else:
        print("\n  ⚠️ 回測未產生有效結果。")

    return report


# ============================================================
# 主程式
# ============================================================
if __name__ == '__main__':
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🏁 Anti-Gravity 完整回測開始\n")
    start_time = datetime.now()

    result = run_backtest()

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⏱️ 總耗時: {elapsed:.1f} 秒")

    if result and 'summary' in result:
        s = result['summary']
        print(f"\n{'='*40}")
        print(f"  最終結論:")
        if s['sharpe_ratio'] >= 1.5 and s['profit_factor'] >= 1.5 and s['win_rate_pct'] >= 50:
            print(f"  ✅ 策略通過基本門檻！可以考慮 Paper Trading")
        elif s['sharpe_ratio'] >= 1.0 and s['profit_factor'] >= 1.2:
            print(f"  ⚠️ 策略表現尚可，但需要進一步優化")
        else:
            print(f"  ❌ 策略尚未達到可交易標準，需要重新調整參數")
        print(f"{'='*40}\n")
