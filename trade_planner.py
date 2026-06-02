"""
trade_planner.py - Anti-Gravity 每日交易計劃產生器
====================================================
盤前執行一次，整合所有策略掃描，輸出精確的進出場指令。

功能：
  1. 判定大盤環境（廣度 + QQQ 均線）
  2. 掃描 TD9 下跌竭盡 / 均線回測 / 動能突破
  3. 計算精確倉位：進場價、停損價、股數、風險金額
  4. 生成 Schwab 可操作的下單指令
  5. 輸出 trade_plan.json 供 Dashboard 讀取

用法：
  python trade_planner.py
"""

import sys
import os
import json
import warnings

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime

from scanner_base import (
    AI_TECH_STOCKS, BENCHMARK, DASHBOARD_DIR, TOTAL_CAPITAL,
    calc_atr, calculate_position, get_market_regime, save_dashboard_data,
)


# ============================================================
# TD9 計算
# ============================================================
def calc_td_list(df):
    """計算整個歷史的 TD Sequential 數列"""
    td_list = [0] * len(df)
    td_count = 0
    for i in range(4, len(df)):
        cur = float(df['Close'].iloc[i])
        prior = float(df['Close'].iloc[i - 4])
        if cur > prior:
            td_count = td_count + 1 if td_count >= 0 else 1
        elif cur < prior:
            td_count = td_count - 1 if td_count <= 0 else -1
        else:
            td_count = 0
        td_list[i] = td_count
    return td_list


def calc_rsi(prices, period=14):
    """計算 RSI"""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


# ============================================================
# 策略掃描
# ============================================================
def scan_td9_signals(stocks_data, regime):
    """掃描 TD9 下跌竭盡買入 + 上漲竭盡賣出"""
    buy_signals = []
    sell_signals = []
    risk_pct = regime['risk_per_trade']

    for ticker, df in stocks_data.items():
        if len(df) < 200:
            continue

        td_list = calc_td_list(df)
        td_val = td_list[-1]

        # --- 賣出竭盡 (TD8/TD9) ---
        if td_val in [8, 9]:
            sell_signals.append({
                'ticker': ticker,
                'strategy': 'td9_sell',
                'td_count': td_val,
                'close': round(float(df['Close'].iloc[-1]), 2),
                'message': f"連漲 {td_val} 天，上漲竭盡，考慮減碼或設移動停損",
            })

        # --- 買入竭盡 (TD-8/TD-9) ---
        if td_val not in [-8, -9]:
            continue

        close = float(df['Close'].iloc[-1])
        low = float(df['Low'].iloc[-1])

        # 濾網 1: 股價 > 200MA
        ma200 = float(df['Close'].rolling(200).mean().iloc[-1])
        if pd.isna(ma200) or close < ma200:
            continue

        # 濾網 2: RSI
        rsi_series = calc_rsi(df['Close'], 14)
        rsi = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0

        # 底背離檢測
        is_price_new_low = close <= float(df['Close'].rolling(15).min().iloc[-1])
        is_rsi_not_new_low = rsi > float(rsi_series.rolling(15).min().iloc[-1]) if not pd.isna(rsi_series.rolling(15).min().iloc[-1]) else False
        is_divergence = is_price_new_low and is_rsi_not_new_low

        if rsi > 35 and not is_divergence:
            continue

        # ATR 停損
        atr_series = calc_atr(df['High'], df['Low'], df['Close'])
        atr = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else close * 0.03

        stop_by_low = low
        stop_by_atr = close - 1.5 * atr
        stop_loss = max(stop_by_low, stop_by_atr)
        if stop_loss >= close * 0.99:
            stop_loss = close * 0.97

        # 目標價
        stop_dist = close - stop_loss
        target_1r = close + stop_dist
        target_2r = close + 2 * stop_dist

        # 倉位計算
        shares, cost, actual_risk = calculate_position(close, stop_loss, custom_risk_pct=risk_pct)

        filters_passed = [f">200MA (${ma200:.2f})"]
        if rsi <= 35:
            filters_passed.append(f"RSI={rsi:.1f} ≤ 35")
        if is_divergence:
            filters_passed.append("RSI 底背離 ⚡")
        filters_passed.append(f"廣度={regime['breadth']:.1f}%")

        # 限價 = 昨收 + 0.5% (給開盤滑點留空間)
        limit_price = round(close * 1.005, 2)

        buy_signals.append({
            'ticker': ticker,
            'strategy': 'td9_buy',
            'strategy_label': f"TD{td_val} 下跌竭盡",
            'signal_date': str(df.index[-1].date()) if hasattr(df.index[-1], 'date') else str(df.index[-1]),
            'close_price': round(close, 2),
            'entry_price': limit_price,
            'entry_note': f"限價 ${limit_price} 以下買入",
            'stop_loss': round(stop_loss, 2),
            'stop_pct': round((stop_loss / close - 1) * 100, 1),
            'target_1r': round(target_1r, 2),
            'target_2r': round(target_2r, 2),
            'shares': shares,
            'position_cost': round(cost, 2),
            'risk_amount': round(actual_risk, 2),
            'risk_pct_of_capital': round(actual_risk / TOTAL_CAPITAL * 100, 1) if TOTAL_CAPITAL > 0 else 0,
            'reason': f"TD{td_val} 下跌竭盡, 高於200MA (${ma200:.0f}), RSI={rsi:.1f}",
            'filters_passed': filters_passed,
            'priority': abs(td_val),
            'atr': round(atr, 2),
            'rsi': round(rsi, 1),
            'schwab_order': _gen_schwab_order(ticker, shares, limit_price, stop_loss),
        })

    return buy_signals, sell_signals


