"""research_b8_data.py - 個股衛星上限測試用資料:今天的 S&P500 成分股(明知有生存者偏差)完整歷史 OHLC"""
import os, json, pickle, sys, warnings
warnings.filterwarnings('ignore')
import pandas as pd, yfinance as yf
OUT = os.path.join('research_cache', 'sp500_ohlc.pkl')
if __name__ == '__main__':
    tks = json.load(open('Web_Dashboard/sp500_universe.json', encoding='utf-8'))
    tks = [t.replace('.', '-') for t in tks] + ['SPY', 'QQQ']
    res = {}
    for i in range(0, len(tks), 100):
        chunk = tks[i:i + 100]
        raw = yf.download(' '.join(chunk), start='2005-01-01', auto_adjust=True, progress=False,
                          group_by='ticker', threads=True)
        for tk in chunk:
            try:
                df = raw[tk][['Open', 'High', 'Low', 'Close']].dropna()
                if len(df) > 300: res[tk] = df
            except Exception:
                pass
        print(f'{min(i + 100, len(tks))}/{len(tks)} 累計 {len(res)} 檔', flush=True)
    pickle.dump(res, open(OUT, 'wb'))
    print('saved', OUT, len(res))
