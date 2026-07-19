"""
research_menu_and_distance.py — 回答 Jason 兩問(2026-07-19):
========================================================================
Q1 憑什麼是這五檔?能不能「廣池取RS最強」或「取最便宜」?
   同一引擎(月選+站上200MA才合格+跌破月中轉現金,shift1,1x不加倉位層)對決:
     M1 現用5檔·RS最強1檔        M2 廣池18檔·RS最強1檔      M3 廣池·RS前3等權
     M4 現用5檔·最便宜1檔(貼MA)  M5 廣池·最便宜3檔等權
   廣池=現用5 + IGV/IWM/DIA/EEM/EFA + 9檔SPDR產業(XLE/XLF/XLV/XLI/XLP/XLU/XLB/XLY/XLRE→XLY等)
Q2 理論驗證:「真強勢股不會回到200MA;回去=動能死了;該買離200MA遠的」
   B1 按距離分桶的前瞻報酬(0-5/5-10/10-20/20-35/>35%),含勝率——動能持續性檢驗
   B2 關鍵子命題:「曾經噴遠(>20%)後跌回貼MA(<5%)」vs「一直貼著磨」vs「現在正噴遠」
      ——回到200MA是不是真的=動能死?
   B3 風險面:每桶「未來63日最大不利波及(跌到停損線的距離)」——報酬要除以你揹的停損距
全部 PIT、誠實尺。
"""
import sys, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, yfinance as yf
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

CUR5 = ['SMH', 'SOXX', 'QQQ', 'XLK', 'SPY']
BROAD = CUR5 + ['IGV', 'IWM', 'DIA', 'EEM', 'EFA',
                'XLE', 'XLF', 'XLV', 'XLI', 'XLP', 'XLU', 'XLB', 'XLY']
START = '2004-01-01'
EVAL = ('2010-01-01', '2026-12-31')
REBAL = 21


def dl(tks):
    raw = yf.download(' '.join(tks), start=START, progress=False, group_by='ticker', auto_adjust=True)
    out = {}
    for tk in tks:
        try:
            s = raw[tk]['Close']; s = (s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s).dropna()
            if len(s) > 300: out[tk] = s
        except Exception:
            pass
    return out


def perf(r, a, b):
    r = r.loc[a:b].dropna()
    sd = r.std(); eq = (1 + r).cumprod()
    y22 = (1 + r.loc['2022-01-01':'2022-12-31']).prod() - 1
    return {'sharpe': r.mean() / sd * np.sqrt(252) if sd > 0 else np.nan,
            'cagr': (eq.iloc[-1] ** (252 / len(r)) - 1) * 100,
            'mdd': (eq / eq.cummax() - 1).min() * 100, 'y22': y22 * 100}


def rotate(prices, pool, metric, topn):
    """月選:合格=站上自己200MA;metric='rs'(voladj126) or 'cheap'(-dist);持有topn等權;
       月中跌破自己200MA→該腳轉現金。回傳日報酬(shift1誠實)。"""
    df = pd.DataFrame({k: prices[k] for k in pool if k in prices})
    ret = df.pct_change()
    ma = df.rolling(200).mean()
    above = df > ma
    r126 = df / df.shift(126) - 1
    vol126 = ret.rolling(126).std() * np.sqrt(252)
    rs = r126 / vol126
    dist = df / ma - 1
    idx = df.index
    w = pd.DataFrame(0.0, index=idx, columns=df.columns)
    held = []
    for i in range(len(idx)):
        if i % REBAL == 0:
            row_ok = above.iloc[i]
            score = (rs.iloc[i] if metric == 'rs' else -dist.iloc[i])
            elig = score[row_ok & score.notna()]
            held = list(elig.nlargest(topn).index) if len(elig) else []
        if held:
            for tk in held:
                if above.iloc[i][tk]:                    # 月中跌破→該腳現金
                    w.iloc[i, w.columns.get_loc(tk)] = 1.0 / max(len(held), 1)
    return (w.shift(1) * ret).sum(axis=1)


