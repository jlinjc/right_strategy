"""
us_scanner_td9.py - 日線 TD Sequential (TD9) 掃描器
捕捉日線級別的趨勢竭盡訊號 (TD8 與 TD9)。
連跌 8-9 天 (買入訊號 / 下跌竭盡)
連漲 8-9 天 (賣出訊號 / 上漲竭盡)
"""

import time
import pandas as pd
from datetime import datetime
from scanner_base import AI_TECH_STOCKS, BENCHMARK, send_line_notify, save_dashboard_data
import yfinance as yf

def get_daily_data(ticker_list, benchmark="QQQ", period="60d"):
    """獲取過去 60 天的日 K 線資料，用於計算 TD"""
    all_tickers = [benchmark] + ticker_list
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 正在下載大盤與 {len(ticker_list)} 檔個股日線資料...")
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
                if not ticker_df.empty:
                    result_dict[ticker] = ticker_df
        except Exception:
            pass

    benchmark_df = result_dict.pop(benchmark, None)
    return benchmark_df, result_dict

def calc_td_setup(df):
    """
    計算 TD Setup (TD9)
    比較當日收盤價與 4 天前的收盤價。
    回傳最後一天的 TD 值：
    正數代表連漲 (Up Setup, 賣出竭盡訊號)
    負數代表連跌 (Down Setup, 買進反彈訊號)
    """
    if len(df) < 5:
        return 0
        
    td_count = 0
    
    for i in range(4, len(df)):
        current_close = df['Close'].iloc[i]
        prior_close = df['Close'].iloc[i-4]
        
        if current_close > prior_close:
            if td_count >= 0:
                td_count += 1
            else:
                td_count = 1
        elif current_close < prior_close:
            if td_count <= 0:
                td_count -= 1
            else:
                td_count = -1
        else:
            # 如果價格一樣，我們讓計數重新開始 (中斷連續狀態)
            td_count = 0
            
    return td_count

def scan_td9():
    market_df, stocks_dict = get_daily_data(AI_TECH_STOCKS, benchmark=BENCHMARK)
    if market_df is None:
        return None, None, None
        
    latest_date = market_df.index[-1].date()
    alerts = []
    
    print(f"\n======== 【日線 TD9 掃描報告】 ========")
    print(f"最新交易日: {latest_date}")
    
    qqq_td = calc_td_setup(market_df)
    print(f"大盤 {BENCHMARK} 目前 TD 計數: {qqq_td}")
    print("---------------------------------------")
    
    td8_buy = []  
    td9_buy = []  
    td8_sell = [] 
    td9_sell = [] 
    
    # 用來收集所有需要畫圖的股票 (含它們的 TD 數值列表)
    triggered_stocks = {}
    all_td_values = {}  # Dashboard 用：每檔股票的最新 TD 值
    
    for ticker, df in stocks_dict.items():
        # 我們需要整個歷史的 TD_list 來畫圖
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
            
        td_val = td_list[-1]
        all_td_values[ticker] = td_val
        
        if td_val == -8: td8_buy.append(ticker)
        elif td_val == -9: td9_buy.append(ticker)
        elif td_val == 8: td8_sell.append(ticker)
        elif td_val == 9: td9_sell.append(ticker)
        
        if td_val in [-8, -9, 8, 9]:
            triggered_stocks[ticker] = (df, td_list)

    if td9_sell:
        msg = f"🔴 [上漲竭盡 TD9] 連漲9根:\n" + ", ".join(td9_sell)
        print(msg)
        alerts.append(msg)
    if td8_sell:
        msg = f"🟠 [上漲竭盡 TD8] 連漲8根:\n" + ", ".join(td8_sell)
        print(msg)
        alerts.append(msg)
    if td9_buy:
        msg = f"🟢 [下跌竭盡 TD9] 連跌9根:\n" + ", ".join(td9_buy)
        print(msg)
        alerts.append(msg)
    if td8_buy:
        msg = f"🟡 [下跌竭盡 TD8] 連跌8根:\n" + ", ".join(td8_buy)
        print(msg)
        alerts.append(msg)
        
    if not alerts:
        print("今日無標的觸發 TD8 / TD9 竭盡訊號。")
        
    print("=======================================\n")
    
    # 將數據寫入 JSON 供 Web Dashboard 讀取
    save_dashboard_data('td9_data.json', {
        'date': str(latest_date),
        'results': all_td_values
    })
    
    return alerts, latest_date, triggered_stocks

from chart_utils import generate_daily_chart

def start_daily_monitor():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🟢 啟動日線 TD9 監控模式 ... (按 Ctrl+C 停止)")
    last_notified_date = None
    
    while True:
        try:
            alerts, latest_date, triggered_stocks = scan_td9()
            
            if alerts and latest_date and latest_date != last_notified_date:
                # 1. 先發送總覽文字
                msg_body = f"\n【日線 TD9 竭盡警報】 {latest_date}\n\n" + "\n\n".join(alerts)
                print("📲 發現新的 TD8/TD9 訊號，正在發送 LINE 通知...")
                send_line_notify(msg_body)
                
                # 2. 針對每一檔觸發的股票，畫圖並傳送
                for ticker, (df, td_list) in triggered_stocks.items():
                    img_path = generate_daily_chart(ticker, df, td_list, filename=f"td9_{ticker}.png")
                    if img_path:
                        send_line_notify(f"{ticker} 最新日線 TD 與均線圖表", image_path=img_path)
                
                last_notified_date = latest_date
            elif latest_date == last_notified_date:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ⏳ 今日 ({latest_date}) 的 TD 訊號已通知過，等待下一個交易日...")
                
        except KeyboardInterrupt:
            print("\n🛑 監控已手動停止。")
            break
        except Exception as e:
            print(f"\n⚠️ 監控發生錯誤: {e}")
            
        time.sleep(3600)

if __name__ == "__main__":
    # 你可以選擇只跑一次 (取消下面兩行的註解)，或是跑持續監控迴圈
    # alerts, latest_date = scan_td9()
    # if alerts: send_line_notify(f"【日線 TD9】\n" + "\n".join(alerts))
    
    start_daily_monitor()
