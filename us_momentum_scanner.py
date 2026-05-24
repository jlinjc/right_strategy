# [LOCAL VERSION DIFF]: 新增了 check_squeeze (TTM Squeeze 擠壓檢測)，並整合基本面資料 (PE 本益比過濾)，以及將風險部位計算結果加入 LINE 通知中。
"""
us_momentum_scanner.py - 暴風動能策略選股引擎
===================================================
實現超級績效/Qullamaggie風格的動能交易策略：
1. 篩選過去 1、3、6 個月最強勢的股票 (Top %)
2. 尋找「高位緊密整理」(緊貼 10MA/20MA) 準備突破的標的
3. 尋找「情境轉折 (EP)」(跳空大漲 + 爆量) 的標的
4. 計算進出場建議 (ATR、停損點、移動停利)
"""

import os
import sys
import json
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = os.path.join(SCRIPT_DIR, 'Web_Dashboard')
OUTPUT_PATH = os.path.join(DASHBOARD_DIR, 'momentum_data.json')

# 載入監控清單與共用風控函式
try:
    from scanner_base import (
        AI_TECH_STOCKS, send_line_notify,
        calculate_position, TOTAL_CAPITAL, RISK_PER_TRADE
    )
except ImportError:
    AI_TECH_STOCKS = ["NVDA", "AMD", "TSM", "MSFT", "GOOGL", "META", "AAPL", "PLTR", "SMCI"]
    def send_line_notify(msg): pass
    def calculate_position(entry, stop): return 0, 0, 0
    TOTAL_CAPITAL = 10000
    RISK_PER_TRADE = 0.02


def calculate_adr(high, low, close, window=14):
    """計算 Average Daily Range (ADR %) 和 Average True Range (ATR)"""
    if len(close) < window:
        return 0, 0
    
    # ATR 計算
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window).mean().iloc[-1]
    
    # ADR % 計算 (Daily High / Low - 1) 的平均
    daily_range_pct = (high / low - 1) * 100
    adr_pct = daily_range_pct.rolling(window).mean().iloc[-1]
    
    return atr, adr_pct


def check_squeeze(close, high, low, window=20):
    """計算 TTM Squeeze 狀態"""
    if len(close) < window:
        return False
    
    ma = close.rolling(window).mean()
    std = close.rolling(window).std()
    
    # 布林通道 (Bollinger Bands)
    bb_up = ma + (2 * std)
    bb_low = ma - (2 * std)
    
    # 肯特納通道 (Keltner Channels)
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window).mean()
    
    kc_up = ma + (1.5 * atr)
    kc_low = ma - (1.5 * atr)
    
    # Squeeze 條件：布林通道被完全包覆在肯特納通道內
    is_squeezed = bool((bb_up.iloc[-1] < kc_up.iloc[-1]) and (bb_low.iloc[-1] > kc_low.iloc[-1]))
    return is_squeezed