def scan_ma_signals(stocks_data, market_df, regime):
    """掃描均線回測買入訊號"""
    buy_signals = []
    risk_pct = regime['risk_per_trade']

    for ticker, df in stocks_data.items():
        if len(df) < 200:
            continue

        close = float(df['Close'].iloc[-1])
        high = float(df['High'].iloc[-1])
        low = float(df['Low'].iloc[-1])

        # 濾網: 200MA
        ma200 = float(df['Close'].rolling(200).mean().iloc[-1])
        if pd.isna(ma200) or close < ma200:
            continue

        # 計算均線
        ma10 = float(df['Close'].rolling(10).mean().iloc[-1])
        ma20 = float(df['Close'].rolling(20).mean().iloc[-1])
        ma60 = float(df['Close'].rolling(60).mean().iloc[-1]) if len(df) >= 60 else float('nan')

        # 均線多頭排列
        if ma10 < ma20 * 0.99:
            continue

        # 觸碰檢測
        touched_ma = None
        touched_ma_val = None
        for w, ma_val in [(10, ma10), (20, ma20), (60, ma60)]:
            if pd.isna(ma_val):
                continue
            is_touching = (low <= ma_val <= high) or (ma_val < low <= ma_val * 1.005)
            if is_touching and close >= ma_val * 0.99:
                touched_ma = f"{w}MA"
                touched_ma_val = ma_val
                break

        if touched_ma is None:
            continue

        # 量縮回檔
        vol_20ma = float(df['Volume'].rolling(20).mean().iloc[-1])
        if vol_20ma > 0 and float(df['Volume'].iloc[-1]) >= vol_20ma:
            continue

        # 相對強度
        if market_df is not None and len(market_df) >= 20:
            stock_ret_20 = (close - float(df['Close'].iloc[-20])) / float(df['Close'].iloc[-20])
            qqq_ret_20 = (float(market_df['Close'].iloc[-1]) - float(market_df['Close'].iloc[-20])) / float(market_df['Close'].iloc[-20])
            rs_score = stock_ret_20 - qqq_ret_20
            if rs_score <= 0:
                continue
        else:
            rs_score = 0

        # ATR 停損
        atr_series = calc_atr(df['High'], df['Low'], df['Close'])
        atr = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else close * 0.03

        stop_by_atr = close - 1.5 * atr
        stop_by_ma = touched_ma_val * 0.99 if touched_ma_val else close * 0.97
        stop_loss = max(stop_by_atr, stop_by_ma)
        if stop_loss >= close * 0.99:
            stop_loss = close * 0.97

        stop_dist = close - stop_loss
        target_1r = close + stop_dist
        target_2r = close + 2 * stop_dist

        shares, cost, actual_risk = calculate_position(close, stop_loss, custom_risk_pct=risk_pct)

        limit_price = round(close * 1.005, 2)

        filters_passed = [
            f">200MA (${ma200:.2f})",
            f"觸碰 {touched_ma} (${touched_ma_val:.2f})",
            "量縮回檔 ✅",
            f"RS > QQQ +{rs_score*100:.1f}%",
        ]

        buy_signals.append({
            'ticker': ticker,
            'strategy': 'ma_pullback',
            'strategy_label': f"均線回測 {touched_ma}",
            'signal_date': str(df.index[-1].date()) if hasattr(df.index[-1], 'date') else str(df.index[-1]),
            'close_price': round(close, 2),
            'entry_price': limit_price,
            'entry_note': f"限價 ${limit_price} 以下買入",
            'stop_loss': round(stop_loss, 2),
            'stop_pct': round((stop_loss / close - 1) * 100, 1),
            'target_1r': round(target_1r, 2),
            'target_2r': round(target_2r, 2),
            'shares': shares,
            'position_cost': round(cost, 2),
            'risk_amount': round(actual_risk, 2),
            'risk_pct_of_capital': round(actual_risk / TOTAL_CAPITAL * 100, 1) if TOTAL_CAPITAL > 0 else 0,
            'reason': f"多頭回測 {touched_ma} (${touched_ma_val:.2f}), >200MA, 量縮, RS強",
            'filters_passed': filters_passed,
            'priority': 1.5 if touched_ma == '60MA' else 1.0,
            'atr': round(atr, 2),
            'schwab_order': _gen_schwab_order(ticker, shares, limit_price, stop_loss),
        })

    return buy_signals


