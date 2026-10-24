"""research_b4_pool_hindsight.py - 待辦④:美股池子的後見之明有多大?
========================================================================
現用 US5(SMH/SOXX/QQQ/XLK/SPY)是 2026 年回頭挑的(16 檔候選所有 5 檔組合中排第 147)。
問題:站在每一年「當下」只用當時資料挑池子,能不能拿到 US5 的成績?
  拿不到 → US5 的回測數字有後見之明灌水,未來期望值應該往「事前方法」的水準調低。
比較(全部用同一套:等權、各自 200MA 擇時、RiskTarget 不用(binary)、成本 0.05%、超額 Sharpe):
  A US5(事後挑,現用)
  B BROAD 14 檔全部等權(1998 就存在的產業+大盤,零挑選)
  C 事前挑:每年初從 16 檔候選(BROAD+SMH+SOXX)挑「過去 36 個月報酬」前 5 名,等權持有一年
  D 事前挑:同上,改用「過去 36 個月 Sharpe」前 5 名
  E 只持有 SPY(擇時)/ 只持有 QQQ(擇時)
另外把 A 與 C/D 的差距拆成每一年,看灌水集中在哪些年份。
用法: python research_b4_pool_hindsight.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_rot_engine as R
import research_data_2026q3 as D

CAND = sorted(set(D.US_BROAD + ['SMH', 'SOXX']))
US5 = ['SMH', 'SOXX', 'QQQ', 'XLK', 'SPY']


def sim(P, W, cost=0.0005):
    """直接權重法(所有池子同一套簡化):W[t,j] = t 日收盤決定的權重 → 吃 t+1 報酬;
    其餘放現金賺 rf;換手收成本。不用槽位記帳,避免縮池時「借錢+閒置現金」的假成本。"""
    Wp = np.vstack([np.zeros((1, P.k)), W[:-1]])
    turn = np.abs(np.diff(np.vstack([np.zeros((1, P.k)), Wp]), axis=0)).sum(1)
    r = (Wp * P.ret).sum(1) + (1 - Wp.sum(1)) * P.rf - turn * cost
    r[0] = 0.0
    return pd.Series(r, index=P.idx)


def pool_w(P, mask):
    """池內等權、各自 200MA 擇時:權重 = 1/池大小 × 在趨勢中。"""
    size = mask.sum(1, keepdims=True).clip(min=1)
    return np.where(mask & P.intrend, 1.0 / size, 0.0)


def exante_mask(P, how):
    """每年第一個交易日,用過去 756 日(36 個月)資料挑前 5 名,持有到年底。"""
    m = np.zeros((P.n, P.k), dtype=bool)
    years = P.idx.year
    for y in sorted(set(years)):
        idx = np.where(years == y)[0]
        t0 = idx[0]
        if t0 < 756:
            continue
        win = P.ret[t0 - 756:t0]
        if how == 'ret':
            score = np.prod(1 + win, axis=0) - 1
        else:
            score = win.mean(0) / np.maximum(win.std(0), 1e-9)
        top = np.argsort(-score)[:5]
        m[np.ix_(idx, top)] = True
    return m


def main():
    P = R.build_panel('BROAD', tickers=CAND)
    start = P.idx[756]                               # 事前法需要 36 個月暖身 → 全部從同一天開始比
    print(f'候選 {len(CAND)} 檔:{CAND}\n比較期間 {start.date()} → {P.idx[-1].date()}')
    res = {}
    allm = np.ones((P.n, P.k), dtype=bool)
    us5 = np.isin(np.array(P.tickers), US5)[None, :].repeat(P.n, 0)
    brd = np.isin(np.array(P.tickers), D.US_BROAD)[None, :].repeat(P.n, 0)
    res['A US5(事後挑,現用)'] = sim(P, pool_w(P, us5))
    res['B BROAD 14檔全部(零挑選)'] = sim(P, pool_w(P, brd))
    res['C 事前挑:過去36月報酬前5'] = sim(P, pool_w(P, exante_mask(P, 'ret')))
    res['D 事前挑:過去36月Sharpe前5'] = sim(P, pool_w(P, exante_mask(P, 'sharpe')))
    for tk in ('SPY', 'QQQ'):
        m = (np.array(P.tickers) == tk)[None, :].repeat(P.n, 0)
        res[f'E 只持有 {tk}(擇時)'] = sim(P, pool_w(P, m))
    rows = []
    IDX = R.sb_indices(int((P.idx >= start).sum()), B=1000, mean_block=63, seed=0)
    base = R.sub(res['A US5(事後挑,現用)'], P.idx, str(start.date()))
    rf = pd.Series(P.rf, index=P.idx).reindex(base.index).values
    for k, r in res.items():
        s = R.sub(r, P.idx, str(start.date()))
        m = R.metrics(s, rf)
        row = {'池子': k, 'CAGR%': round(m['CAGR'], 1), 'Vol%': round(m['Vol'], 1),
               '超額Sharpe': round(m['ShEx'], 2), 'MDD%': round(m['MDD'], 1)}
        if not k.startswith('A'):
            bs = R.boot_delta_sharpe(base.values, s.values, rf, IDX)
            row.update({'US5領先ΔSharpe': round(bs['d'], 3), 'p': round(bs['p'], 3)})
        rows.append(row)
    print(pd.DataFrame(rows).to_string(index=False))
    # 逐年差距:A − C
    a = R.sub(res['A US5(事後挑,現用)'], P.idx, str(start.date()))
    c = R.sub(res['C 事前挑:過去36月報酬前5'], P.idx, str(start.date()))
    b = R.sub(res['B BROAD 14檔全部(零挑選)'], P.idx, str(start.date()))
    yr = pd.DataFrame({'US5': a.groupby(a.index.year).apply(lambda x: (1 + x).prod() - 1) * 100,
                       '事前挑(報酬)': c.groupby(c.index.year).apply(lambda x: (1 + x).prod() - 1) * 100,
                       'BROAD全部': b.groupby(b.index.year).apply(lambda x: (1 + x).prod() - 1) * 100}).round(1)
    yr['US5−事前'] = (yr['US5'] - yr['事前挑(報酬)']).round(1)
    print('\n逐年報酬(%):'); print(yr.to_string())
    print(f"\nUS5 勝過事前挑的年數:{int((yr['US5−事前'] > 0).sum())}/{len(yr)};"
          f"2017 年前 {int((yr.loc[:2016, 'US5−事前'] > 0).sum())}/{len(yr.loc[:2016])}、"
          f"2017 年後 {int((yr.loc[2017:, 'US5−事前'] > 0).sum())}/{len(yr.loc[2017:])}")
    # 事前法實際挑了什麼
    m = exante_mask(P, 'ret')
    picks = {y: [P.tickers[j] for j in np.where(m[np.where(P.idx.year == y)[0][0]])[0]]
             for y in sorted(set(P.idx.year)) if m[np.where(P.idx.year == y)[0][0]].any()}
    print('\n事前挑(過去36月報酬前5)每年的池子:')
    for y, p in picks.items():
        print(f'  {y}: {", ".join(p)}')


if __name__ == '__main__':
    main()
