# [LOCAL VERSION DIFF]: 加入大盤廣度 (calculate_market_breadth) 與動態部位風險計算 (calculate_position)，依據大盤狀態調整曝險比例 (0%, 50%, 100%)。
"""
us_scanner_ma.py - 日線均線回檔掃描器
捕捉多頭趨勢中 (股價 > 200MA)，回測重要均線 (10MA, 20MA, 60MA) 的個股。
透過大盤廣度、相對強度 (RS Score) 與縮量回檔多重因子進行極致選股。
"""

import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import time
import pandas as pd
from datetime import datetime
from scanner_base import (
    AI_TECH_STOCKS, BENCHMARK, send_line_notify, save_dashboard_data,
    calc_atr, calculate_position, TOTAL_CAPITAL, RISK_PER_TRADE, calculate_market_breadth
)
import yfinance as yf
from chart_utils import generate_daily_chart

def get_daily_data_for_ma(ticker_list, benchmark="QQQ", period="1y"):
    """獲取過去 1 年的日 K 線資料，用於計算 200MA 與 相對強度"""
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
        # 若資料不足 200 天，設 200MA 為 0
        df['200MA'] = pd.Series([0] * len(df), index=df.index)
    else:
        df['200MA'] = df['Close'].rolling(window=200).mean()

    # 計算均線
    df['10MA'] = df['Close'].rolling(window=10).mean()
    df['20MA'] = df['Close'].rolling(window=20).mean()
    df['60MA'] = df['Close'].rolling(window=60).mean()
    df['Vol20MA'] = df['Volume'].rolling(window=20).mean()
    
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
    if market_df is None or market_df.empty:
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

    print(f"\n======== 【日線均線回檔掃描報告】 ========")
    print(f"最新交易日: {latest_date}")
    print(f"大盤廣度: {breadth:.1f}% | 狀態: {breadth_label}")
    print("條件：多頭趨勢 (股價 > 200MA) 且今日回測觸碰 10MA/20MA/60MA")
    print("---------------------------------------")
    
    touch_10ma_alerts = []
    touch_20ma_alerts = []
    touch_60ma_alerts = []
    
    triggered_stocks = {}
    
    for ticker, df in stocks_dict.items():
        touched = check_ma_touch(df)
        if touched:
            latest = df.iloc[-1]
            
            # 多頭排列濾網：10MA 必須大於 20MA
            if latest['10MA'] < latest['20MA'] * 0.99:
                continue
                
            close = latest['Close']
            low = latest['Low']
            
            # 疊加過濾器 1：量縮回檔 (當日成交量必須低於 20 日平均成交量)
            vol_20ma = latest['Vol20MA']
            is_volume_ok = latest['Volume'] < vol_20ma
            
            # 疊加過濾器 2：相對強度 (計算個股相較 QQQ 20 日的滾動回報率)
            stock_ret_20 = (close - df['Close'].iloc[-20]) / df['Close'].iloc[-20]
            qqq_ret_20 = (market_df['Close'].iloc[-1] - market_df['Close'].iloc[-20]) / market_df['Close'].iloc[-20]
            rs_score = stock_ret_20 - qqq_ret_20
            is_rs_ok = rs_score > 0
            
            # 嚴格選股過濾
            if not (is_volume_ok and is_rs_ok):
                continue
            
            # 計算 ATR
            atr_series = calc_atr(df['High'], df['Low'], df['Close'])
            atr = atr_series.iloc[-1]
            
            # 決定停損價 (找觸碰到的最長均線當支撐)
            support_ma = latest['10MA']
            if '60MA' in touched: support_ma = latest['60MA']
            elif '20MA' in touched: support_ma = latest['20MA']
            
            stop_by_atr = close - 1.5 * atr
            stop_by_ma = support_ma * 0.99
            stop_loss = max(stop_by_atr, stop_by_ma)
            
            if stop_loss >= close * 0.99:
                stop_loss = close * 0.97
                
            stop_pct = ((close - stop_loss) / close) * 100
            shares, cost, actual_risk = calculate_position(close, stop_loss, custom_risk_pct=risk_pct)
            
            risk_warning = "⚠️ 觸及單筆 20% 資金上限 (風控鎖定 $2,000)" if cost >= 1990 else ""
            breadth_status_str = f"🌐 【Anti-Gravity 系統廣度報告】\n📊 科技股高於 50MA 比例：{breadth:.1f}% {breadth_label}\n├─ {exposure_label}\n└─ 大盤 QQQ 當日：{qqq_ret_20*100:+.2f}%\n"
            
            ma_name = touched[0]  # 取第一個觸碰的
            
            if risk_pct == 0.0:
                msg = (
                    f"{breadth_status_str}──────────────────────────────\n"
                    f"📉 [均線回測 + 強勢股] ➔ {ticker}\n"
                    f"⏱️ 時間：日線收盤確認\n"
                    f"──────────────────────────────\n"
                    f"📈 均線支撐與強度確認：\n"
                    f" ├─ 當前收盤：${close:.2f}\n"
                    f" ├─ 觸碰支撐：{ma_name} (目前於 ${support_ma:.2f} 🟢)\n"
                    f" ├─ 中線趨勢：高於 200MA 🟢 (目前 200MA 在 ${latest['200MA']:.2f})\n"
                    f" ├─ 相對強度 (20日)：比大盤強 +{rs_score*100:.1f}% 🟢\n"
                    f" └─ 回踩量能：{latest['Volume']/1000:.0f}K (均量 {vol_20ma/1000:.0f}K 🟢 量縮 {latest['Volume']/vol_20ma:.1f}x)\n"
                    f"──────────────────────────────\n"
                    f"⚠️ 系統防禦：大盤廣度過低 (Breadth < 40%)，暫停做多警報開倉！"
                )
            else:
                msg = (
                    f"{breadth_status_str}──────────────────────────────\n"
                    f"📉 [均線回測 + 強勢股] ➔ {ticker}\n"
                    f"⏱️ 時間：日線收盤確認\n"
                    f"──────────────────────────────\n"
                    f"📈 均線支撐與強度確認：\n"
                    f" ├─ 當前收盤：${close:.2f}\n"
                    f" ├─ 觸碰支撐：{ma_name} (目前於 ${support_ma:.2f} 🟢)\n"
                    f" ├─ 中線趨勢：高於 200MA 🟢 (目前 200MA 在 ${latest['200MA']:.2f})\n"
                    f" ├─ 相對強度 (20日)：比大盤強 +{rs_score*100:.1f}% 🟢\n"
                    f" └─ 回踩量能：{latest['Volume']/1000:.0f}K (均量 {vol_20ma/1000:.0f}K 🟢 量縮 {latest['Volume']/vol_20ma:.1f}x)\n"
                    f"──────────────────────────────\n"
                    f"🛡️ 機構級風控與倉位 (本金 $10,000 | 風險 {risk_pct*100}%):\n"
                    f" ├─ 停損價位 (MA支撐/ATR)：${stop_loss:.2f} (-{stop_pct:.1f}%)\n"
                    f" ├─ 建議買入：{shares} 股 (約 ${cost:,.0f})\n"
                    f" └─ 實際承擔風險：${actual_risk:.1f} (本金 {actual_risk/100:.2f}%)\n"
                    f"   {risk_warning}"
                )
            
            if "10MA" in touched: touch_10ma_alerts.append(msg)
            elif "20MA" in touched: touch_20ma_alerts.append(msg)
            elif "60MA" in touched: touch_60ma_alerts.append(msg)
            
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

    if touch_10ma_alerts:
        alerts.extend(touch_10ma_alerts)
    if touch_20ma_alerts:
        alerts.extend(touch_20ma_alerts)
    if touch_60ma_alerts:
        alerts.extend(touch_60ma_alerts)
        
    if not alerts:
        print("今日無標的回測 10/20/60MA (或未通過 QQQ 相對強度 / 縮量回踩過濾)。")
        
    print("=======================================\n")
    
    # 整理給 Dashboard 的 JSON
    ma_results = {}
    for ticker, (df, td_list) in triggered_stocks.items():
        # 僅記錄真正通過過濾的
        any_alert_has_ticker = any(ticker in alert for alert in alerts)
        if any_alert_has_ticker:
            touched = check_ma_touch(df)
            if touched:
                if "10MA" in touched: ma_results[ticker] = "10MA"
                elif "20MA" in touched: ma_results[ticker] = "20MA"
                elif "60MA" in touched: ma_results[ticker] = "60MA"
                
    save_dashboard_data('ma_data.json', {
        'date': str(latest_date),
        'results': ma_results,
        'market_breadth': breadth
    })
    
    return alerts, latest_date, triggered_stocks

def start_daily_ma_monitor():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🟢 啟動日線均線回檔監控模式 ... (按 Ctrl+C 停止)")
    last_notified_date = None
    
    while True:
        try:
            alerts, latest_date, triggered_stocks = scan_ma_pullback()
            
            if alerts and latest_date and latest_date != last_notified_date:
                # 合併發送所有通過的警報
                msg_body = "\n\n".join(alerts)
                print("📲 發現均線回測訊號，正在發送 LINE 通知...")
                send_line_notify(msg_body)
                
                # 針對每一檔觸發的股票，畫圖並傳送
                for ticker, (df, td_list) in triggered_stocks.items():
                    any_alert_has_ticker = any(ticker in alert for alert in alerts)
                    if any_alert_has_ticker:
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
