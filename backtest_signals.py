"""
backtest_signals.py - 個股訊號勝率回測引擎
==============================================
下載每檔 AI/Tech 股票近 1 年日線資料，
掃描所有歷史上觸發 TD8/TD9 竭盡訊號和均線回測的時間點，
計算觸發後 1 / 3 / 5 / 10 天的報酬率、勝率與期望值。
輸出 JSON 供 Web Dashboard 讀取。
"""

import json
import os
import numpy as np
import yfinance as yf
import pandas as pd
from datetime import datetime
from scanner_base import AI_TECH_STOCKS, BENCHMARK, DASHBOARD_DIR

FORWARD_DAYS = [1, 3, 5, 10]


def calc_td_series(df):
    """計算每一根 K 棒的 TD Sequential 計數值"""
    td_list = [0] * len(df)
    td_count = 0
    for i in range(4, len(df)):
        cur = df['Close'].iloc[i]
        prior = df['Close'].iloc[i - 4]
        if cur > prior:
            td_count = td_count + 1 if td_count >= 0 else 1
        elif cur < prior:
            td_count = td_count - 1 if td_count <= 0 else -1
        else:
            td_count = 0
        td_list[i] = td_count
    return td_list


def calc_ma_touches(df, windows=[10, 20, 60]):
    """回傳每一根 K 棒是否碰觸某條均線 (dict: {idx: 'xxMA'})"""
    touches = {}
    for w in windows:
        if len(df) < w:
            continue
        ma = df['Close'].rolling(w).mean()
        for i in range(w, len(df)):
            if pd.isna(ma.iloc[i]):
                continue
            low, high = df['Low'].iloc[i], df['High'].iloc[i]
            ma_val = ma.iloc[i]
            # 當日K棒碰到均線 (最低 <= MA <= 最高) 且收在均線之上 → 回測成功
            if low <= ma_val <= high and df['Close'].iloc[i] >= ma_val * 0.99:
                if i not in touches:
                    touches[i] = f'{w}MA'
    return touches


def compute_forward_returns(df, idx):
    """計算某一天之後 1/3/5/10 天的報酬率 (%)"""
    close_at_trigger = df['Close'].iloc[idx]
    results = {}
    for d in FORWARD_DAYS:
        future_idx = idx + d
        if future_idx < len(df):
            future_close = df['Close'].iloc[future_idx]
            ret = (future_close - close_at_trigger) / close_at_trigger * 100
            results[f'ret_{d}d'] = round(ret, 2)
        else:
            results[f'ret_{d}d'] = None
    return results


def aggregate_stats(events, signal_direction):
    """
    彙總多次觸發事件的勝率與平均報酬。
    signal_direction: 'sell' 表示預期下跌 (TD9+), 'buy' 表示預期上漲 (TD9-)
    """
    if not events:
        return None

    stats = {
        'total_signals': len(events),
        'history': events,
    }

    for d in FORWARD_DAYS:
        key = f'ret_{d}d'
        vals = [e[key] for e in events if e.get(key) is not None]
        if not vals:
            stats[f'avg_{d}d'] = None
            stats[f'win_rate_{d}d'] = None
            stats[f'best_{d}d'] = None
            stats[f'worst_{d}d'] = None
            continue

        avg = round(np.mean(vals), 2)
        stats[f'avg_{d}d'] = avg

        if signal_direction == 'sell':
            # 做空視角：下跌才算贏
            wins = sum(1 for v in vals if v < 0)
        else:
            # 做多視角：上漲才算贏
            wins = sum(1 for v in vals if v > 0)

        stats[f'win_rate_{d}d'] = round(wins / len(vals) * 100, 1)
        stats[f'best_{d}d'] = round(max(vals), 2) if signal_direction == 'buy' else round(min(vals), 2)
        stats[f'worst_{d}d'] = round(min(vals), 2) if signal_direction == 'buy' else round(max(vals), 2)

    return stats


def run_backtest():
    """主回測引擎"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📈 啟動個股訊號勝率回測引擎...")
    print(f"  → 下載 {len(AI_TECH_STOCKS)} 檔股票 1 年日線資料...")

    tickers_str = " ".join(AI_TECH_STOCKS)
    all_data = yf.download(tickers_str, period="1y", interval="1d", progress=False, group_by='ticker')

    results = {}
    success_count = 0

    for ticker in AI_TECH_STOCKS:
        try:
            if ticker not in all_data.columns.levels[0]:
                continue
            df = all_data[ticker].dropna(how='all').copy()
            if len(df) < 60:
                continue

            # ----- TD Sequential 回測 -----
            td_series = calc_td_series(df)
            td9_sell_events = []   # TD >= 9 (上漲竭盡 → 預期回調)
            td9_buy_events = []    # TD <= -9 (下跌竭盡 → 預期反彈)
            td8_sell_events = []
            td8_buy_events = []

            for i in range(len(df)):
                td = td_series[i]
                fwd = compute_forward_returns(df, i)
                event = {
                    'date': df.index[i].strftime('%Y-%m-%d'),
                    'td_val': td,
                    'price': round(float(df['Close'].iloc[i]), 2),
                    **fwd,
                }
                if td == 9:
                    td9_sell_events.append(event)
                elif td == -9:
                    td9_buy_events.append(event)
                elif td == 8:
                    td8_sell_events.append(event)
                elif td == -8:
                    td8_buy_events.append(event)

            # ----- 均線回測 回測 -----
            ma_touches_map = calc_ma_touches(df)
            ma_events = []
            for idx, ma_label in ma_touches_map.items():
                fwd = compute_forward_returns(df, idx)
                ma_events.append({
                    'date': df.index[idx].strftime('%Y-%m-%d'),
                    'ma': ma_label,
                    'price': round(float(df['Close'].iloc[idx]), 2),
                    **fwd,
                })

            # ----- 彙整 -----
            ticker_result = {}

            s = aggregate_stats(td9_sell_events, 'sell')
            if s: ticker_result['td9_sell'] = s

            s = aggregate_stats(td9_buy_events, 'buy')
            if s: ticker_result['td9_buy'] = s

            s = aggregate_stats(td8_sell_events, 'sell')
            if s: ticker_result['td8_sell'] = s

            s = aggregate_stats(td8_buy_events, 'buy')
            if s: ticker_result['td8_buy'] = s

            s = aggregate_stats(ma_events, 'buy')
            if s: ticker_result['ma_pullback'] = s

            if ticker_result:
                results[ticker] = ticker_result
                success_count += 1

        except Exception as e:
            print(f"  ⚠️ {ticker} 回測失敗: {e}")

    # ----- 輸出 JSON -----
    output = {
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'lookback': '1y',
        'forward_days': FORWARD_DAYS,
        'stocks': results,
    }

    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    filepath = os.path.join(DASHBOARD_DIR, 'backtest_data.json')
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False)

    print(f"  ✅ 回測完成！{success_count} 檔股票，結果已寫入 backtest_data.json")
    return output


if __name__ == '__main__':
    run_backtest()
