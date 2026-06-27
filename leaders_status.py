"""
leaders_status.py - 台美股各產業龍頭股(衛星個股)狀態產生器
========================================================================
核心(指數ETF)用 core_status.py(美) / taiwan_status.py(台);這支管『個股衛星』——
台美股不同產業的龍頭股,套同一套趨勢引擎(站上200MA+不追高進場、跌破停損、RiskTarget下注)。

★ 真相提醒(寫進輸出):個股=衛星,非系統核心edge。MA200擇時對『乾淨趨勢指數』最有效;
  個股有 single-name 風險 + 財報跳空(earnings gap 會跳空穿停損)。龍頭股動能較乾淨但仍須:
  小部位、分散產業、看收盤、財報前減碼。長多龍頭(台積電/NVDA)也可『核心長抱』不擇時。

★ 個股參數(較指數寬,防財報/單一股波動鋸齒):進場不追高 close<MA50×1.10、
  停損 MA200×0.95(留緩衝)、RiskTarget cap 1.0(個股不上槓桿)。
★ .TW 資料 splice 清理(同 taiwan_status,yfinance台股有假分割/尖刺)。

輸出 Web_Dashboard/leaders_status.json。用法: python leaders_status.py
"""
import os, sys, json, warnings
from datetime import datetime
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import yfinance as yf
from scanner_base import DASHBOARD_DIR

# ── 台美股各產業龍頭股(market, sector, [(ticker,name)]) ──
LEADERS = {
 'US': {
   '半導體': [('NVDA','輝達'),('AVGO','博通'),('AMD','超微'),('TSM','台積電ADR')],
   '軟體雲端': [('MSFT','微軟'),('GOOGL','Alphabet'),('META','Meta'),('ORCL','甲骨文')],
   '消費電子/電商': [('AAPL','蘋果'),('AMZN','亞馬遜')],
   '電動車': [('TSLA','特斯拉')],
   '金融支付': [('JPM','摩根大通'),('V','Visa'),('MA','萬事達')],
   '醫療生技': [('LLY','禮來'),('UNH','聯合健康'),('NVO','諾和諾德')],
   '能源': [('XOM','埃克森美孚'),('CVX','雪佛龍')],
   '消費必需': [('COST','好市多'),('WMT','沃爾瑪')],
   '工業': [('CAT','卡特彼勒'),('GE','奇異')],
 },
 'TW': {
   '半導體': [('2330.TW','台積電'),('2454.TW','聯發科'),('2303.TW','聯電')],
   'AI伺服器/代工': [('2317.TW','鴻海'),('2382.TW','廣達'),('3231.TW','緯創'),('2376.TW','技嘉')],
   '被動元件/IC': [('2327.TW','國巨'),('3034.TW','聯詠')],
   '金融': [('2881.TW','富邦金'),('2882.TW','國泰金'),('2891.TW','中信金')],
   '電信': [('2412.TW','中華電')],
   '塑化傳產': [('1301.TW','台塑'),('1303.TW','南亞'),('2002.TW','中鋼')],
   '航運': [('2603.TW','長榮'),('2609.TW','陽明')],
   '食品零售': [('1216.TW','統一'),('2912.TW','統一超')],
   '光學/生技': [('3008.TW','大立光'),('6446.TW','藥華藥')],
 },
}
MA, MA_FAST = 200, 50
PANIC_VIX = 30.0
# 個股參數:較指數寬,RiskTarget cap 1.0(個股不上槓桿)
P_THR, P_BUF, P_BUDGET, P_CAP = 1.10, 0.95, 0.12, 1.0
STOP_FLOOR = 0.03   # 個股停損距下限較寬(波動大)


def clean(s):
    """.TW yfinance 假分割/尖刺 splice 清理(同 taiwan_status)。"""
    v = s.dropna().astype(float).values.copy(); idx = s.dropna().index
    for i in range(1, len(v)):
        if v[i-1] <= 0 or v[i] <= 0: continue
        ch = v[i]/v[i-1]-1
        if abs(ch) > 0.35:
            spike = False
            if i+1 < len(v) and v[i] > 0:
                nx = v[i+1]/v[i]-1
                if abs(nx) > 0.30 and (nx > 0) != (ch > 0): spike = True
            v[i] = (v[i-1]+v[i+1])/2.0 if spike else v[i]
            if not spike: v[i:] = v[i:]*(v[i-1]/v[i])
    return pd.Series(v, index=idx)