def main():
    print("📥 下載", len(BROAD), "檔 ...")
    px = dl(BROAD)
    print("  取得:", sorted(px.keys()))
    print("\n" + "=" * 92)
    print("  Q1 選單對決(同引擎:月選·站上200MA合格·跌破月中轉現金;1x無倉位層;2010-2026)")
    print("=" * 92)
    print(f"  {'變體':30}{'Sharpe':>8}{'CAGR':>8}{'MDD':>9}{'2022':>9}")
    tests = [
        ('M1 現用5檔·RS最強1檔', CUR5, 'rs', 1),
        ('M2 廣池18檔·RS最強1檔', BROAD, 'rs', 1),
        ('M3 廣池·RS前3等權', BROAD, 'rs', 3),
        ('M4 現用5檔·最便宜1檔', CUR5, 'cheap', 1),
        ('M5 廣池·最便宜3檔等權', BROAD, 'cheap', 3),
    ]
    for name, pool, metric, n in tests:
        r = rotate(px, pool, metric, n)
        p = perf(r, *EVAL)
        print(f"  {name:30}{p['sharpe']:>8.2f}{p['cagr']:>7.1f}%{p['mdd']:>8.1f}%{p['y22']:>8.1f}%")
    bh = px['SPY'].pct_change()
    p = perf(bh, *EVAL)
    print(f"  {'(對照)SPY買持':30}{p['sharpe']:>8.2f}{p['cagr']:>7.1f}%{p['mdd']:>8.1f}%{p['y22']:>8.1f}%")

    print("\n" + "=" * 92)
    print("  Q2 距離理論:「該買離200MA遠的?回到MA=動能死?」(5核心檔,PIT,fwd重疊樣本注意)")
    print("=" * 92)
    bands = [(0, 5), (5, 10), (10, 20), (20, 35), (35, 999)]
    agg = {b: {'f21': [], 'f63': [], 'stop': []} for b in bands}
    pull, grind, ext = {'f63': []}, {'f63': []}, {'f63': []}
    for tk in CUR5:
        c = px[tk]; ma = c.rolling(200).mean()
        d = (c / ma - 1) * 100
        f21 = c.shift(-21) / c - 1; f63 = c.shift(-63) / c - 1
        maxd63 = d.rolling(63).max()
        for i in range(200, len(c) - 63):
            di = d.iloc[i]
            if np.isnan(di) or di < 0: continue
            for b in bands:
                if b[0] <= di < b[1]:
                    agg[b]['f21'].append(f21.iloc[i]); agg[b]['f63'].append(f63.iloc[i])
                    agg[b]['stop'].append(di)          # 停損距≈離MA距離
            m63 = maxd63.iloc[i]
            if di < 5 and not np.isnan(m63):
                if m63 > 20: pull['f63'].append(f63.iloc[i])     # 曾噴遠→跌回貼MA
                elif m63 < 10: grind['f63'].append(f63.iloc[i])  # 一直貼著磨
            if di > 20: ext['f63'].append(f63.iloc[i])           # 現在正噴遠
    print(f"  [B1] 距離分桶(5檔彙總):{'桶':6}{'n':>7}{'21日':>8}{'勝率':>6}{'63日':>8}{'勝率':>6}{'平均停損距':>10}{'63日報酬/停損距':>14}")
    for b in bands:
        f1 = np.array(agg[b]['f21']); f3 = np.array(agg[b]['f63']); st = np.mean(agg[b]['stop'])
        if len(f1) == 0: continue
        eff = np.mean(f3) * 100 / max(st, 1)
        print(f"  {'':6}{b[0]}-{'' if b[1]<999 else ''}{min(b[1],99)}%{len(f1):>6}{np.mean(f1)*100:>7.1f}%{np.mean(f1>0)*100:>5.0f}%"
              f"{np.mean(f3)*100:>7.1f}%{np.mean(f3>0)*100:>5.0f}%{st:>9.1f}%{eff:>12.2f}")
    print(f"\n  [B2] 「回到200MA=動能死?」(63日前瞻,5檔彙總)")
    for name, v in [('曾噴遠>20%→跌回貼MA(<5%)', pull), ('一直貼著磨(<5%,從未>10%)', grind), ('現在正噴遠(>20%)', ext)]:
        a = np.array(v['f63'])
        if len(a):
            print(f"     {name:28} n={len(a):5d}  63日均 {np.mean(a)*100:+5.1f}%  勝率{np.mean(a>0)*100:3.0f}%  最差 {np.min(a)*100:+.0f}%")


if __name__ == '__main__':
    main()
