"""research_data_2026q3.py - 2026-09 三題稽核(輪動/燈號/牛市跌深槓桿)共用資料載入器
全部用 yfinance auto_adjust=True(含息總報酬),快取在 research_cache/ 避免重抓。"""
import os, pickle, warnings
warnings.filterwarnings('ignore')
import pandas as pd, yfinance as yf

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research_cache', 'q3_prices.pkl')
US_CORE = ['SMH', 'QQQ', 'XLK', 'SOXX', 'SPY']
# 無後見之明的對照池:1998-12 就存在的 9 個 SPDR 產業 + 大盤類(不是事後挑的科技)
US_BROAD = ['XLB', 'XLE', 'XLF', 'XLI', 'XLK', 'XLP', 'XLU', 'XLV', 'XLY', 'SPY', 'QQQ', 'DIA', 'IWM', 'MDY']
LEV = ['QLD', 'SSO', 'TQQQ', 'UPRO', 'SOXL', 'USD', 'ROM']
TW_CORE = ['0050.TW', '0052.TW', '006208.TW', '0051.TW', '00757.TW', '00631L.TW', '0056.TW', '00692.TW']
MISC = ['HYG', 'LQD', '^VIX', 'BIL', '^IRX', 'SOXX']
ALL = sorted(set(US_CORE + US_BROAD + LEV + TW_CORE + MISC))


def load(refresh=False) -> dict:
    if os.path.exists(CACHE) and not refresh:
        return pickle.load(open(CACHE, 'rb'))
    raw = yf.download(' '.join(ALL), period='max', interval='1d', auto_adjust=True,
                      progress=False, group_by='ticker', threads=True)
    out = {}
    for tk in ALL:
        try:
            df = raw[tk].dropna(subset=['Close'])
            if len(df): out[tk] = df
        except Exception:
            pass
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    pickle.dump(out, open(CACHE, 'wb'))
    return out


def tw_clean(s: pd.Series) -> pd.Series:
    """台股 yfinance 假分割/尖刺修補(同 taiwan_status.clean,直接 import 以保持一致)"""
    import taiwan_status as T
    return T.clean(s)


if __name__ == '__main__':
    d = load(refresh=True)
    for tk, df in sorted(d.items()):
        print(f'{tk:10s} {df.index[0].date()} → {df.index[-1].date()}  n={len(df)}')
