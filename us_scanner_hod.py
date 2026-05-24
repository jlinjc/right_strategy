# [LOCAL VERSION DIFF]: 整合了大盤廣度計算 (calculate_market_breadth)，並加入 3m ATR 計算、VWAP 濾網與自動化倉位風險計算 (calculate_position)。
"""
us_scanner_hod.py - HOD/LOD 策略掃描器
使用今日最高點 (High of Day) 與最低點 (Low of Day) 作為動態基準。
"""

import time
import re
from datetime import datetime
import pandas as pd
import yfinance as yf
from scanner_base import (
    get_market_and_stocks_3m, calc_volume_ratio, send_line_notify, save_dashboard_data,
    SURGE_THRESHOLD, RESILIENT_MIN_DAY_RET, BENCHMARK, calculate_market_breadth, calculate_position
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

    # === 1. 計算系統級大盤廣度 ===
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

    # === 2. 下載日線資料，以便檢查個股中線趨勢是否大於 20MA ===
    tickers = list(stocks_dict.keys())
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 正在下載個股日線以檢查中線趨勢 (20MA)...")
    daily_trend_ok = {}
    try:
        daily_data = yf.download(" ".join(tickers), period="45d", interval="1d", progress=False, group_by='ticker')
        for ticker in tickers:
            if ticker in daily_data.columns.levels[0]:
                st_df = daily_data[ticker].dropna(how='all')
                if len(st_df) >= 20:
                    ma20 = st_df['Close'].rolling(window=20).mean().iloc[-1]
                    prev_close = st_df['Close'].iloc[-1]
                    daily_trend_ok[ticker] = (prev_close > ma20)
                else:
                    daily_trend_ok[ticker] = True
            else:
                daily_trend_ok[ticker] = True
    except Exception as e:
        print(f"⚠️ 下載日線資料失敗，預設均線過濾通過: {e}")
        for ticker in tickers:
            daily_trend_ok[ticker] = True

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
    print(f"大盤廣度: {breadth:.1f}% | 狀態: {breadth_label}")
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
            today_stock = df[df.index.date == latest_date].copy()
            if len(today_stock) < 2: continue
            
            # === 計算 3m K線 的 ATR ===
            tr1 = df['High'] - df['Low']
            tr2 = (df['High'] - df['Close'].shift(1)).abs()
            tr3 = (df['Low'] - df['Close'].shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            df['3mATR'] = tr.rolling(14).mean()
            today_stock['3mATR'] = df['3mATR'].loc[today_stock.index]

            # === 計算當日 VWAP ===
            tp = (today_stock['High'] + today_stock['Low'] + today_stock['Close']) / 3
            tpv = tp * today_stock['Volume']
            cum_tpv = tpv.cumsum()
            cum_vol = today_stock['Volume'].cumsum()
            today_stock['VWAP'] = cum_tpv / cum_vol
            
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
                
                # 攻擊型突破：大盤突破 HOD，個股也突破且幅度更大
                if qqq_state['is_new_high']:
                    stock_bp = (candle['High'] - stock_hod) / stock_hod if stock_hod > 0 else -1
                    if stock_bp > 0 and stock_bp > qqq_state['breakout_pct']:
                        # 疊加過濾器：1. 日線在 20MA 之上 2. 股價高於 VWAP 3. 成交量大於 1.5 倍 
                        is_above_vwap = candle['Close'] > candle['VWAP']
                        is_trend_ok = daily_trend_ok.get(ticker, True)
                        is_volume_ok = vol_ratio >= 1.5
                        
                        if is_above_vwap and is_trend_ok and is_volume_ok:
                            if ticker not in best_strong or stock_bp > best_strong[ticker][2]:
                                best_strong[ticker] = (ticker, t, stock_bp, qqq_state['breakout_pct'], vol_ratio)
                            
                            if t == target_time:
                                # 計算風控與建議倉位
                                atr = candle['3mATR'] if not pd.isna(candle['3mATR']) else (candle['Close'] * 0.005)
                                stop_loss = max(stock_lod, candle['Close'] - 2.5 * atr)
                                if stop_loss >= candle['Close'] * 0.995:
                                    stop_loss = candle['Close'] * 0.98  # 至少給 2% 空間
                                    
                                stop_pct = ((candle['Close'] - stop_loss) / candle['Close']) * 100
                                shares, cost, actual_risk = calculate_position(candle['Close'], stop_loss, custom_risk_pct=risk_pct)
                                
                                risk_warning = "⚠️ 觸及單筆 20% 資金上限 (風控鎖定 $2,000)" if cost >= 1990 else ""
                                breadth_status_str = f"🌐 【Anti-Gravity 系統廣度報告】\n📊 科技股高於 50MA 比例：{breadth:.1f}% {breadth_label}\n├─ {exposure_label}\n└─ 大盤 QQQ 單根：{qqq_ret*100:+.2f}%\n"
                                
                                if risk_pct == 0.0:
                                    realtime_alerts.append(
                                        f"{breadth_status_str}──────────────────────────────\n"
                                        f"🔥 [當沖突破 HOD] ➔ {ticker:<5}\n"
                                        f"⏱️ 時間：{t.strftime('%H:%M')} (3m K線)\n"
                                        f"──────────────────────────────\n"
                                        f"📈 價量與指標確認：\n"
                                        f" ├─ 當前價格：${candle['Close']:.2f} (突破前高 ${stock_hod:.2f})\n"
                                        f" ├─ 日內支撐：VWAP ${candle['VWAP']:.2f} (高於 VWAP 🟢)\n"
                                        f" ├─ 當根成交量：{candle['Volume']/1000:.1f}K (今日平均 {vol_ratio:.1f} 倍 ⚡)\n"
                                        f" └─ 對大盤強度：比 QQQ 強 (突破幅度比大盤高 +{(stock_bp - qqq_state['breakout_pct'])*100:.2f}% 🟢)\n"
                                        f"──────────────────────────────\n"
                                        f"⚠️ 系統防禦：大盤廣度過低 (Breadth < 40%)，暫停做多警報開倉！"
                                    )
                                else:
                                    realtime_alerts.append(
                                        f"{breadth_status_str}──────────────────────────────\n"
                                        f"🔥 [當沖突破 HOD] ➔ {ticker:<5}\n"
                                        f"⏱️ 時間：{t.strftime('%H:%M')} (3m K線)\n"
                                        f"──────────────────────────────\n"
                                        f"📈 價量與指標確認：\n"
                                        f" ├─ 當前價格：${candle['Close']:.2f} (突破前高 ${stock_hod:.2f})\n"
                                        f" ├─ 日內支撐：VWAP ${candle['VWAP']:.2f} (高於 VWAP 🟢)\n"
                                        f" ├─ 當根成交量：{candle['Volume']/1000:.1f}K (今日平均 {vol_ratio:.1f} 倍 ⚡)\n"
                                        f" └─ 對大盤強度：比 QQQ 強 (突破幅度比大盤高 +{(stock_bp - qqq_state['breakout_pct'])*100:.2f}% 🟢)\n"
                                        f"──────────────────────────────\n"
                                        f"🛡️ 機構級風控與倉位 (本金 $10,000 | 風險 {risk_pct*100}%):\n"
                                        f" ├─ 停損價位 (今日LOD)：${stop_loss:.2f} (-{stop_pct:.1f}%)\n"
                                        f" ├─ 建議買入：{shares} 股 (約 ${cost:,.0f})\n"
                                        f" └─ 實際承擔風險：${actual_risk:.1f} (本金 {actual_risk/100:.2f}%)\n"
                                        f"   {risk_warning}"
                                    )
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
        except Exception as e:
            print(f"⚠️ 掃描個股 {ticker} 發生錯誤: {e}")
            continue

    # === 輸出報告 ===
    if qqq_high_count > 0:
        print(f"【攻擊型強勢股】(今日共 {qqq_high_count} 根 K 線突破 QQQ HOD，以下為各股最強突破):")
        if best_strong:
            for t, time, s_pct, q_pct, vr in sorted(best_strong.values(), key=lambda x: x[2], reverse=True):
                print(f"  -> {t:<6} | 突破 HOD: +{s_pct*100:.3f}% (大盤: +{q_pct*100:.3f}%) | 量能: {vr:.1f}x | {time.strftime('%H:%M')}")
        else:
            print("  -> 無 (因未通過中線 20MA/日內 VWAP/1.5x爆量 等過濾條件)")
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
        ticker_match = re.search(r'➔ ([A-Z]+)', msg)
        if not ticker_match:
            ticker_match = re.search(r'\[.*?\] ([A-Z]+)', msg)
        ticker = ticker_match.group(1) if ticker_match else "N/A"
        
        alert_type = 'surge' if '🔥' in msg else ('up' if '🚀' in msg else 'down')
        title = "當沖突破 HOD" if '🔥' in msg else ("逆勢抗跌" if '🛡️' in msg else "動能飆升")
        time_str = datetime.now().strftime('%H:%M')
        desc = f"突破 HOD | 大盤廣度: {breadth:.1f}%"
        
        dashboard_alerts.append({
            'symbol': ticker,
            'type': alert_type,
            'title': title,
            'desc': desc,
            'time': time_str
        })
        
    save_dashboard_data('hod_data.json', {
        'alerts': dashboard_alerts,
        'market_breadth': breadth
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
                        msg = "\n" + "\n\n".join(alerts)
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
