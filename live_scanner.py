"""
live_scanner.py - Anti-Gravity 即時當沖戰情引擎
=================================================
整合 HOD / ORB 策略，每 60 秒掃描一輪：
  1. 下載最新 3 分 K 線
  2. 計算 55 檔個股相對 QQQ 的即時強弱排名
  3. 偵測 HOD 突破 / ORB 突破 / 逆勢抗跌 / 動能飆升
  4. 將個別圖表輸出到 Web_Dashboard/charts/，供前端獨立讀取
  5. 將排名與累積警報寫入 live_data.json
"""

import time
import sys
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import json
import os
import pandas as pd
from datetime import datetime
from scanner_base import (
    get_market_and_stocks_3m, calc_volume_ratio, save_dashboard_data,
    send_line_notify, AI_TECH_STOCKS, BENCHMARK,
    SURGE_THRESHOLD, RESILIENT_MIN_DAY_RET, DASHBOARD_DIR,
)
from chart_utils import generate_intraday_chart

def df_to_chart_json(df, latest_date):
    # Get the last 3 unique dates
    unique_dates = df.index.date
    if len(unique_dates) == 0: return []
    target_dates = sorted(list(set(unique_dates)))[-3:]
    target_df = df[df.index.date.isin(target_dates)]
    
    data = []
    for idx, row in target_df.iterrows():
        data.append({
            'time': int(idx.timestamp()),
            'open': round(float(row['Open']), 2),
            'high': round(float(row['High']), 2),
            'low': round(float(row['Low']), 2),
            'close': round(float(row['Close']), 2),
            'volume': int(row['Volume']) if not pd.isna(row['Volume']) else 0,
        })
    return data

def calc_relative_strength(benchmark_df, stocks_dict, latest_date):
    today_qqq = benchmark_df[benchmark_df.index.date == latest_date]
    if today_qqq.empty: return {}
    qqq_open = today_qqq.iloc[0]['Open']
    qqq_close = today_qqq.iloc[-1]['Close']
    qqq_ret = (qqq_close - qqq_open) / qqq_open * 100

    rankings = {}
    for ticker, df in stocks_dict.items():
        try:
            today = df[df.index.date == latest_date]
            if today.empty or len(today) < 2: continue
            stock_open = today.iloc[0]['Open']
            stock_close = today.iloc[-1]['Close']
            stock_ret = (stock_close - stock_open) / stock_open * 100
            rs = round(stock_ret - qqq_ret, 2)
            
            stock_hod = today['High'].max()
            stock_lod = today['Low'].min()

            rankings[ticker] = {
                'ret': round(stock_ret, 2),
                'rs': rs,
                'close': round(stock_close, 2),
                'at_hod': stock_close >= stock_hod,
                'at_lod': stock_close <= stock_lod,
            }
        except Exception: continue
    return {'qqq_ret': round(qqq_ret, 2), 'qqq_close': round(qqq_close, 2), 'qqq_open': round(qqq_open, 2), 'stocks': rankings}

def detect_signals(benchmark_df, stocks_dict, latest_date, target_time):
    today_qqq = benchmark_df[benchmark_df.index.date == latest_date]
    if len(today_qqq) < 2: return [], {}

    qqq_prior = today_qqq.iloc[:-1]
    qqq_latest = today_qqq.iloc[-1]
    
    qqq_hod = qqq_prior['High'].max()
    qqq_lod = qqq_prior['Low'].min()
    qqq_at_hod = qqq_latest['High'] > qqq_hod
    qqq_at_lod = qqq_latest['Low'] < qqq_lod
    qqq_bp = (qqq_latest['High'] - qqq_hod) / qqq_hod if qqq_hod > 0 and qqq_at_hod else 0

    qqq_status = {'at_hod': qqq_at_hod, 'at_lod': qqq_at_lod, 'hod_price': round(qqq_hod, 2), 'lod_price': round(qqq_lod, 2)}
    new_alerts = []

    for ticker, df in stocks_dict.items():
        try:
            today = df[df.index.date == latest_date]
            if len(today) < 2: continue

            stock_prior = today.iloc[:-1]
            candle = today.iloc[-1]
            stock_hod = stock_prior['High'].max()
            stock_lod = stock_prior['Low'].min()
            stock_open = today.iloc[0]['Open']
            stock_ret = (candle['Close'] - candle['Open']) / candle['Open']
            stock_day_ret = (candle['Close'] - stock_open) / stock_open
            today_avg_vol = today['Volume'].mean()
            vol_ratio = calc_volume_ratio(candle['Volume'], today_avg_vol)

            if qqq_at_hod:
                if candle['High'] >= stock_hod:
                    new_alerts.append({'symbol': ticker, 'type': 'surge', 'title': 'HOD 突破', 'desc': '大盤過高，個股同步過高', 'time': target_time.strftime('%H:%M'), 'source': 'HOD', 'vol_ratio': round(vol_ratio, 1)})
            
            if qqq_at_lod:
                if candle['Low'] >= stock_lod:
                    new_alerts.append({'symbol': ticker, 'type': 'up', 'title': 'HOD 逆勢', 'desc': '大盤破底，個股未破底', 'time': target_time.strftime('%H:%M'), 'source': 'HOD', 'vol_ratio': round(vol_ratio, 1)})

            if stock_ret >= SURGE_THRESHOLD:
                new_alerts.append({'symbol': ticker, 'type': 'down', 'title': '動能飆升', 'desc': f"單根飆漲 +{stock_ret*100:.2f}%", 'time': target_time.strftime('%H:%M'), 'source': 'HOD', 'vol_ratio': round(vol_ratio, 1)})
        except Exception: continue

    return new_alerts, qqq_status