def stock_signal(close, name, sector, market, vix_last):
    isTW = market == 'TW'
    ma_s = close.rolling(MA).mean()
    ma = float(ma_s.iloc[-1]); last = float(close.iloc[-1])
    ma50 = float(close.iloc[-MA_FAST:].mean())
    dist = (last/ma-1)*100; dist50 = (last/ma50-1)*100
    exit_price = round(ma*P_BUF, 2); entry_cap = round(ma50*P_THR, 2)
    stop_risk = round((last-exit_price)/last*100, 1)
    entry_cap_pct = round((entry_cap/last-1)*100, 1)
    exit_pct = round((exit_price/last-1)*100, 1)
    rs = None
    if len(close) > 130:
        r126 = float(close.iloc[-1]/close.iloc[-127]-1)
        v126 = float(close.pct_change().iloc[-126:].std()*(252**0.5))
        rs = round(r126/v126, 3) if v126 > 0 else None
    stop_frac = max(stop_risk/100.0, STOP_FLOOR)
    expo = round(min(P_BUDGET/stop_frac, P_CAP), 2)

    if last >= ma:
        state = 'risk_on'
    elif last < exit_price:
        state = 'risk_off'
    else:
        state = 'warning'
    if last < ma:
        entry_state, entry_action = 'no_entry', f'不進(在200MA之下,趨勢轉弱)'
    elif last >= entry_cap:
        entry_state = 'extended'
        entry_action = (f'追高,等拉回 {"現價回到+" if isTW else "≤$"}{entry_cap_pct if isTW else entry_cap}{"%以內" if isTW else ""}'
                        f'(距50MA +{dist50:.0f}% > 門檻 +{(P_THR-1)*100:.0f}%)')
    else:
        entry_state = 'can_enter'
        entry_action = (f'可進(距50MA +{dist50:.0f}% ≤ +{(P_THR-1)*100:.0f}%);停損距 -{stop_risk:.0f}% → 曝險 {expo*100:.0f}%')
    return {'name': name, 'sector': sector, 'market': market, 'currency': 'TWD' if isTW else 'USD',
            'close': round(last, 2), 'ma200': round(ma, 2), 'dist_pct': round(dist, 2), 'dist50_pct': round(dist50, 2),
            'exit_price': exit_price, 'entry_cap': entry_cap, 'stop_risk_pct': stop_risk,
            'entry_cap_pct': entry_cap_pct, 'exit_pct': exit_pct, 'suggested_expo': expo,
            'rs_score': rs, 'state': state, 'entry_state': entry_state, 'entry_action': entry_action}


def main():
    allt = []
    for mk, secs in LEADERS.items():
        for sec, lst in secs.items():
            for tk, nm in lst: allt.append((tk, nm, sec, mk))
    tickers = [t[0] for t in allt] + ['^VIX']
    print(f"📥 下載 台美股龍頭個股 {len(allt)} 檔 + VIX (2y)...")
    raw = yf.download(' '.join(tickers), period='2y', interval='1d',
                      progress=False, group_by='ticker')

    def series(tk):
        try:
            s = raw[tk]['Close']; s = (s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s).dropna()
            return clean(s) if tk.endswith('.TW') else s
        except Exception:
            return None

    vix_last = None
    vs = series('^VIX')
    if vs is not None and len(vs) > 0: vix_last = float(vs.iloc[-1])

    out = {'US': {}, 'TW': {}}
    counts = {'US': 0, 'TW': 0}
    for tk, nm, sec, mk in allt:
        s = series(tk)
        if s is None or len(s) < MA:
            continue
        sig = stock_signal(s, nm, sec, mk, vix_last)
        sig['ticker'] = tk
        out[mk].setdefault(sec, []).append(sig)
        counts[mk] += 1

    # RS 排序每產業(讓最強的排前面)
    for mk in out:
        for sec in out[mk]:
            out[mk][sec].sort(key=lambda d: (d['rs_score'] if d['rs_score'] is not None else -9), reverse=True)

    data = {
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'vix': round(vix_last, 2) if vix_last is not None else None,
        'counts': counts,
        'note': ('個股=衛星非核心edge。MA200擇時對乾淨趨勢指數最有效;個股有single-name+財報跳空風險→'
                 '小部位、分散產業、看收盤、財報前減碼。長多龍頭(台積電/NVDA)也可核心長抱不擇時。'),
        'params_note': f'個股參數:不追高<MA50×{P_THR}、停損MA200×{P_BUF}(較寬防財報鋸齒)、RiskTarget cap{P_CAP}(個股不上槓桿)。',
        'data_note': '台股個股 yfinance 絕對價位可能失真→看「%」(距200MA/停損距);下單以券商報價換算。',
        'leaders': out,
    }
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    path = os.path.join(DASHBOARD_DIR, 'leaders_status.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    em = {'risk_on': '🟢', 'risk_off': '🔴', 'warning': '🟡'}
    enem = {'can_enter': '🟢可進', 'extended': '🟡別追', 'no_entry': '🔴不碰'}
    print(f"\n  VIX {vix_last:.1f}" if vix_last else "")
    for mk in ['US', 'TW']:
        flag = '🇺🇸' if mk == 'US' else '🇹🇼'
        print(f"\n  {flag} {mk} 產業龍頭({counts[mk]}檔):")
        for sec, lst in out[mk].items():
            print(f"   ── {sec} ──")
            for d in lst:
                px = f"{'NT$' if mk=='TW' else '$'}{d['close']}"
                print(f"     {em[d['state']]} {d['ticker']:<9}{d['name']:<10} {px:<10} 距200MA{d['dist_pct']:+.0f}% "
                      f"距50MA{d['dist50_pct']:+.0f}% RS{d['rs_score'] if d['rs_score'] is not None else '—'}  {enem[d['entry_state']]}")
    print(f"\n💾 {path}")


if __name__ == '__main__':
    main()
