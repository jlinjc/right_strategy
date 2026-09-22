"""research_q1_rotation_audit.py - 問題1:輪動選標的有沒有根據?多久換一次?為什麼是這5檔?
========================================================================
E1 美股5檔:輪動(各種換倉頻率) vs 等權 vs 單押 vs 隨機挑(同規則,檢驗 RS 排名本身有沒有用)
E2 換倉頻率統計:live 規則(持滿21交易日)實際多久換一次、平均抱多久
E3 無後見之明對照池(1999 就存在的產業/大盤 ETF 14 檔):輪動 edge 是普遍的,還是只因為池子裡放了科技?
E4 為什麼是這5檔:16 檔候選中所有 5 檔組合跑同一套輪動,看現用組合排第幾
E5 台股:同樣比較
用法: python research_q1_rotation_audit.py
"""
import sys, itertools, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_data_2026q3 as D
import research_engine_2026q3 as E

data = D.load()
WINDOWS = [('FULL', None, None), ('2003-09', '2003-01-01', '2009-12-31'),
           ('2010-16', '2010-01-01', '2016-12-31'), ('2017-26', '2017-01-01', None)]


def sub(r, a, b):
    s = r
    if a: s = s[s.index >= pd.Timestamp(a)]
    if b: s = s[s.index <= pd.Timestamp(b)]
    return s


def compare(tickers, tw=False, title='', random_n=100, cost=None):
    cost = cost if cost is not None else (0.001 if tw else 0.0005)
    idx, P = E.panel(data, tickers, tw=tw)
    cash = E.cash_daily(data, idx) if not tw else np.zeros(len(idx))  # 台股現金不計息(保守)
    print(f'\n{"=" * 100}\n{title}  期間 {idx[0].date()} → {idx[-1].date()}  ({len(idx)/252:.1f} 年)\n{"=" * 100}')
    series = {}
    for mode in ['daily', 'lock', 'monthly', 'quarterly']:
        W, sw = E.rotation_weights(idx, P, tickers, mode=mode)
        series[f'輪動-{mode}'] = (E.run_weights(idx, P, W, tickers, cash, cost), sw)
    # 隨機挑選(同 lock 規則)= RS 排名的對照組
    rnd = []
    for s in range(random_n):
        W, sw = E.rotation_weights(idx, P, tickers, mode='random', seed=s)
        rnd.append(E.run_weights(idx, P, W, tickers, cash, cost))
    # 等權:每檔各自擇時,各占 1/k 資金
    Wt = E.timed_single_weights(P, tickers) / len(tickers)
    series['等權各自擇時'] = (E.run_weights(idx, P, Wt, tickers, cash, cost), None)
    for j, tk in enumerate(tickers):
        Ws = np.zeros((len(idx), len(tickers))); Ws[:, j] = P[tk]['intrend'].values
        series[f'單押擇時 {tk}'] = (E.run_weights(idx, P, Ws, tickers, cash, cost), None)
    for tk in tickers:
        series[f'買持 {tk}'] = (P[tk]['ret'].fillna(0), None)
    yrs = len(idx) / 252
    for wn, a, b in WINDOWS:
        rows = []
        for k, (r, sw) in series.items():
            m = E.metrics(sub(r, a, b), k)
            if m and sw is not None and wn == 'FULL':
                m['換倉/年'] = round(sw / yrs, 1)
            rows.append(m)
        rs = [E.metrics(sub(r, a, b)) for r in rnd]
        if rs and rs[0]:
            rows.append({'label': f'隨機挑(同鎖規則,{random_n}次)中位',
                         'CAGR%': np.median([x['CAGR%'] for x in rs]), 'Sharpe': np.median([x['Sharpe'] for x in rs]),
                         'MDD%': np.median([x['MDD%'] for x in rs]), 'Vol%': np.median([x['Vol%'] for x in rs])})
            lk = E.metrics(sub(series['輪動-lock'][0], a, b))
            pct = np.mean([x['Sharpe'] < lk['Sharpe'] for x in rs]) * 100
            rows.append({'label': f'→ live輪動 Sharpe 贏過 {pct:.0f}% 的隨機挑選'})
        if len(sub(series['輪動-lock'][0], a, b)) < 252:
            continue
        print(f'\n-- 視窗 {wn} --')
        print(E.table(rows))
    return idx, P, series


