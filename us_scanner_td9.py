"""
us_scanner_td9.py - 日線 TD Sequential (TD9) 掃描器
捕捉日線級別的趨勢竭盡訊號 (TD8 與 TD9)，並透過大盤廣度與 RSI 雙重過濾。
"""

import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import time
import re
import pandas as pd
from datetime import datetime
from scanner_base import (
    AI_TECH_STOCKS, BENCHMARK, send_line_notify, save_dashboard_data,
    calc_atr, calculate_position, TOTAL_CAPITAL, RISK_PER_TRADE, calculate_market_breadth
)
import yfinance as yf

def get_daily_data(ticker_list, benchmark="QQQ", period="1y"):
    """獲取過去 1 年的日 K 線資料，確保有足夠的資料算 200MA 與 RSI"""
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

def calc_rsi(prices, period=14):
    """計算 14 日 RSI"""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_td_setup(df):
    """
    計算 TD Setup (TD9)
    比較當日收盤價與 4 天前的收盤價。
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
            td_count = 0
            
    return td_count

def scan_td9():
    market_df, stocks_dict = get_daily_data(AI_TECH_STOCKS, benchmark=BENCHMARK)
    if market_df is None:
        return None, None, None
        
    latest_date = market_df.index[-1].date()
    alerts = []
    
    # === 1. 計算大盤廣度與風控係數 ===
    breadth = calculate_market_breadth()
    if breadth > 60:
        breadth_label = "🟢 【強勢多頭】"
        risk_pct = 0.02
        exposure_label = "建議曝險：100% (正常交易)"
    elif breadth >= 40:
        breadth_label = "🟡 【震盪行情】"
        risk_pct = 0.01
        exposure_label = "建議曝險：50% (單筆交易風險自動減半至 1%)"
    else:
        breadth_label = "🔴 【防守行情】"
        risk_pct = 0.00
        exposure_label = "建議曝險：0% (熊市/大跌行情，暫停做多交易)"

    print(f"\n======== 【日線 TD9 掃描報告】 ========")
    print(f"最新交易日: {latest_date}")
    print(f"大盤廣度: {breadth:.1f}% | 狀態: {breadth_label}")
    
    qqq_td = calc_td_setup(market_df)
    print(f"大盤 {BENCHMARK} 目前 TD 計數: {qqq_td}")
    print("---------------------------------------")
    
    td9_sell = [] 
    td8_sell = [] 
    
    triggered_stocks = {}
    all_td_values = {}  # Dashboard 用
    
    for ticker, df in stocks_dict.items():
        if len(df) < 200:
            continue
            
        # 計算 200MA 與 RSI
        df['200MA'] = df['Close'].rolling(window=200).mean()
        df['RSI'] = calc_rsi(df['Close'], 14)
        
        # 整個歷史的 TD_list
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
        
        # 判斷是否為買進竭盡 (TD-8 或 TD-9)
        is_buy_signal = td_val in [-8, -9]
        is_sell_signal = td_val in [8, 9]
        
        if is_buy_signal or is_sell_signal:
            triggered_stocks[ticker] = (df, td_list)
            
            # 買進訊號過濾與風控
            if is_buy_signal:
                close = df['Close'].iloc[-1]
                low = df['Low'].iloc[-1]
                ma200 = df['200MA'].iloc[-1]
                rsi = df['RSI'].iloc[-1]
                
                # 底背離檢測：今日 Close 是 15 天內最低，但今日 RSI 高於 15 天內最低 RSI
                is_price_new_low = close == df['Close'].rolling(15).min().iloc[-1]
                is_rsi_not_new_low = rsi > df['RSI'].rolling(15).min().iloc[-1]
                is_divergence = is_price_new_low and is_rsi_not_new_low
                
                # 疊加過濾器：1. 股價必須大於 200MA (多頭回調) 2. RSI <= 35 或是底背離確認
                is_rsi_ok = rsi <= 35 or is_divergence
                is_trend_ok = close > ma200
                
                if is_trend_ok and is_rsi_ok:
                    atr_series = calc_atr(df['High'], df['Low'], df['Close'])
                    atr = atr_series.iloc[-1]
                    
                    # 停損價：近期低點或收盤-1.5ATR (取較高者)
                    stop_loss = max(low, close - 1.5 * atr)
                    if stop_loss >= close * 0.99:
                        stop_loss = close * 0.97  # 至少給 3% 空間
                        
                    stop_pct = ((close - stop_loss) / close) * 100
                    shares, cost, actual_risk = calculate_position(close, stop_loss, custom_risk_pct=risk_pct)
                    
                    div_label = "⚡ 底背離確認 🟢" if is_divergence else "無"
                    risk_warning = "⚠️ 觸及單筆 20% 資金上限 (風控鎖定 $2,000)" if cost >= 1990 else ""
                    
                    breadth_status_str = f"🌐 【Anti-Gravity 系統廣度報告】\n📊 科技股高於 50MA 比例：{breadth:.1f}% {breadth_label}\n├─ {exposure_label}\n└─ 大盤 QQQ TD 目前：{qqq_td}\n"
                    label = "🟢 [下跌竭盡 TD9]" if td_val == -9 else "🟡 [下跌竭盡 TD8]"
                    
                    if risk_pct == 0.0:
                        msg = (
                            f"{breadth_status_str}──────────────────────────────\n"
                            f"{label} ➔ {ticker}\n"
                            f"⏱️ 時間：日線收盤確認\n"
                            f"──────────────────────────────\n"
                            f"📊 指標與背離確認：\n"
                            f" ├─ 當前收盤：${close:.2f} (完成連跌 {abs(td_val)} 日)\n"
                            f" ├─ 中線趨勢：高於 200MA 🟢 (目前 200MA 在 ${ma200:.2f})\n"
                            f" ├─ 14日 RSI：{rsi:.1f} 🟢\n"
                            f" └─ RSI 背離：{div_label}\n"
                            f"──────────────────────────────\n"
                            f"⚠️ 系統防禦：大盤廣度過低 (Breadth < 40%)，暫停做多警報開倉！"
                        )
                    else:
                        msg = (
                            f"{breadth_status_str}──────────────────────────────\n"
                            f"{label} ➔ {ticker}\n"
                            f"⏱️ 時間：日線收盤確認\n"
                            f"──────────────────────────────\n"
                            f"📊 指標與背離確認：\n"
                            f" ├─ 當前收盤：${close:.2f} (完成連跌 {abs(td_val)} 日)\n"
                            f" ├─ 中線趨勢：高於 200MA 🟢 (目前 200MA 在 ${ma200:.2f})\n"
                            f" ├─ 14日 RSI：{rsi:.1f} 🟢\n"
                            f" └─ RSI 背離：{div_label}\n"
                            f"──────────────────────────────\n"
                            f"🛡️ 機構級風控與倉位 (本金 $10,000 | 風險 {risk_pct*100}%):\n"
                            f" ├─ 停損價位 (1.5 ATR/前低)：${stop_loss:.2f} (-{stop_pct:.1f}%)\n"
                            f" ├─ 建議買入：{shares} 股 (約 ${cost:,.0f})\n"
                            f" └─ 實際承擔風險：${actual_risk:.1f} (本金 {actual_risk/100:.2f}%)\n"
                            f"   {risk_warning}"
                        )
                    
                    print(msg)
                    alerts.append(msg)
                    
            # 賣出訊號
            elif td_val == 9:
                td9_sell.append(ticker)
            elif td_val == 8:
                td8_sell.append(ticker)

    if td9_sell:
        msg = f"🔴 [上漲竭盡 TD9] 連漲9根:\n" + ", ".join(td9_sell)
        print(msg)
        alerts.append(msg)
    if td8_sell:
        msg = f"🟠 [上漲竭盡 TD8] 連漲8根:\n" + ", ".join(td8_sell)
        print(msg)
        alerts.append(msg)
        
    if not alerts:
        print("今日無標的觸發 TD8 / TD9 竭盡訊號 (或未通過 RSI/200MA 篩選)。")
        
    print("=======================================\n")
    
    # 寫入 JSON
    save_dashboard_data('td9_data.json', {
        'date': str(latest_date),
        'results': all_td_values,
        'market_breadth': breadth
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
                # 1. 先發送警報文字
                msg_body = "\n\n".join(alerts)
                print("📲 發現新的 TD8/TD9 訊號，正在發送 LINE 通知...")
                send_line_notify(msg_body)
                
                # 2. 針對每一檔觸發的股票，畫圖並傳送
                for ticker, (df, td_list) in triggered_stocks.items():
                    # 只有真正通過篩選的才需要發圖 (因為 triggered_stocks 包含未過濾的，在此簡單以 alert 內容判定)
                    any_alert_has_ticker = any(ticker in alert for alert in alerts)
                    if any_alert_has_ticker:
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
    start_daily_monitor()
