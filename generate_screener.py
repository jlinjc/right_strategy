# [LOCAL VERSION DIFF]: 全新加入的檔案。用於產生前端 Dashboard 所需的 Strategy Screener 候選清單資料。
import os
import sys
import json
import time
from datetime import datetime
import pandas as pd
import yfinance as yf

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from scanner_base import AI_TECH_STOCKS, DASHBOARD_DIR

def check_squeeze(close, high, low, window=20):
    if len(close) < window: return False
    ma = close.rolling(window).mean()
    std = close.rolling(window).std()
    bb_up, bb_low = ma + (2 * std), ma - (2 * std)
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(window).mean()
    kc_up, kc_low = ma + (1.5 * atr), ma - (1.5 * atr)
    return bool((bb_up.iloc[-1] < kc_up.iloc[-1]) and (bb_low.iloc[-1] > kc_low.iloc[-1]))

def generate_screener():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔍 開始掃描篩選器指標 ({len(AI_TECH_STOCKS)} 檔)...")
    
    # 讀取現有的 fundamentals_data.json 以取得 PE
    fundamentals = {}
    fund_path = os.path.join(DASHBOARD_DIR, 'fundamentals_data.json')
    if os.path.exists(fund_path):
        try:
            with open(fund_path, 'r', encoding='utf-8') as f:
                fdata = json.load(f)
                fundamentals = fdata.get('stocks', {})
        except Exception as e:
            print(f"⚠️ 無法讀取基本面資料: {e}")

    # 下載 3 個月的資料供 Squeeze 運算
    df = yf.download(" ".join(AI_TECH_STOCKS), period="3mo", interval="1d", progress=False, group_by='ticker')
    
    results = []
    
    for ticker in AI_TECH_STOCKS:
        try:
            t_data = df[ticker] if len(AI_TECH_STOCKS) > 1 else df
            t_data = t_data.dropna(subset=['Close'])
            if len(t_data) < 20: continue
            
            close = t_data['Close']
            high = t_data['High']
            low = t_data['Low']
            
            is_sq = check_squeeze(close, high, low)
            
            # 從 fundamentals 讀取 PE，若無則嘗試向 yf 獲取 (這比較慢，所以預設為 None)
            pe = None
            if ticker in fundamentals:
                pe = fundamentals[ticker].get('pe_fwd') or fundamentals[ticker].get('pe_ttm')
                
            # 也可以取得當前價格
            price = round(close.iloc[-1], 2)
            
            # 1M 報酬率 (抓取前21個交易日的價格)
            ret_1m = 0
            if len(close) >= 21:
                ret_1m = round((close.iloc[-1] - close.iloc[-21]) / close.iloc[-21] * 100, 1)

            results.append({
                "ticker": ticker,
                "price": price,
                "returns": {
                    "1M": ret_1m
                },
                "indicators": {
                    "is_squeezed": is_sq,
                    "pe": pe
                }
            })
            
        except Exception as e:
            print(f"⚠️ {ticker} 資料處理錯誤: {e}")
            
    # 儲存為 screener_data.json
    output = {
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stocks": results
    }
    
    out_path = os.path.join(DASHBOARD_DIR, 'screener_data.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ 篩選器資料已儲存至 {out_path} (共 {len(results)} 檔)")

if __name__ == "__main__":
    generate_screener()
