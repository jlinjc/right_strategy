"""
us_scanner_hod.py - HOD/LOD 策略掃描器
使用今日最高點 (High of Day) 與最低點 (Low of Day) 作為動態基準。

P0: 用 High/Low 判斷突破，掃描所有 K 線
P1: 共用模組、錯誤處理
P2: 成交量確認、抗跌股更嚴格篩選
"""

import time
from datetime import datetime
from scanner_base import (
    get_market_and_stocks_3m, calc_volume_ratio, send_line_notify, save_dashboard_data,
    SURGE_THRESHOLD, RESILIENT_MIN_DAY_RET, BENCHMARK,
)
from chart_utils import generate_intraday_chart

def scan_latest_kline_hod(benchmark_df, stocks_dict, target_time=None):
    if len(benchmark_df) < 2: return [], []
        
    latest_date = benchmark_df.index[-1].date()
    today_benchmark = benchmark_df[benchmark_df.index.date == latest_date]
    
    realtime_alerts = []
    triggered_tickers = set()
    if target_time is None:
        target_time = today_benchmark.index[-1]

    if len(today_benchmark) < 2:
        print("💡 剛開盤，目前只有第一根 K 線，尚無今日區間可供比對。")
        return [], []

    # === 預計算 QQQ 每根 K 線的 HOD/LOD 突破狀態 ===
    qqq_state_map = {}
    for i in range(1, len(today_benchmark)):
        candle = today_benchmark.iloc[i]
        history = today_benchmark.iloc[:i]
        hod = history['High'].max()
        lod = history['Low'].min()
        
        t = today_benchmark.index[i]
        is_new_high = candle['High'] > hod
        is_new_low  = candle['Low'] < lod
        
        qqq_state_map[t] = {
            'is_new_high': is_new_high,
            'is_new_low': is_new_low,
            'breakout_pct': (candle['High'] - hod) / hod if is_new_high else 0,
        }

    # === Header ===
    latest_time = today_benchmark.index[-1]
    if latest_time not in qqq_state_map:
        return [], []
        
    latest_qqq = today_benchmark.iloc[-1]
    latest_state = qqq_state_map[latest_time]
    qqq_ret = (latest_qqq['Close'] - latest_qqq['Open']) / latest_qqq['Open']
    qqq_high_count = sum(1 for s in qqq_state_map.values() if s['is_new_high'])
    qqq_low_count  = sum(1 for s in qqq_state_map.values() if s['is_new_low'])
    
    print(f"\n======== 【HOD/LOD 策略掃描報告】 ========")
    print(f"時間: {latest_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"比對基準: 今日最高點 (HOD) 與最低點 (LOD)")
    print(f"大盤 QQQ 單根漲跌幅: {qqq_ret*100:.3f}%")
    print(f"今日 QQQ 突破 HOD: {qqq_high_count} 次 | 跌破 LOD: {qqq_low_count} 次")
    if latest_state['is_new_high']:
        print("[大盤狀態] 【此刻正在突破今日最高點 (HOD)！】")
    elif latest_state['is_new_low']:
        print("[大盤狀態] 【此刻正在跌破今日最低點 (LOD)！】")
    else:
        print("[大盤狀態] 在今日最高最低區間內震盪")
    print("=============================================\n")

    # === 掃描每檔個股的「每一根」K 線 ===
    best_strong = {}
    best_resilient = {}
    all_surge = []

    for ticker, df in stocks_dict.items():
        try:
            today_stock = df[df.index.date == latest_date]
            if len(today_stock) < 2: continue
            
            stock_day_open = today_stock.iloc[0]['Open']
            today_avg_vol = today_stock['Volume'].mean()
            
            for i in range(1, len(today_stock)):
                candle = today_stock.iloc[i]
                history = today_stock.iloc[:i]
                t = today_stock.index[i]
                
                qqq_state = qqq_state_map.get(t)
                if qqq_state is None: continue
                
                stock_hod = history['High'].max()
                stock_lod = history['Low'].min()
                stock_ret = (candle['Close'] - candle['Open']) / candle['Open']
                stock_day_ret = (candle['Close'] - stock_day_open) / stock_day_open
                vol_ratio = calc_volume_ratio(candle['Volume'], today_avg_vol)
                
                # 攻擊型：大盤突破 HOD，個股也突破且幅度更大
                if qqq_state['is_new_high']:
                    stock_bp = (candle['High'] - stock_hod) / stock_hod if stock_hod > 0 else -1
                    if stock_bp > 0 and stock_bp > qqq_state['breakout_pct']:
                        if ticker not in best_strong or stock_bp > best_strong[ticker][2]:
                            best_strong[ticker] = (ticker, t, stock_bp, qqq_state['breakout_pct'], vol_ratio)
                        if t == target_time:
                            realtime_alerts.append(f"🔥 [攻擊強勢] {ticker:<5} | 突破: +{stock_bp*100:.2f}% (大盤 +{qqq_state['breakout_pct']*100:.2f}%) | 量能: {vol_ratio:.1f}x")
                            triggered_tickers.add(ticker)
                
                # 逆勢抗跌：大盤跌破 LOD，個股沒破底且日漲幅 > 門檻
                stock_broke_low = candle['Low'] < stock_lod
                if (qqq_state['is_new_low'] and not stock_broke_low
                        and stock_day_ret > RESILIENT_MIN_DAY_RET):
                    if ticker not in best_resilient or stock_day_ret > best_resilient[ticker][3]:
                        best_resilient[ticker] = (ticker, t, stock_ret, stock_day_ret, vol_ratio)
                    if t == target_time:
                        realtime_alerts.append(f"🛡️ [逆勢抗跌] {ticker:<5} | 單根: {stock_ret*100:+.2f}% | 距開盤: +{stock_day_ret*100:.2f}% | 量能: {vol_ratio:.1f}x")
                        triggered_tickers.add(ticker)
                
                # 動能飆升
                if stock_ret >= SURGE_THRESHOLD:
                    all_surge.append((ticker, t, stock_ret, vol_ratio))
                    if t == target_time:
                        realtime_alerts.append(f"🚀 [動能飆升] {ticker:<5} | 單根飆漲: +{stock_ret*100:.2f}% | 量能: {vol_ratio:.1f}x")
                        triggered_tickers.add(ticker)
        except Exception:
            continue

    # === 輸出報告 ===
    if qqq_high_count > 0:
        print(f"【攻擊型強勢股】(今日共 {qqq_high_count} 根 K 線突破 QQQ HOD，以下為各股最強突破):")
        if best_strong:
            for t, time, s_pct, q_pct, vr in sorted(best_strong.values(), key=lambda x: x[2], reverse=True):
                print(f"  -> {t:<6} | 突破 HOD: +{s_pct*100:.3f}% (大盤: +{q_pct*100:.3f}%) | 量能: {vr:.1f}x | {time.strftime('%H:%M')}")
        else:
            print("  -> 無")
    else:
        print("【攻擊型強勢股】今日大盤尚未突破 HOD，無攻擊型訊號。")
            
    if qqq_low_count > 0:
        print(f"\n【逆勢抗跌股】(今日共 {qqq_low_count} 根 K 線跌破 QQQ LOD，以下為各股最佳抗跌表現):")
        if best_resilient:
            for t, time, ret_s, ret_d, vr in sorted(best_resilient.values(), key=lambda x: x[3], reverse=True):
                print(f"  -> {t:<6} | 單根: {ret_s*100:+.3f}% | 相對開盤: +{ret_d*100:.2f}% | 量能: {vr:.1f}x | {time.strftime('%H:%M')}")
        else:
            print("  -> 無")
    else:
        print("\n【逆勢抗跌股】今日大盤尚未跌破 LOD，無抗跌型訊號。")
            
    if all_surge:
        print(f"\n【動能飆升警報】(今日所有單根3分K飆漲 >= 0.5%，共 {len(all_surge)} 筆):")
        for t, time, r, vr in sorted(all_surge, key=lambda x: x[2], reverse=True):
            print(f"  -> {t:<6} | 飆漲: +{r*100:.3f}% | 量能: {vr:.1f}x | {time.strftime('%H:%M')}")
    else:
        print("\n【動能飆升警報】今日無單根飆升 >= 0.5% 的標的。")

    # 將 HOD 即時警報寫入 Dashboard JSON
    dashboard_alerts = []
    for msg in realtime_alerts:
        import re
        ticker_match = re.search(r'\[.*?\] ([A-Z]+)', msg)
        ticker = ticker_match.group(1) if ticker_match else "N/A"
        
        alert_type = 'surge' if '🔥' in msg else ('up' if '🚀' in msg else 'down')
        title = msg.split(']')[0].split('[')[-1]
        time_str = datetime.now().strftime('%H:%M')
        desc = msg.split('|')[1].strip() if '|' in msg else msg
        
        dashboard_alerts.append({
            'symbol': ticker,
            'type': alert_type,
            'title': title,
            'desc': desc,
            'time': time_str
        })
        
    save_dashboard_data('hod_data.json', {
        'alerts': dashboard_alerts
    })

    return realtime_alerts, list(triggered_tickers)

def start_continuous_monitor():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🟢 啟動即時監控模式 (HOD 策略) ... (按 Ctrl+C 停止)")
    last_processed_time = None
    
    while True:
        try:
            market_df, stocks_dict = get_market_and_stocks_3m()
            if market_df is not None and not market_df.empty:
                latest_time = market_df.index[-1]
                
                if last_processed_time is None or latest_time > last_processed_time:
                    print(f"\n=============================================")
                    print(f"📊 發現新 K 線: {latest_time.strftime('%H:%M')} (HOD 策略)")
                    
                    alerts, triggered_tickers = scan_latest_kline_hod(market_df, stocks_dict, target_time=latest_time)
                    
                    if alerts and last_processed_time is not None:
                        msg = f"\n【HOD 即時警報】 {latest_time.strftime('%H:%M')}\n" + "\n".join(alerts)
                        print(f"📲 發現 {len(alerts)} 個即時訊號，正在發送 LINE 通知...")
                        send_line_notify(msg)
                        
                        for ticker in triggered_tickers:
                            img_path = generate_intraday_chart(ticker, stocks_dict[ticker], BENCHMARK, market_df, filename=f"hod_{ticker}.png")
                            if img_path:
                                send_line_notify(f"{ticker} 最新 3m K線比對圖", image_path=img_path)
                    
                    last_processed_time = latest_time
                else:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ⏳ 尚無新 K 線，等待中...")
                    
        except KeyboardInterrupt:
            print("\n🛑 監控已手動停止。")
            break
        except Exception as e:
            print(f"\n⚠️ 監控發生錯誤: {e}")
            
        time.sleep(60)

if __name__ == "__main__":
    start_continuous_monitor()
