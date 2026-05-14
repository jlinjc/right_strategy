"""
us_scanner_ma.py - 日線均線回檔掃描器
捕捉多頭趨勢中 (股價 > 200MA)，回測重要均線 (10MA, 20MA, 60MA) 的個股。
"""

import time
import pandas as pd
from datetime import datetime
from scanner_base import (
    AI_TECH_STOCKS, BENCHMARK, send_line_notify, save_dashboard_data
)
import yfinance as yf
from chart_utils import generate_daily_chart

def get_daily_data_for_ma(ticker_list, benchmark="QQQ", period="1y"):
    """獲取過去 1 年的日 K 線資料，用於計算 200MA"""
    all_tickers = [benchmark] + ticker_list
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 正在下載大盤與 {len(ticker_list)} 檔個股日線資料 (計算均線)...")
    tickers_str = " ".join(all_tickers)
    df = yf.download(tickers_str, period=period, interval="1d", progress=False, group_by='ticker')
    
    if df.empty:
        print("❌ 下載失敗，請檢查網路連線。")
        return None, {}

    result_dict = {}
    for ticker in all_tickers:
        try:
            if ticker in df.columns.levels[0]:
                ticker_df = df[ticker].dropna(how='all')
                # 至少要有足夠的資料算 MA
                if not ticker_df.empty and len(ticker_df) > 60:
                    result_dict[ticker] = ticker_df
        except Exception:
            pass

    benchmark_df = result_dict.pop(benchmark, None)
    return benchmark_df, result_dict

def check_ma_touch(df):
    """
    判斷今日是否觸碰 MA 且收盤在 200MA 之上。
    """
    if len(df) < 200:
        # 若資料不足 200 天，退而求其次用 120MA 甚至跳過
        pass

    # 計算均線
    df['10MA'] = df['Close'].rolling(window=10).mean()
    df['20MA'] = df['Close'].rolling(window=20).mean()
    df['60MA'] = df['Close'].rolling(window=60).mean()
    
    # 計算 200MA，如果上市不滿 200 天則不計算
    if len(df) >= 200:
        df['200MA'] = df['Close'].rolling(window=200).mean()
    else:
        df['200MA'] = pd.Series([0] * len(df), index=df.index) # 設為 0 以防報錯
    
    latest = df.iloc[-1]
    
    # 趨勢濾網：必須在 200MA 之上 (如果資料不足 200 天則預設通過)
    if latest['200MA'] > 0 and latest['Close'] < latest['200MA']:
        return None
        
    touched_mas = []
    
    low = latest['Low']
    high = latest['High']
    
    def is_touching(ma_val):
        if pd.isna(ma_val): return False
        # 1. 實體穿過或下影線踩到 (最低價 <= 均線 且 最高價 >= 均線)
        if low <= ma_val <= high:
            return True
        # 2. 差一點點摸到 (最低價在均線上方 0.5% 以內，給予一點緩衝空間)
        if ma_val < low <= ma_val * 1.005:
            return True
        return False

    if is_touching(latest['10MA']): touched_mas.append("10MA")
    if is_touching(latest['20MA']): touched_mas.append("20MA")
    if is_touching(latest['60MA']): touched_mas.append("60MA")
    
    if touched_mas:
        return touched_mas
    return None

def scan_ma_pullback():
    market_df, stocks_dict = get_daily_data_for_ma(AI_TECH_STOCKS, benchmark=BENCHMARK)
    if market_df is None:
        return None, None, None
        
    latest_date = market_df.index[-1].date()
    alerts = []
    
    print(f"\n======== 【日線均線回檔掃描報告】 ========")
    print(f"最新交易日: {latest_date}")
    print("條件：多頭趨勢 (股價 > 200MA) 且今日回測觸碰 10MA/20MA/60MA")
    print("---------------------------------------")
    
    touch_10ma = []
    touch_20ma = []
    touch_60ma = []
    
    triggered_stocks = {}
    
    for ticker, df in stocks_dict.items():
        touched = check_ma_touch(df)
        if touched:
            if "10MA" in touched: touch_10ma.append(ticker)
            if "20MA" in touched: touch_20ma.append(ticker)
            if "60MA" in touched: touch_60ma.append(ticker)
            
            # 計算這檔股票的 TD9 數列，讓均線圖上也能顯示 TD 數字
            td_list = [0] * len(df)
            td_count = 0
            for i in range(4, len(df)):
                current_close = df['Close'].iloc[i]
                prior_close = df['Close'].iloc[i-4]
                if current_close > prior_close:
                    if td_count >= 0: td_count += 1
                    else: td_count = 1
                elif current_close < prior_close:
                    if td_count <= 0: td_count -= 1
                    else: td_count = -1
                else:
                    td_count = 0
                td_list[i] = td_count
                
            triggered_stocks[ticker] = (df, td_list)

    if touch_10ma:
        msg = f"📉 [回測 10MA] 強勢股短線拉回:\n" + ", ".join(touch_10ma)
        print(msg)
        alerts.append(msg)
    if touch_20ma:
        msg = f"📉 [回測 20MA (月線)] 波段支撐測試:\n" + ", ".join(touch_20ma)
        print(msg)
        alerts.append(msg)
    if touch_60ma:
        msg = f"📉 [回測 60MA (季線)] 中長線買點浮現:\n" + ", ".join(touch_60ma)
        print(msg)
        alerts.append(msg)
        
    if not alerts:
        print("今日無標的回測 10/20/60MA。")
        
    print("=======================================\n")
    
    # 整理給 Dashboard 的 JSON
    ma_results = {}
    for t in touch_10ma: ma_results[t] = "10MA"
    for t in touch_20ma: ma_results[t] = "20MA"
    for t in touch_60ma: ma_results[t] = "60MA"
    save_dashboard_data('ma_data.json', {
        'date': str(latest_date),
        'results': ma_results
    })
    
    return alerts, latest_date, triggered_stocks

from chart_utils import generate_daily_chart

def start_daily_ma_monitor():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🟢 啟動日線均線回檔監控模式 ... (按 Ctrl+C 停止)")
    last_notified_date = None
    
    while True:
        try:
            alerts, latest_date, triggered_stocks = scan_ma_pullback()
            
            if alerts and latest_date and latest_date != last_notified_date:
                msg_body = f"\n【均線回測警報】 {latest_date}\n(多頭趨勢 股價>200MA)\n\n" + "\n\n".join(alerts)
                print("📲 發現均線回測訊號，正在發送 LINE 通知...")
                send_line_notify(msg_body)
                
                # 針對每一檔觸發的股票，畫圖並傳送
                for ticker, (df, td_list) in triggered_stocks.items():
                    img_path = generate_daily_chart(ticker, df, td_list, filename=f"ma_{ticker}.png")
                    if img_path:
                        send_line_notify(f"{ticker} 最新均線回測圖表", image_path=img_path)
                
                last_notified_date = latest_date
            elif latest_date == last_notified_date:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ⏳ 今日 ({latest_date}) 的均線訊號已通知過，等待下一個交易日...")
                
        except KeyboardInterrupt:
            print("\n🛑 監控已手動停止。")
            break
        except Exception as e:
            print(f"\n⚠️ 監控發生錯誤: {e}")
            
        time.sleep(3600)

if __name__ == "__main__":
    start_daily_ma_monitor()
