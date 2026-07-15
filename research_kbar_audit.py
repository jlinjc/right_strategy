"""
research_kbar_audit.py — 審計 K 棒逐日標籤 vs 事後實際報酬(找不合邏輯/更好操作)
========================================================================
標籤本身是 PIT(只用當天前資料);這裡用『後見之明』的未來報酬回頭評分每個標籤——
這對審計是公平的(我們在評『那天的建議事後對不對』,不是把未來塞進標籤)。

診斷邏輯:
  ● 標「別買/觀望/追高/偏貴」但之後大漲 → 太保守(其實該勇敢買)
  ● 標「可買/足量」但之後大跌       → 太樂觀
  ● 標「出場/信用清倉」但之後續漲   → 洗損(賣在反彈前)
逐 ticker 印:各分類的未來21/63日報酬 + 命中率 + 最離譜的錯標日期。
"""
import sys, json, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

KB = 'Web_Dashboard/kbar_annotations.json'


def cat(label):
    if '信用示警' in label: return 'credit_clear 信用清倉'
    if '跌破線' in label:   return 'exit 跌破出場'
    if '恐慌觀察' in label: return 'panic_watch 恐慌觀察'
    if '逼近停損' in label: return 'near_stop 逼近停損'
    if '爆量突破' in label: return 'blowoff 爆量別追'
    if '頭上壓力' in label: return 'resistance 頭上壓力'
    if '鋸齒' in label:     return 'chop 鋸齒觀望'
    if '追高' in label:     return 'chase 追高別追'
    if '偏貴' in label:     return 'expensive 偏貴少量'
    if '可小買' in label:   return 'buy_small 可小買'
    if '可買' in label:     return 'buy_full 可買足量'
    return 'other'


# 每個分類的『隱含動作方向』:+1=叫你買/抱、0=中性、-1=叫你別買/出場。
IMPLIED = {
    'buy_full 可買足量': +1, 'buy_small 可小買': +1, 'expensive 偏貴少量': +1,
    'chase 追高別追': -1, 'chop 鋸齒觀望': -1, 'resistance 頭上壓力': -1,
    'blowoff 爆量別追': -1, 'near_stop 逼近停損': -1, 'panic_watch 恐慌觀察': 0,
    'exit 跌破出場': -1, 'credit_clear 信用清倉': -1,
}


def analyze(tk, bars):
    df = pd.DataFrame(bars)
    df['dt'] = pd.to_datetime(df['time'])
    close = df['close'].values.astype(float)
    n = len(df)
    f21 = np.array([close[i+21]/close[i]-1 if i+21 < n else np.nan for i in range(n)])
    f63 = np.array([close[i+63]/close[i]-1 if i+63 < n else np.nan for i in range(n)])
    df['c'] = [cat(l) for l in df['label']]
    df['f21'] = f21 * 100
    df['f63'] = f63 * 100

    print("=" * 84)
    print(f"  {tk}  {df['dt'].iloc[0].date()}→{df['dt'].iloc[-1].date()}  ({n} 根K)")
    print("=" * 84)
    print(f"  基準:全期任一天買,之後21日均報酬 {np.nanmean(f21)*100:+.2f}% / 63日 {np.nanmean(f63)*100:+.2f}%")
    print(f"\n  {'分類':22}{'占比':>6}{'未來21d':>10}{'勝率':>7}{'未來63d':>10}{'判讀':>4}")
    order = ['buy_full 可買足量','buy_small 可小買','expensive 偏貴少量','chase 追高別追',
             'chop 鋸齒觀望','resistance 頭上壓力','blowoff 爆量別追','near_stop 逼近停損',
             'panic_watch 恐慌觀察','exit 跌破出場','credit_clear 信用清倉']
    base21 = np.nanmean(f21) * 100
    for c in order:
        g = df[df['c'] == c]
        if len(g) == 0:
            continue
        m21, m63 = g['f21'].mean(), g['f63'].mean()
        hit = (g['f21'] > 0).mean() * 100
        imp = IMPLIED.get(c, 0)
        # 旗標:叫你別買(-1)但之後>基準,或叫你買(+1)但之後<0
        flag = ''
        if imp == -1 and m21 > base21 + 1: flag = '⚠️太保守(之後反漲)'
        elif imp == -1 and m21 > 0 and m63 > 3: flag = '🟡別買但續漲'
        elif imp == +1 and m21 < 0: flag = '⚠️太樂觀(之後跌)'
        print(f"  {c:22}{len(g)/n*100:5.0f}%{m21:>9.2f}%{hit:>6.0f}%{m63:>9.2f}%   {flag}")

    # 洗損:每次進入 exit/credit_clear 的第一天,之後21日index報酬(>0=賣早了)
    red = df['c'].isin(['exit 跌破出場','credit_clear 信用清倉']).values
    starts = [i for i in range(n) if red[i] and (i == 0 or not red[i-1])]
    ev = [f21[i]*100 for i in starts if not np.isnan(f21[i])]
    if ev:
        print(f"\n  🔁 洗損檢查:{len(ev)} 次出場事件,之後21日大盤中位 {np.median(ev):+.1f}%、"
              f"{(np.array(ev)>0).mean()*100:.0f}% 是賣在之後更高處(>0=賣早)")

    # 最離譜:叫你別買/出場 但之後63日漲最多的前5天(該勇敢買/該抱)
    caut = df[df['c'].map(lambda x: IMPLIED.get(x, 0)) <= 0].copy()
    worst_miss = caut.nlargest(5, 'f63')[['time','label','f63']]
    print("\n  😖 標『別買/出場』但之後63日漲最多(該勇敢買/該抱的時點):")
    for _, r in worst_miss.iterrows():
        print(f"     {r['time']}  {r['label'][:34]:36} → 之後63日 {r['f63']:+.1f}%")
    # 最離譜:叫你買 但之後63日跌最多的前5天
    buys = df[df['c'].map(lambda x: IMPLIED.get(x, 0)) > 0].copy()
    worst_buy = buys.nsmallest(5, 'f63')[['time','label','f63']]
    print("\n  💸 標『可買』但之後63日跌最多(不該買的時點):")
    for _, r in worst_buy.iterrows():
        print(f"     {r['time']}  {r['label'][:34]:36} → 之後63日 {r['f63']:+.1f}%")
    print()


def main():
    data = json.load(open(KB, encoding='utf-8'))
    tmap = {t['symbol']: t['bars'] for t in data['tickers']}
    for tk in ['QQQ', 'SMH']:
        if tk in tmap:
            analyze(tk, tmap[tk])


if __name__ == '__main__':
    main()