def scan_momentum_signals(stocks_data, regime):
    """掃描動能突破買入訊號"""
    buy_signals = []
    risk_pct = regime['risk_per_trade']

    for ticker, df in stocks_data.items():
        if len(df) < 84:
            continue

        close = float(df['Close'].iloc[-1])
        high = float(df['High'].iloc[-1])
        low = float(df['Low'].iloc[-1])
        volume = float(df['Volume'].iloc[-1])

        # 3M 報酬
        close_3m = float(df['Close'].iloc[-63])
        ret_3m = (close / close_3m - 1) * 100
        if ret_3m < 20:
            continue

        # > 50MA
        ma50 = float(df['Close'].rolling(50).mean().iloc[-1])
        if pd.isna(ma50) or close < ma50:
            continue

        # 整理範圍
        recent_high = float(df['High'].iloc[-10:].max())
        recent_low = float(df['Low'].iloc[-10:].min())
        consolidation = (recent_high / recent_low - 1) * 100
        if consolidation > 12:
            continue

        # 突破前高
        prev_20d_high = float(df['High'].iloc[-21:-1].max())
        if high <= prev_20d_high:
            continue

        # 爆量
        avg_vol_20 = float(df['Volume'].iloc[-21:-1].mean())
        if avg_vol_20 > 0 and volume < avg_vol_20 * 1.5:
            continue

        # ATR 停損
        atr_series = calc_atr(df['High'], df['Low'], df['Close'])
        atr = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else close * 0.03

        stop_by_low = low
        stop_by_atr = close - 1.5 * atr
        stop_loss = max(stop_by_low, stop_by_atr)
        if stop_loss >= close * 0.99:
            stop_loss = close * 0.97

        stop_dist = close - stop_loss
        target_1r = close + stop_dist
        target_2r = close + 2 * stop_dist

        shares, cost, actual_risk = calculate_position(close, stop_loss, custom_risk_pct=risk_pct)

        limit_price = round(close * 1.005, 2)
        vol_ratio = volume / avg_vol_20 if avg_vol_20 > 0 else 0

        filters_passed = [
            f"3M動能 +{ret_3m:.0f}%",
            f">50MA (${ma50:.2f})",
            f"整理 {consolidation:.1f}% < 12%",
            f"突破 20日高 ${prev_20d_high:.2f}",
            f"爆量 {vol_ratio:.1f}x",
        ]

        buy_signals.append({
            'ticker': ticker,
            'strategy': 'momentum_breakout',
            'strategy_label': '動能突破',
            'signal_date': str(df.index[-1].date()) if hasattr(df.index[-1], 'date') else str(df.index[-1]),
            'close_price': round(close, 2),
            'entry_price': limit_price,
            'entry_note': f"限價 ${limit_price} 以下買入",
            'stop_loss': round(stop_loss, 2),
            'stop_pct': round((stop_loss / close - 1) * 100, 1),
            'target_1r': round(target_1r, 2),
            'target_2r': round(target_2r, 2),
            'shares': shares,
            'position_cost': round(cost, 2),
            'risk_amount': round(actual_risk, 2),
            'risk_pct_of_capital': round(actual_risk / TOTAL_CAPITAL * 100, 1) if TOTAL_CAPITAL > 0 else 0,
            'reason': f"動能突破 (3M +{ret_3m:.0f}%, 整理 {consolidation:.1f}%, 量 {vol_ratio:.1f}x)",
            'filters_passed': filters_passed,
            'priority': ret_3m / 10,
            'atr': round(atr, 2),
            'schwab_order': _gen_schwab_order(ticker, shares, limit_price, stop_loss),
        })

    return buy_signals


