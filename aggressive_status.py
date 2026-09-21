"""
aggressive_status.py - 進取模式(TQQQ/SOXL 槓桿ETF)操作狀態
========================================================================
給「$30k 以下、要成長、能扛回撤」的階段用。
規則:用母指數(QQQ→TQQQ / SMH→SOXL)的 200MA + 信用哨(HYG/LQD/SOXX)當保命開關。
  母指數站上200MA 且 信用哨健康 → 綠燈,可持有/進場槓桿ETF
  母指數收盤跌破200MA 或 信用哨任一破 → 🔴賣光,轉現金
輸出 Web_Dashboard/aggressive_status.json。用法: python aggressive_status.py
"""
import os, sys, json, warnings
from datetime import datetime
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
import pandas as pd, yfinance as yf
from scanner_base import DASHBOARD_DIR
MA = 200
CANARY = ['HYG', 'LQD', 'SOXX']
# (槓桿ETF, 母指數訊號, 說明, 回測CAGR/MDD)
VEHICLES = [
    {'etf': 'TQQQ', 'signal': 'QQQ', 'name': '3x那斯達克', 'cagr': '+39%', 'mdd': '-47%', 'tag': '較能睡·先驗證心臟'},
    {'etf': 'SOXL', 'signal': 'SMH', 'name': '3x半導體', 'cagr': '+61%', 'mdd': '-70%', 'tag': '報酬最高·最兇'},
]

def main():
    tks = list({v['etf'] for v in VEHICLES} | {v['signal'] for v in VEHICLES} | set(CANARY) | {'^VIX'})
    print(f"📥 下載 進取模式標的 ({'/'.join(tks)}, 2y)...")
    raw = yf.download(' '.join(tks), period='2y', interval='1d', progress=False, group_by='ticker')
    def ser(tk):
        try:
            c = raw[tk]['Close']; return (c.iloc[:, 0] if hasattr(c, 'columns') else c).dropna()
        except Exception:
            return None
    def info(tk):
        s = ser(tk)
        if s is None or len(s) < MA: return None
        ma = float(s.rolling(MA).mean().iloc[-1]); last = float(s.iloc[-1]); ma50 = float(s.iloc[-50:].mean())
        return {'close': round(last, 2), 'ma200': round(ma, 2), 'dist_pct': round((last/ma-1)*100, 1),
                'dist50_pct': round((last/ma50-1)*100, 1), 'above': last >= ma}
    vix = ser('^VIX'); vix_last = round(float(vix.iloc[-1]), 1) if vix is not None and len(vix) else None
    # 信用哨
    can = {}
    healthy = 0
    for tk in CANARY:
        d = info(tk)
        if d: can[tk] = d; healthy += 1 if d['above'] else 0
    canary_ok = healthy == len(can)
    out_veh = []
    for v in VEHICLES:
        etf = info(v['etf']); sig = info(v['signal'])
        if not etf or not sig: continue
        green = sig['above'] and canary_ok
        clean_entry = sig['above'] and sig['dist50_pct'] <= 8   # 母指數不追高
        out_veh.append({
            'etf': v['etf'], 'signal_tk': v['signal'], 'name': v['name'], 'tag': v['tag'],
            'cagr': v['cagr'], 'mdd': v['mdd'],
            'etf_price': etf['close'], 'sig_close': sig['close'], 'sig_ma200': sig['ma200'],
            'sig_buffer_pct': round((sig['close']/sig['ma200']-1)*100, 1),
            'sig_dist50': sig['dist50_pct'],
            'state': 'green' if green else 'red',
            'clean_entry': clean_entry,
            'action': ('🟢 綠燈：可持有/進場 ' + v['etf'] + ('（母指數進場乾淨）' if clean_entry else '（母指數已延伸,進場偏追高·小心）'))
                      if green else ('🔴 賣出：' + v['signal'] + ' 跌破200MA' if not sig['above'] else '🔴 賣出：信用哨示警') + ' → ' + v['etf'] + ' 轉現金',
            'sell_line_note': f"{v['signal']} 收盤跌破 ${sig['ma200']:.0f}(其200MA) → 賣光 {v['etf']}",
        })
    data = {
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'vix': vix_last,
        'canary': {'ok': canary_ok, 'healthy': healthy, 'total': len(can), 'assets': can},
        'vehicles': out_veh,
        'note': ('進取模式給「$30k以下·要成長·能扛深回撤」用。一次全投(回測證實分批少賺);'
                 '母指數跌破200MA或信用哨破→賣光;只看週收盤;狂存錢。本金長大→降槓桿。'),
        'rules': ['母指數站上200MA + 信用哨(HYG/LQD/SOXX)健康 = 綠燈,持有/進場',
                  '母指數收盤跌破200MA 或 信用哨任一破 = 🔴賣光轉現金',
                  '只看週五收盤 · 破線才動 · 別憑感覺砍 · 狂存錢'],
    }
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    path = os.path.join(DASHBOARD_DIR, 'aggressive_status.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n  信用哨: {'🟢健康' if canary_ok else '🔴示警'} {healthy}/{len(can)}  VIX {vix_last}")
    for v in out_veh:
        print(f"  {'🟢' if v['state']=='green' else '🔴'} {v['etf']}({v['name']}) ${v['etf_price']}  "
              f"訊號{v['signal_tk']} ${v['sig_close']} vs 200MA ${v['sig_ma200']}(緩衝{v['sig_buffer_pct']:+.0f}%) → {v['action']}")
    print(f"💾 {path}")

if __name__ == '__main__':
    main()
