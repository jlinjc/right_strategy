"""
generate_chart_data.py - 產出前端圖表所需的 K 線 JSON
======================================================
下載所有監控股票的日線與 3 分鐘 K 線資料，
轉成 LightweightCharts 格式的 JSON 存到 Web_Dashboard/charts/
"""

import os
import sys
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import json
import pandas as pd
import yfinance as yf
from datetime import datetime
from scanner_base import AI_TECH_STOCKS, BENCHMARK

DASHBOARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Web_Dashboard')
CHARTS_DIR = os.path.join(DASHBOARD_DIR, 'charts')


def generate_all_chart_data():
    """下載所有股票的日線與分鐘線資料並存成 JSON"""
    os.makedirs(CHARTS_DIR, exist_ok=True)

    all_tickers = [BENCHMARK] + AI_TECH_STOCKS
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 正在產出 {len(all_tickers)} 檔股票圖表資料...")

    # ===== 日線資料 (60 天) =====
    print("  → 下載日線資料 (60d)...")
    tickers_str = " ".join(all_tickers)
    daily_df = yf.download(tickers_str, period="60d", interval="1d", progress=False, group_by='ticker')

    for ticker in all_tickers:
        try:
            if ticker in daily_df.columns.levels[0]:
                df = daily_df[ticker].dropna(how='all')
                if df.empty:
                    continue

                data = []
                for idx, row in df.iterrows():
                    data.append({
                        'time': idx.strftime('%Y-%m-%d'),
                        'open': round(float(row['Open']), 2),
                        'high': round(float(row['High']), 2),
                        'low': round(float(row['Low']), 2),
                        'close': round(float(row['Close']), 2),
                        'volume': int(row['Volume']) if not pd.isna(row['Volume']) else 0,
                    })

                filepath = os.path.join(CHARTS_DIR, f'{ticker}_daily.json')
                with open(filepath, 'w') as f:
                    json.dump(data, f)
        except Exception as e:
            print(f"  ⚠️ {ticker} 日線失敗: {e}")

    # ===== 3 分鐘 K 線 (5 天) =====
    print("  → 下載 3 分鐘 K 線 (5d)...")
    intra_df = yf.download(tickers_str, period="5d", interval="1m", progress=False, group_by='ticker')

    for ticker in all_tickers:
        try:
            if ticker in intra_df.columns.levels[0]:
                df = intra_df[ticker].dropna(how='all')
                if df.empty:
                    continue

                # 合成 3 分鐘 K 線
                df_3m = df.resample('3min').agg({
                    'Open': 'first', 'High': 'max', 'Low': 'min',
                    'Close': 'last', 'Volume': 'sum'
                }).dropna()

                data = []
                for idx, row in df_3m.iterrows():
                    data.append({
                        'time': int(idx.timestamp()),
                        'open': round(float(row['Open']), 2),
                        'high': round(float(row['High']), 2),
                        'low': round(float(row['Low']), 2),
                        'close': round(float(row['Close']), 2),
                        'volume': int(row['Volume']) if not pd.isna(row['Volume']) else 0,
                    })

                filepath = os.path.join(CHARTS_DIR, f'{ticker}_3m.json')
                with open(filepath, 'w') as f:
                    json.dump(data, f)
        except Exception as e:
            print(f"  ⚠️ {ticker} 3分K失敗: {e}")

    # ===== 計算 QQQ 均線支撐 =====
    try:
        if BENCHMARK in daily_df.columns.levels[0]:
            qqq_df = daily_df[BENCHMARK].dropna(how='all')
            if not qqq_df.empty:
                qqq_df['MA5'] = qqq_df['Close'].rolling(window=5).mean()
                qqq_df['MA10'] = qqq_df['Close'].rolling(window=10).mean()
                qqq_df['MA20'] = qqq_df['Close'].rolling(window=20).mean()
                last = qqq_df.iloc[-1]
                ma_data = {
                    'ma5': round(float(last['MA5']), 2) if not pd.isna(last['MA5']) else 0,
                    'ma10': round(float(last['MA10']), 2) if not pd.isna(last['MA10']) else 0,
                    'ma20': round(float(last['MA20']), 2) if not pd.isna(last['MA20']) else 0,
                    'last_close': round(float(last['Close']), 2),
                }
                with open(os.path.join(DASHBOARD_DIR, 'qqq_ma.json'), 'w') as f:
                    json.dump(ma_data, f)
                print(f"  📐 QQQ 均線: 5MA={ma_data['ma5']}  10MA={ma_data['ma10']}  20MA={ma_data['ma20']}")
    except Exception as e:
        print(f"  ⚠️ QQQ 均線計算失敗: {e}")

    # 寫入一個 metadata 讓前端知道圖表資料有更新
    meta = {
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'tickers': all_tickers,
    }
    with open(os.path.join(CHARTS_DIR, '_meta.json'), 'w') as f:
        json.dump(meta, f)

    print(f"  ✅ 圖表資料產出完成！共 {len(all_tickers)} 檔\n")


if __name__ == '__main__':
    generate_all_chart_data()