# ============================================================
# Schwab 下單指令生成
# ============================================================
def _gen_schwab_order(ticker, shares, limit_price, stop_loss):
    """生成人類可讀的 Schwab 下單指令"""
    stop_limit = round(stop_loss - 0.50, 2)  # 停損限價留 $0.50 滑點空間
    return {
        'buy_order': f"BUY {shares} shares {ticker} @ LIMIT ${limit_price:.2f} (Day Order)",
        'stop_order': f"SELL {shares} shares {ticker} @ STOP ${stop_loss:.2f} LIMIT ${stop_limit:.2f} (GTC)",
        'summary': f"買 {shares} 股 {ticker}，限價 ${limit_price:.2f}，停損 ${stop_loss:.2f}",
    }


# ============================================================
# 主函式
# ============================================================
def generate_trade_plan():
    """主函式：產出完整的每日交易計劃"""
    print(f"\n{'='*55}")
    print(f"  Anti-Gravity 每日交易計劃產生器")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  本金: ${TOTAL_CAPITAL:,}")
    print(f"{'='*55}\n")

    # 1. 判定大盤環境
    print("🌐 Step 1: 判定大盤環境...")
    regime = get_market_regime()
    print(f"   → {regime['message']}\n")

    # 2. 下載數據
    print("📥 Step 2: 下載股票日線數據...")
    all_tickers = [BENCHMARK] + AI_TECH_STOCKS
    tickers_str = " ".join(all_tickers)
    df = yf.download(tickers_str, period="1y", interval="1d", progress=False, group_by='ticker')

    if df.empty:
        print("❌ 數據下載失敗！")
        return None

    stocks_data = {}
    market_df = None
    for ticker in all_tickers:
        try:
            if ticker in df.columns.levels[0]:
                ticker_df = df[ticker].dropna(how='all')
                if not ticker_df.empty and len(ticker_df) > 60:
                    if ticker == BENCHMARK:
                        market_df = ticker_df
                    else:
                        stocks_data[ticker] = ticker_df
        except Exception:
            pass

    print(f"   ✅ 成功載入 {len(stocks_data)} 檔股票\n")

    # 3. 掃描策略
    all_buy_signals = []
    all_sell_signals = []

    if regime['regime'] != 'no_risk':
        print("🔍 Step 3: 掃描策略訊號...")

        # TD9
        print("   → TD9 下跌竭盡...")
        td9_buys, td9_sells = scan_td9_signals(stocks_data, regime)
        all_buy_signals.extend(td9_buys)
        all_sell_signals.extend(td9_sells)
        print(f"     買入: {len(td9_buys)} 檔 | 賣出竭盡: {len(td9_sells)} 檔")

        # 均線回測
        print("   → 均線回測...")
        ma_buys = scan_ma_signals(stocks_data, market_df, regime)
        all_buy_signals.extend(ma_buys)
        print(f"     買入: {len(ma_buys)} 檔")

        # 動能突破
        print("   → 動能突破...")
        mom_buys = scan_momentum_signals(stocks_data, regime)
        all_buy_signals.extend(mom_buys)
        print(f"     買入: {len(mom_buys)} 檔")
    else:
        print("🛑 Step 3: 大盤環境為防守，跳過策略掃描。")
        # 仍然掃描賣出竭盡
        _, td9_sells = scan_td9_signals(stocks_data, regime)
        all_sell_signals.extend(td9_sells)

    # 排序: 優先級高的在前
    all_buy_signals.sort(key=lambda s: s['priority'], reverse=True)

    # 去重 (同一標的只保留最高優先級)
    seen = set()
    unique_buys = []
    for s in all_buy_signals:
        if s['ticker'] not in seen:
            seen.add(s['ticker'])
            unique_buys.append(s)
    all_buy_signals = unique_buys

    # 4. 組裝輸出
    plan = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'capital': TOTAL_CAPITAL,
        'market_regime': regime,
        'buy_signals': all_buy_signals,
        'sell_signals': all_sell_signals,
        'total_buy_signals': len(all_buy_signals),
        'total_sell_signals': len(all_sell_signals),
    }

    # 5. 寫入 JSON
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    filepath = os.path.join(DASHBOARD_DIR, 'trade_plan.json')
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    # 6. 印出摘要
    print(f"\n{'='*55}")
    print(f"📋 交易計劃摘要")
    print(f"{'='*55}")
    print(f"  大盤環境: {regime['breadth_label']} (廣度 {regime['breadth']:.1f}%)")
    print(f"  QQQ: ${regime['qqq_price']} | 20MA: ${regime['qqq_20ma']} | 50MA: ${regime['qqq_50ma']}")
    print(f"  交易檔位: 單筆風險 {regime['risk_per_trade']*100:.0f}% | 最多 {regime['max_positions']} 檔")

    if all_buy_signals:
        print(f"\n  🟢 買入訊號 ({len(all_buy_signals)} 檔):")
        for s in all_buy_signals:
            print(f"     {s['ticker']:6s} | {s['strategy_label']:12s} | 進場 ${s['entry_price']} | 停損 ${s['stop_loss']} ({s['stop_pct']}%) | {s['shares']} 股 ${s['position_cost']:,.0f} | 風險 ${s['risk_amount']:.0f}")
    else:
        print(f"\n  ⚪ 今日無買入訊號。")

    if all_sell_signals:
        print(f"\n  🔴 賣出竭盡 ({len(all_sell_signals)} 檔):")
        for s in all_sell_signals:
            print(f"     {s['ticker']:6s} | TD+{s['td_count']} | ${s['close']} | {s['message']}")

    print(f"\n  💾 已寫入: {filepath}")
    print(f"{'='*55}\n")

    return plan


if __name__ == '__main__':
    generate_trade_plan()