def hold_stats(tickers, tw=False):
    idx, P = E.panel(data, tickers, tw=tw)
    W, sw = E.rotation_weights(idx, P, tickers, mode='lock')
    held = np.where(W.sum(1) > 0, W.argmax(1), -1)
    runs, cur, ln = [], held[0], 0
    for h in held:
        if h == cur: ln += 1
        else:
            runs.append((cur, ln)); cur, ln = h, 1
    runs.append((cur, ln))
    hr = [l for h, l in runs if h >= 0]
    cash = [l for h, l in runs if h < 0]
    share = {tickers[j]: round((held == j).mean() * 100, 1) for j in range(len(tickers))}
    share['現金'] = round((held < 0).mean() * 100, 1)
    print(f'\n[換倉頻率] {tickers}: 持有段數 {len(hr)},中位抱 {np.median(hr):.0f} 交易日、平均 {np.mean(hr):.0f} 日、'
          f'最短 {min(hr)} / 最長 {max(hr)};空手段 {len(cash)} 次')
    print(f'  各標的持有時間占比 %: {share}')
    # 近 3 年逐段列出
    t0 = 0; seg = []
    for h, l in runs:
        if idx[t0] >= pd.Timestamp('2023-01-01'):
            seg.append(f'{idx[t0].date()} {tickers[h] if h >= 0 else "現金"} {l}日')
        t0 += l
    print('  2023以來持有紀錄:', ' → '.join(seg))


if __name__ == '__main__':
    compare(list(E.US_PARAMS), title='E1 美股現用5檔')
    hold_stats(list(E.US_PARAMS))

    broad = D.US_BROAD
    compare(broad, title='E3 無後見之明池:14檔1998就存在的產業/大盤ETF(非事後挑科技)', random_n=100)

    # E4 16 檔候選(廣池+SMH+SOXX)所有 5 檔組合,live 輪動規則,2003起
    cand = sorted(set(broad + ['SMH', 'SOXX']))
    idx, P = E.panel(data, cand)
    cash = E.cash_daily(data, idx)
    res = []
    for combo in itertools.combinations(cand, 5):
        c = list(combo)
        W, _ = E.rotation_weights(idx, {k: P[k] for k in c}, c, mode='lock')
        r = E.run_weights(idx, {k: P[k] for k in c}, W, c, cash)
        m = E.metrics(r); m['combo'] = ','.join(c)
        m['OOS前半Sharpe'] = E.metrics(r[r.index < pd.Timestamp('2014-07-01')])['Sharpe']
        m['OOS後半Sharpe'] = E.metrics(r[r.index >= pd.Timestamp('2014-07-01')])['Sharpe']
        res.append(m)
    df = pd.DataFrame(res).sort_values('Sharpe', ascending=False).reset_index(drop=True)
    cur = ','.join(sorted(E.US_PARAMS))
    rank = df.index[df['combo'] == cur][0] + 1
    print(f'\n{"=" * 100}\nE4 16檔候選 {cand}\n   所有 {len(df)} 種5檔組合跑同一套 live 輪動({idx[0].date()}起)')
    print(f'   現用組合 {cur}: 全期 Sharpe 排第 {rank}/{len(df)}  →', df.iloc[rank - 1][['CAGR%', 'Sharpe', 'MDD%']].to_dict())
    print(f'   全組合中位: CAGR {df["CAGR%"].median()}%  Sharpe {df["Sharpe"].median()}  MDD {df["MDD%"].median()}%')
    # 前半段挑組合 → 後半段表現(模擬「站在2014年用當時資料挑5檔」)
    top_first = df.sort_values('OOS前半Sharpe', ascending=False).head(20)
    print(f'   用前半段(→2014-06)挑出的前20組合,後半段 Sharpe 中位 {top_first["OOS後半Sharpe"].median()}'
          f' vs 全組合後半中位 {df["OOS後半Sharpe"].median()}(前半挑的在後半還有沒有用)')
    cur_row = df[df['combo'] == cur].iloc[0]
    print(f'   現用組合 前半 Sharpe {cur_row["OOS前半Sharpe"]}(前半排第 '
          f'{int((df["OOS前半Sharpe"] > cur_row["OOS前半Sharpe"]).sum()) + 1}) / 後半 {cur_row["OOS後半Sharpe"]}')
    print('   前10名:'); print(df.head(10)[['combo', 'CAGR%', 'Sharpe', 'MDD%', 'OOS前半Sharpe', 'OOS後半Sharpe']].to_string(index=False))

    compare(['0050.TW', '0052.TW', '006208.TW', '0051.TW'], tw=True, title='E5a 台股4檔(00757歷史不足,先排除;長期)')
    compare(list(E.TW_PARAMS), tw=True, title='E5b 台股現用5檔(含00757,期間短)')
    hold_stats(list(E.TW_PARAMS), tw=True)
