"""
fundamentals_filter.py - 基本面濾網（當下快照，含污染警告）
================================================================
⚠️ 重要：yfinance 無逐日歷史基本面，只能取「當下」數值。把當下基本面套到
過去回測 = look-ahead 污染（今天成長高的股票，事後本來就會漲）。
因此本模組的回測結果只能「排除無效因子 / 挑方向」，不能當乾淨證據。
品質類(margin/ROE)污染最小(結構性穩定)；成長/估值類污染最重。

提供：
  fetch_fundamentals(tickers) → {ticker: {metrics}}，快取到 Web_Dashboard
  各種 make_f_* 濾網工廠（吃 fundamentals dict，回傳 (idx,df,bm)->bool）
"""

import os
import sys
import json
import warnings
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import yfinance as yf
from scanner_base import DASHBOARD_DIR
from rs_selection import RSRankScaledExit

CACHE = os.path.join(DASHBOARD_DIR, 'broad_fundamentals.json')
_METRICS = ['profitMargins', 'returnOnEquity', 'revenueGrowth', 'earningsGrowth',
            'forwardPE', 'trailingPegRatio', 'recommendationKey']


def fetch_fundamentals(tickers, use_cache=True) -> dict:
    if use_cache and os.path.exists(CACHE):
        try:
            with open(CACHE, encoding='utf-8') as f:
                data = json.load(f)
            if set(tickers).issubset(set(data.keys())):
                return data
        except Exception:
            pass
    print(f"  抓取 {len(tickers)} 檔當下基本面 (yfinance .info，較慢)...")
    out = {}
    for i, tk in enumerate(tickers):
        try:
            info = yf.Ticker(tk).info
            out[tk] = {m: info.get(m) for m in _METRICS}
        except Exception:
            out[tk] = {m: None for m in _METRICS}
        if (i + 1) % 20 == 0:
            print(f"    ...{i+1}/{len(tickers)}")
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    with open(CACHE, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  💾 {CACHE}")
    return out


#  濾網介面：f(tk) -> bool（None 值放行，避免缺資料就全擋）
def make_f_margin(fund, min_op_margin=0.10):
    """淨利潤率 > 門檻（品質，污染最小）"""
    def f(tk):
        m = fund.get(tk, {}).get('profitMargins')
        return m is None or m >= min_op_margin
    return f


def make_f_roe(fund, min_roe=0.15):
    def f(tk):
        v = fund.get(tk, {}).get('returnOnEquity')
        return v is None or v >= min_roe
    return f


def make_f_rev_growth(fund, min_g=0.10):
    """營收年增 > 門檻（成長，污染較重）"""
    def f(tk):
        v = fund.get(tk, {}).get('revenueGrowth')
        return v is None or v >= min_g
    return f


def make_f_earn_growth(fund, min_g=0.0):
    """盈餘年增 > 門檻"""
    def f(tk):
        v = fund.get(tk, {}).get('earningsGrowth')
        return v is None or v >= min_g
    return f


def make_f_not_expensive(fund, max_fpe=60.0):
    """前瞻本益比 < 門檻（排除極端估值/避免買在泡沫）"""
    def f(tk):
        v = fund.get(tk, {}).get('forwardPE')
        return v is None or (0 < v <= max_fpe)
    return f


def make_f_analyst_buy(fund):
    """分析師共識為 buy / strong_buy"""
    def f(tk):
        v = fund.get(tk, {}).get('recommendationKey')
        return v is None or v in ('buy', 'strong_buy')
    return f


class RSFundScaledExit(RSRankScaledExit):
    """
    在 RS 排名選股(voladj) + 定案進出場之上，再加基本面濾網(f(tk)->bool)。
    ⚠️ 基本面為當下快照，回測有 look-ahead 污染，僅供方向參考。
    """
    def __init__(self, fund_filters=None, **kw):
        super().__init__(**kw)
        self.fund_filters = fund_filters or []

    def scan(self, idx, ticker, df, benchmark_df):
        for f in self.fund_filters:
            if not f(ticker):
                return None
        return super().scan(idx, ticker, df, benchmark_df)