def analyze_momentum(tickers):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 正在執行暴風動能策略掃描 ({len(tickers)} 檔)...")
    
    # 載入基本面資料以取得本益比 (PE)
    fundamentals = {}
    fund_path = os.path.join(DASHBOARD_DIR, 'fundamentals_data.json')
    if os.path.exists(fund_path):
        try:
            with open(fund_path, 'r', encoding='utf-8') as f:
                fdata = json.load(f)
                fundamentals = fdata.get('stocks', {})
        except Exception as e:
            print(f"⚠️ 無法讀取基本面資料: {e}")

    # 抓取 6 個月的資料以涵蓋 1M, 3M, 6M 報酬率計算
    tickers_str = " ".join(tickers)
    df = yf.download(tickers_str, period="6mo", interval="1d", progress=False, group_by='ticker')
    
    results = []
    
    for ticker in tickers:
        try:
            if ticker not in df.columns.levels[0]:
                continue
            
            stock_df = df[ticker].dropna(how='all')
            if len(stock_df) < 20:
                continue
                
            close = stock_df['Close']
            high = stock_df['High']
            low = stock_df['Low']
            open_p = stock_df['Open']
            volume = stock_df['Volume']
            
            last_close = float(close.iloc[-1])
            last_high = float(high.iloc[-1])
            last_low = float(low.iloc[-1])
            last_open = float(open_p.iloc[-1])
            last_vol = float(volume.iloc[-1])
            
            # 計算均線
            ma10 = float(close.rolling(10).mean().iloc[-1])
            ma20 = float(close.rolling(20).mean().iloc[-1])
            ma50 = float(close.rolling(50).mean().iloc[-1])
            
            # 計算歷史報酬率
            ret_1m = ((last_close / close.iloc[-21]) - 1) * 100 if len(close) >= 21 else 0
            ret_3m = ((last_close / close.iloc[-63]) - 1) * 100 if len(close) >= 63 else 0
            ret_6m = ((last_close / close.iloc[0]) - 1) * 100 if len(close) >= 120 else 0
            
            # 計算 ADR 和 ATR
            atr, adr_pct = calculate_adr(high, low, close, 14)
            if pd.isna(atr) or pd.isna(adr_pct):
                continue
                
            # 計算最近 10 天的波動收斂程度 (緊密整理 VCP)
            recent_high_10 = high.iloc[-10:].max()
            recent_low_10 = low.iloc[-10:].min()
            consolidation_range = ((recent_high_10 / recent_low_10) - 1) * 100
            
            # 判斷型態
            setup_type = "觀察中"
            priority = 0
            reasons = []
            
            # 條件 1: 情境轉折 (EP) - 當日跳空大漲 > 8% 且爆量
            avg_vol_20 = volume.rolling(20).mean().iloc[-2]
            gap_pct = ((last_open / close.iloc[-2]) - 1) * 100
            if gap_pct > 8 and last_vol > avg_vol_20 * 1.5:
                setup_type = "🔥 情境轉折 (EP)"
                priority = 3
                reasons.append(f"跳空大漲 {gap_pct:.1f}% 且成交量放大")
            
            # 條件 2: 日線緊密突破 (Breakout) - 1M/3M 強勢，且近期波動收斂，股價貼近 10MA/20MA
            elif ret_1m > 10 and consolidation_range < 12 and (ma20 * 0.98 <= last_close <= ma10 * 1.05):
                setup_type = "🎯 緊密整理突破"
                priority = 2
                reasons.append("前期強勢，近期波動收斂，緊貼均線")
            
            # 條件 3: 強勢延續 (Riding 10MA) - 沿著 10MA 穩步向上
            elif last_close > ma10 > ma20 and ret_1m > 15:
                setup_type = "📈 強勢延續中"
                priority = 1
                reasons.append("10MA 之上強勢多頭")
                
            # 若無特別型態，但動能極強
            elif ret_3m > 30 and last_close > ma50:
                setup_type = "👀 潛在強勢股"
                priority = 0
                reasons.append("長線動能強，等待整理")
            else:
                continue # 不滿足強勢條件的過濾掉
                
            # 判斷 Squeeze 與 基本面
            is_squeezed = check_squeeze(close, high, low)
            if is_squeezed:
                reasons.append("⚡ 處於 Squeeze 擠壓狀態 (動能醞釀中)")
                # 如果處於擠壓狀態，提升一點優先級，特別是對於緊密整理的股票
                if setup_type == "🎯 緊密整理突破":
                    priority += 0.5
                    
            pe = None
            if ticker in fundamentals:
                pe = fundamentals[ticker].get('pe_fwd')
                if pe is None:
                    pe = fundamentals[ticker].get('pe_ttm')
            
            if pe is not None and pe > 0 and pe < 20:
                reasons.append(f"💰 估值偏低 (PE: {pe})")
                
            # 計算操作建議點位
            # 停損永遠是當日最低點，或用 ATR 防守
            recommended_stop = max(last_low, last_close - (atr * 1.5))
            stop_dist_pct = ((last_close - recommended_stop) / last_close) * 100
            
            # 計算倉位
            shares, cost, actual_risk = calculate_position(last_close, recommended_stop)
            
            results.append({
                "ticker": ticker,
                "price": round(last_close, 2),
                "setup": setup_type,
                "priority": priority,
                "reasons": reasons,
                "returns": {
                    "1M": round(ret_1m, 1),
                    "3M": round(ret_3m, 1),
                    "6M": round(ret_6m, 1)
                },
                "indicators": {
                    "is_squeezed": is_squeezed,
                    "pe": pe
                },
                "volatility": {
                    "ADR_pct": round(adr_pct, 1),
                    "ATR": round(atr, 2),
                    "consolidation": round(consolidation_range, 1)
                },
                "levels": {
                    "MA10": round(ma10, 2),
                    "MA20": round(ma20, 2),
                    "entry_suggest": "開盤高點突破 (ORH)",
                    "stop_loss": round(recommended_stop, 2),
                    "stop_pct": round(stop_dist_pct, 1),
                    "trailing": "10MA" if setup_type != "🔥 情境轉折 (EP)" else "5MA/10MA"
                },
                "position": {
                    "shares": shares,
                    "cost": round(cost, 2),
                    "risk": round(actual_risk, 2)
                }
            })
            
        except Exception as e:
            print(f"  ⚠️ {ticker} 處理失敗: {e}")
            
    # 排序：優先級 > 3M報酬率
    results.sort(key=lambda x: (x['priority'], x['returns']['3M']), reverse=True)
    
    # 輸出至 JSON
    output = {
        "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "stocks": results
    }
    
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        
    print(f"  ✅ 動能選股完成！共篩選出 {len(results)} 檔股票。")
    
    # 針對高優先級 (EP或緊密整理突破) 發送 LINE 警報
    high_priority_results = [r for r in results if r['priority'] >= 2]
    if high_priority_results:
        alerts = ["【動能突破掃描報告】"]
        for r in high_priority_results:
            msg = f"{r['setup']} {r['ticker']}\n"
            msg += f"現價: ${r['price']}\n"
            msg += f"停損: ${r['levels']['stop_loss']} (-{r['levels']['stop_pct']}%)\n"
            msg += f"建議買入: {r['position']['shares']} 股 (約 ${r['position']['cost']:,.0f})\n"
            msg += f"風險金額: ${r['position']['risk']:,.0f} ({RISK_PER_TRADE*100}% 本金)\n"
            msg += "亮點: " + ", ".join(r['reasons'])
            alerts.append(msg)
            
        send_line_notify("\n\n".join(alerts))


if __name__ == '__main__':
    analyze_momentum(AI_TECH_STOCKS)
