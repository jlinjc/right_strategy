"""
sector_rs.py - 板塊相對強度閘門 (sector-level dual momentum)
=================================================================
領導股的拉回會被資金接回；落隊板塊裡「個股很強但板塊在崩」的拉回是陷阱。
2025 全年 S&P500 只有 3 個板塊(科技/通訊/工業)贏過大盤，且 2026 領導權
正往能源/必需消費輪動 —— 一檔 RS≥80 但所屬板塊正 roll over 的股，拉回
失敗率遠高於板塊也在領導的同分股。

作法：
  1. 用 11 個 SPDR 板塊 ETF 的「風險調整動能 (voladj = 6M報酬 / 日報酬波動)」
     —— 與個股 RS 同一把尺，但用 ETF 而非個股 RS 平均，避免和個股自身 RS
     循環論證。
  2. 跨 11 板塊橫斷面排百分位 → 每個板塊一個 sector_rs (0~100)。
  3. 個股經 yfinance .info 對應到 GICS 板塊 → 取得其 sector_rs。
  進場時：sector_rs 當「顯示 + 排序 tilt」，並可設一個保守地板(落後板塊才擋)。

⚠️ 無 look-ahead：ETF 動能是純價格 point-in-time；板塊歸屬是結構性穩定資訊。
"""

import os
import sys
import json
import warnings

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import yfinance as yf

from scanner_base import DASHBOARD_DIR

CACHE = os.path.join(DASHBOARD_DIR, 'sector_map.json')

# 落後板塊地板：sector_rs 低於此 → 視為「落隊板塊的假強股」，擋掉進場
# 預設 30 (約後 1/3 板塊)。設 None 則純顯示/排序、不擋。
SECTOR_RS_FLOOR = 30.0

# GICS 板塊(yfinance .info['sector'] 用語) → SPDR 板塊 ETF
SECTOR_ETF = {
    'Technology': 'XLK',
    'Financial Services': 'XLF',
    'Energy': 'XLE',
    'Healthcare': 'XLV',
    'Industrials': 'XLI',
    'Consumer Cyclical': 'XLY',
    'Consumer Defensive': 'XLP',
    'Utilities': 'XLU',
    'Basic Materials': 'XLB',
    'Real Estate': 'XLRE',
    'Communication Services': 'XLC',
}
ALL_ETFS = sorted(set(SECTOR_ETF.values()))


def fetch_sector_map(tickers, use_cache=True) -> dict:
    """回傳 {ticker: GICS_sector_name|None}，快取到 Web_Dashboard。"""
    if use_cache and os.path.exists(CACHE):
        try:
            with open(CACHE, encoding='utf-8') as f:
                data = json.load(f)
            if set(tickers).issubset(set(data.keys())):
                return data
        except Exception:
            pass

    print(f"  抓取 {len(tickers)} 檔板塊歸屬 (yfinance .info)...")
    out = {}
    for i, tk in enumerate(tickers):
        try:
            out[tk] = yf.Ticker(tk).info.get('sector')
        except Exception:
            out[tk] = None
        if (i + 1) % 20 == 0:
            print(f"    ...{i+1}/{len(tickers)}")
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    with open(CACHE, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  💾 {CACHE}")
    return out


def compute_sector_rs(etf_closes: pd.DataFrame = None) -> dict:
    """
    回傳 {sector_name: sector_rs_percentile(0~100)}。
    用 voladj (r126 / 日報酬波動) 對 11 板塊 ETF 橫斷面排名，取最新一日。
    etf_closes: 可外部注入 (欄=ETF代碼) 的收盤 DataFrame；None 則自行下載 1y。
    """
    if etf_closes is None:
        raw = yf.download(' '.join(ALL_ETFS), period='1y', interval='1d',
                          progress=False, group_by='ticker')
        cols = {}
        for etf in ALL_ETFS:
            try:
                s = raw[etf]['Close'].dropna()
                if len(s) >= 130:
                    cols[etf] = s
            except Exception:
                continue
        etf_closes = pd.DataFrame(cols).sort_index()

    if etf_closes is None or etf_closes.empty:
        return {}

    # voladj，與 rs_selection._rs_score('voladj') 同義
    r126 = etf_closes / etf_closes.shift(126) - 1
    vol = etf_closes.pct_change().rolling(126).std()
    score = (r126 / vol.replace(0, np.nan)).iloc[-1].dropna()
    if score.empty:
        return {}
    pct = score.rank(pct=True) * 100  # 跨 11 板塊百分位

    # ETF → sector_name 反查
    etf_to_sector = {v: k for k, v in SECTOR_ETF.items()}
    return {etf_to_sector[etf]: round(float(p), 0)
            for etf, p in pct.items() if etf in etf_to_sector}


def sector_context(sector_map: dict, sector_rs: dict, tk: str) -> dict:
    """
    回傳某股的板塊情境：
      sector        : GICS 板塊名 (None=未知)
      sector_rs     : 該板塊相對強度百分位 (None=未知)
      sector_lagging: 是否落後板塊(低於地板) → 進場應擋掉
    未知板塊一律放行 (sector_lagging=False)，避免缺資料就全擋。
    """
    sec = sector_map.get(tk)
    srs = sector_rs.get(sec) if sec else None
    lagging = False
    if srs is not None and SECTOR_RS_FLOOR is not None and srs < SECTOR_RS_FLOOR:
        lagging = True
    return {'sector': sec, 'sector_rs': srs, 'sector_lagging': lagging}


if __name__ == '__main__':
    srs = compute_sector_rs()
    print("板塊相對強度 (voladj, 0~100):")
    for s, p in sorted(srs.items(), key=lambda x: -x[1]):
        flag = '🟢' if p >= 50 else ('🔴' if (SECTOR_RS_FLOOR and p < SECTOR_RS_FLOOR) else '🟡')
        print(f"  {flag} {s:24} {p:5.0f}  ({SECTOR_ETF[s]})")