def run_one_scan(accumulated_alerts):
    market_df, stocks_dict = get_market_and_stocks_3m()
    if market_df is None or market_df.empty: return None

    latest_date = market_df.index[-1].date()
    latest_time = market_df.index[-1]

    CHARTS_DIR = os.path.join(DASHBOARD_DIR, 'charts')
    os.makedirs(CHARTS_DIR, exist_ok=True)
    qqq_data = df_to_chart_json(market_df, latest_date)
    with open(os.path.join(CHARTS_DIR, 'QQQ_3m.json'), 'w') as f: json.dump(qqq_data, f)
    for ticker, df in stocks_dict.items():
        with open(os.path.join(CHARTS_DIR, f'{ticker}_3m.json'), 'w') as f: json.dump(df_to_chart_json(df, latest_date), f)

    rs_data = calc_relative_strength(market_df, stocks_dict, latest_date)
    new_alerts, qqq_status = detect_signals(market_df, stocks_dict, latest_date, latest_time)

    # 計算大盤均線
    try:
        import yfinance as yf
        qqq_daily = yf.download(BENCHMARK, period="30d", interval="1d", progress=False)
        if not qqq_daily.empty:
            qqq_daily['MA5'] = qqq_daily['Close'].rolling(window=5).mean()
            qqq_daily['MA10'] = qqq_daily['Close'].rolling(window=10).mean()
            qqq_daily['MA20'] = qqq_daily['Close'].rolling(window=20).mean()
            last_day = qqq_daily.iloc[-1]
            qqq_status['ma5'] = round(float(last_day['MA5']), 2) if not pd.isna(last_day['MA5']) else 0
            qqq_status['ma10'] = round(float(last_day['MA10']), 2) if not pd.isna(last_day['MA10']) else 0
            qqq_status['ma20'] = round(float(last_day['MA20']), 2) if not pd.isna(last_day['MA20']) else 0
    except Exception as e:
        print(f"Error calculating QQQ MA: {e}")

    actual_new_alerts = []
    for a in new_alerts:
        if not any(ex['symbol']==a['symbol'] and ex['time']==a['time'] and ex['title']==a['title'] for ex in accumulated_alerts):
            actual_new_alerts.append(a)
            accumulated_alerts.insert(0, a)
    
    del accumulated_alerts[100:]

    live_payload = {
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'latest_time': latest_time.strftime('%H:%M'),
        'qqq_status': qqq_status,
        'relative_strength': rs_data,
        'alerts': accumulated_alerts,
    }
    with open(os.path.join(DASHBOARD_DIR, 'live_data.json'), 'w', encoding='utf-8') as f: json.dump(live_payload, f, ensure_ascii=False)
    
    # 為了向下相容 script.js 目前會撈取的 hod_data.json 和 orb_data.json，我們將其分離儲存
    hod_alerts = [a for a in accumulated_alerts if a['source'] == 'HOD']
    orb_alerts = [a for a in accumulated_alerts if a['source'] == 'ORB']
    save_dashboard_data('hod_data.json', {'alerts': hod_alerts})
    save_dashboard_data('orb_data.json', {'alerts': orb_alerts})

    print(f"  ✅ live_data.json 更新 | 新增 {len(actual_new_alerts)} 筆警報 | 累積 {len(accumulated_alerts)} 筆 | 圖表已匯出")
    return actual_new_alerts, market_df, stocks_dict

def start_live_scanner():
    print(f"\n{'='*50}\n  Anti-Gravity 即時當沖戰情引擎\n{'='*50}\n  掃描標的: {len(AI_TECH_STOCKS)} 檔 AI/Tech 股\n  基準指標: {BENCHMARK}\n  掃描間隔: 60 秒\n{'='*50}\n")
    last_time, accumulated_alerts = None, []
    while True:
        try:
            now = datetime.now()
            print(f"\n[{now.strftime('%H:%M:%S')}] 📊 開始第 {'N' if last_time is None else str(int((now - last_time).total_seconds())//60+1)} 輪掃描...")
            result = run_one_scan(accumulated_alerts)
            if result:
                new_alerts, market_df, stocks_dict = result
                if new_alerts:
                    # 不再自動發送 LINE，交給 Web Dashboard 手動觸發
                    first_alert_ticker = new_alerts[0]['symbol']
                    generate_intraday_chart(first_alert_ticker, stocks_dict[first_alert_ticker], BENCHMARK, market_df, filename=f"live_{first_alert_ticker}.png")
            last_time = now
        except KeyboardInterrupt:
            print("\n🛑 即時掃描已停止。")
            break
        except Exception as e:
            print(f"⚠️ 掃描錯誤: {e}")
        time.sleep(60)

if __name__ == '__main__':
    start_live_scanner()
