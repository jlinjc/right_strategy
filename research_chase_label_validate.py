"""
research_chase_label_validate.py — 驗證「追高·別追」標籤到底該不該存在
========================================================================
Jason 質疑:4~6月那種穩定緩漲,系統整段狂貼「追高別追」,會不會根本錯過行情。
用 generate_kbar_annotations.annotate() 產生的逐日標籤(PIT,無未來函數),
對每一類標籤算「事後21日/63日報酬」,誠實回答:「追高別追」這格歷史上到底是不是該躲開。

拆兩個子格分開看(程式碼裡本來就分兩支):
  - amber「追高·別追」          = 平靜牛市條件不成立時的預設
  - lime 「追高·可小額試單」    = 2026-07-18 已升級的平靜牛市子集(程式碼註解稱 29✅:3❌)
對照組:同一檔股票『可買(足量)』(green, buy_full)當基準。
"""
import warnings
warnings.filterwarnings('ignore')
import numpy as np, yfinance as yf
import core_status as C
import taiwan_status as T
from generate_kbar_annotations import annotate, US, TW, US_CANARY, TW_CANARY, _clean_ohlc

def get(raw, sym, is_tw=False):
    try:
        d = raw[sym][['Open', 'High', 'Low', 'Close', 'Volume']]
        return _clean_ohlc(d, is_tw)
    except Exception:
        return None

def main():
    all_syms = [s for s, _ in US + TW] + ['HYG', 'LQD', 'SOXX', '^VIX']
    all_syms = list(dict.fromkeys(all_syms))
    print(f"下載 {len(all_syms)} 檔...")
    raw = yf.download(' '.join(all_syms), start='2006-01-01', interval='1d',
                      progress=False, group_by='ticker', auto_adjust=True, threads=True)
    vix = raw['^VIX']['Close'].dropna()
    canary_raw = {}
    for tk in ['HYG', 'LQD', 'SOXX']:
        d = get(raw, tk)
        if d is not None:
            canary_raw[tk] = d['Close']

    buckets = {'amber_chase': [], 'lime_chase': [], 'buy_full': [], 'buy_small': []}
    per_symbol = {}

    for sym, name in US + TW:
        is_tw = sym.endswith('.TW')
        d = get(raw, sym, is_tw)
        if d is None or len(d) < C.MA + 30:
            continue
        params = dict((T.TW_PARAMS.get(sym) if is_tw else C.PARAMS.get(sym)) or C.DEFAULT_PARAM, _sym=sym)
        canary_closes = {k: canary_raw[k] for k in (TW_CANARY if is_tw else US_CANARY) if k in canary_raw}
        bars, extra = annotate(d, params, vix, canary_closes)
        n = len(bars)
        sym_bucket = {'amber_chase': [], 'lime_chase': [], 'buy_full': [], 'buy_small': []}
        for i, b in enumerate(bars):
            lbl = b['label']
            key = None
            if lbl.startswith('追高·別追'):
                key = 'amber_chase'
            elif lbl.startswith('追高·可小額試單'):
                key = 'lime_chase'
            elif lbl.startswith('可買(足量'):
                key = 'buy_full'
            elif lbl.startswith('可小買'):
                key = 'buy_small'
            if key is None:
                continue
            if i + 21 < n:
                f21 = bars[i + 21]['close'] / b['close'] - 1
                sym_bucket[key].append(f21)
                buckets[key].append(f21)
        per_symbol[sym] = sym_bucket
        print(f"  {sym:11} n={n}  追別追={len(sym_bucket['amber_chase']):4d}  追小額={len(sym_bucket['lime_chase']):4d}"
              f"  可買足量={len(sym_bucket['buy_full']):4d}  可小買={len(sym_bucket['buy_small']):4d}")

    print("\n=== 全樣本(US+TW合併)事後21日報酬 ===")
    print(f"{'類別':16}{'n':>6}{'均值%':>9}{'中位%':>9}{'勝率(>0)%':>11}{'std%':>8}")
    for key, label in [('amber_chase', '追高·別追(amber)'), ('lime_chase', '追高·可小額(lime)'),
                        ('buy_full', '可買·足量(green)'), ('buy_small', '可小買(green)')]:
        arr = np.array(buckets[key])
        if len(arr) == 0:
            continue
        print(f"{label:16}{len(arr):6d}{arr.mean()*100:9.2f}{np.median(arr)*100:9.2f}"
              f"{(arr>0).mean()*100:11.1f}{arr.std()*100:8.2f}")

    print("\n=== 逐標的:追高·別追(amber) vs 可買·足量(green) 事後21日均值% ===")
    print(f"{'symbol':11}{'追別追n':>9}{'追別追均值%':>13}{'可買n':>8}{'可買均值%':>11}{'差(追-買)%':>12}")
    for sym, b in per_symbol.items():
        ac = np.array(b['amber_chase']); bf = np.array(b['buy_full'])
        if len(ac) < 10 or len(bf) < 10:
            continue
        print(f"{sym:11}{len(ac):9d}{ac.mean()*100:13.2f}{len(bf):8d}{bf.mean()*100:11.2f}{(ac.mean()-bf.mean())*100:12.2f}")

if __name__ == '__main__':
    main()
