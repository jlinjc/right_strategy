# [LOCAL VERSION DIFF]: 全新加入的檔案。終端機介面的 ASCII 報價看板或掃描結果顯示工具。
import yfinance as yf
import pandas as pd
import json

try:
    from scanner_base import AI_TECH_STOCKS
except:
    AI_TECH_STOCKS = ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'META']

def check_squeeze(close, high, low, window=20):
    if len(close) < window: return False
    ma = close.rolling(window).mean()
    std = close.rolling(window).std()
    bb_up, bb_low = ma + (2 * std), ma - (2 * std)
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(window).mean()
    kc_up, kc_low = ma + (1.5 * atr), ma - (1.5 * atr)
    return bool((bb_up.iloc[-1] < kc_up.iloc[-1]) and (bb_low.iloc[-1] > kc_low.iloc[-1]))

results = []
df = yf.download(" ".join(AI_TECH_STOCKS), period="3mo", interval="1d", progress=False, group_by='ticker')

for ticker in AI_TECH_STOCKS:
    try:
        t_data = df[ticker] if len(AI_TECH_STOCKS) > 1 else df
        t_data = t_data.dropna(subset=['Close'])
        if len(t_data) < 20: continue
        is_sq = check_squeeze(t_data['Close'], t_data['High'], t_data['Low'])
        
        info = yf.Ticker(ticker).info
        pe = info.get('forwardPE') or info.get('trailingPE')
        
        if is_sq or (pe is not None and pe > 0 and pe < 25):
            results.append({
                'ticker': ticker,
                'pe': round(pe, 1) if pe else None,
                'is_sq': is_sq
            })
    except Exception as e:
        pass

print(json.dumps(results))
