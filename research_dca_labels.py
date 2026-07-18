"""
research_dca_labels.py — 標籤導引定投 vs 無腦定投 vs 大盤定投(Jason 目標:長期持續買進、賺贏大盤)
========================================================================
情境:每月固定有 1 單位新資金。比較三種「細節操作」(全 PIT,用 kbar 標籤=當天收盤後可知):
  PLAIN  無腦定投:每月月底全數買入。
  LABEL  標籤導引:綠(可買/小買)→ 投入全部可用現金(當月+之前存下的);
                  lime(偏貴/追高可小額)→ 只投 1 單位;amber(觀望/壓/追)→ 投 0.5;紅(出場/清倉)→ 存現金。
  SPY-PLAIN 大盤無腦定投(=「賺贏大盤」的對照組)。
同一總投入、同一時間軸(2016-01 起,kbar JSON)。指標:終值/投入(MOIC)、帳戶最大回撤。
誠實註:2016-26 大多頭樣本;紅燈存現金在長多頭是拖累、在熊市是保護,看全期淨效果。
"""
import sys, json, warnings
warnings.filterwarnings('ignore')
import numpy as np
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

KB = 'Web_Dashboard/kbar_annotations.json'


def month_ends(times):
    idx = []
    for i in range(len(times) - 1):
        if times[i][:7] != times[i + 1][:7]:
            idx.append(i)
    idx.append(len(times) - 1)
    return idx


def run(bars, mode):
    times = [b['time'] for b in bars]
    closes = [b['close'] for b in bars]
    tones = [b['tone'] for b in bars]
    mends = month_ends(times)
    shares = 0.0; cash = 0.0; invested = 0.0
    eq = []
    mset = set(mends)
    for i, b in enumerate(bars):
        if i in mset:
            cash += 1.0; invested += 1.0
            if mode == 'PLAIN':
                deploy = cash
            else:
                t = tones[i]
                if t == 'green':   deploy = cash
                elif t == 'lime':  deploy = min(cash, 1.0)
                elif t == 'amber': deploy = min(cash, 0.5)
                else:              deploy = 0.0
            shares += deploy / closes[i]; cash -= deploy
        eq.append(shares * closes[i] + cash)
    eq = np.array(eq)
    peak = np.maximum.accumulate(np.maximum(eq, 1e-9))
    mdd = float((eq / peak - 1).min())
    return invested, eq[-1], mdd


def main():
    d = json.load(open(KB, encoding='utf-8'))
    tmap = {t['symbol']: t['bars'] for t in d['tickers']}
    spy_inv, spy_fin, spy_mdd = run(tmap['SPY'], 'PLAIN')
    print("=" * 86)
    print(f"  標籤導引定投 vs 無腦定投(2016-01→,每月1單位;對照=SPY無腦定投 MOIC {spy_fin/spy_inv:.2f}x)")
    print("=" * 86)
    print(f"  {'標的':10}{'策略':10}{'投入':>7}{'終值':>9}{'MOIC':>8}{'帳戶MDD':>9}{'vs SPY定投':>11}")
    for sym in ['QQQ', 'SMH', 'SOXX', 'XLK', 'SPY', '006208.TW', '0050.TW']:
        if sym not in tmap:
            continue
        for mode in ['PLAIN', 'LABEL']:
            inv, fin, mdd = run(tmap[sym], mode)
            moic = fin / inv
            edge = (moic / (spy_fin / spy_inv) - 1) * 100
            print(f"  {sym:10}{mode:10}{inv:>7.0f}{fin:>9.1f}{moic:>7.2f}x{mdd*100:>8.1f}%{edge:>+10.1f}%")
        print()


if __name__ == '__main__':
    main()
