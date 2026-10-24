"""research_b8_satellite.py - 待辦⑦:個股衛星「上限測試」(2007-2026)
========================================================================
沒有歷史成分股資料 → 用「今天的」S&P500 500 檔回測 19 年。這有生存者偏差(被踢出指數的失敗股不在池子裡),
結果只會「比真實更好看」。所以這是上限測試:
  連這麼有利的條件都贏不了 SPY 擇時 → 衛星沒有 edge(結論可靠)
  贏了 → 只能說「不排除有 edge」(結論不可靠,因為有偏差)
規則照 live(generate_signals.py)的核心:
  進場(t 日收盤判斷、t+1 開盤買,開高 >3% 放棄):126日報酬橫斷面排名前20% + 站上200MA + 20日動能>0
        + 當天最低碰到 10/20/60MA 之一且收盤站回 + 收盤 ≤ 10MA×1.20
  出場:收盤跌破 50MA(隔日開盤賣)或 盤中觸及 進場價−1.5ATR 硬停損
  倉位:每筆風險 1% 淨值(= 1% ÷ 停損距離),單筆上限 25%;組合總風險上限 3%;同時多個訊號取 RS 最高
  成本 0.05%/邊;閒置現金賺國庫券利率
簡化(標註):沒有財報盲區/板塊閘門/信心分級/大盤 regime 縮倉。
對照:SPY 200MA 擇時、SPY 買持、QQQ 200MA 擇時、同規則但「不用 RS 篩選」。
用法: python research_b8_satellite.py
"""
import sys, pickle, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_rot_engine as R
import research_data_2026q3 as D

START = '2007-01-01'
COST = 0.0005


def load():
    d = pickle.load(open('research_cache/sp500_ohlc.pkl', 'rb'))
    idx = d['SPY'].index
    idx = idx[idx >= pd.Timestamp('2005-01-01')]
    tks = [t for t in d if t not in ('SPY', 'QQQ')]
    M = {f: pd.DataFrame({t: d[t][f] for t in tks}).reindex(idx) for f in ('Open', 'High', 'Low', 'Close')}
    return d, idx, tks, M


def backtest(d, idx, tks, M, use_rs=True, gate=False):
    C, O, H, L = (M[f].values for f in ('Close', 'Open', 'High', 'Low'))
    Cdf = M['Close']
    ma = {w: Cdf.rolling(w, min_periods=w).mean().values for w in (10, 20, 50, 60, 200)}
    r126 = (Cdf / Cdf.shift(126) - 1)
    rank = r126.rank(axis=1, pct=True).values
    mom = (Cdf / Cdf.shift(20) - 1).values
    tr = np.maximum(M['High'] - M['Low'], np.maximum((M['High'] - Cdf.shift(1)).abs(), (M['Low'] - Cdf.shift(1)).abs()))
    atr = tr.rolling(14).mean().values
    pull = np.zeros_like(C, dtype=bool)
    for w in (10, 20, 60):
        pull |= (L <= ma[w]) & (C >= ma[w])
    sig = (C > ma[200]) & (mom > 0) & pull & (C <= ma[10] * 1.20)
    if use_rs:
        sig &= rank >= 0.80
    sig &= ~np.isnan(ma[200]) & ~np.isnan(atr)
    if gate:   # 大盤濾網(live 有 regime 縮倉):SPY 收盤在 200MA 之上才開新倉
        spy = d['SPY']['Close'].reindex(idx); g = (spy >= spy.rolling(200).mean()).values
        sig &= g[:, None]
    rf = (D.load()['^IRX']['Close'].reindex(idx).ffill().fillna(0) / 100 / 252).values

    n, k = C.shape
    t0 = int(np.searchsorted(idx, pd.Timestamp(START)))
    eq = np.ones(n); cash = 1.0
    pos = {}                       # j -> dict(sh, entry, stop, risk)
    trades = []
    pending = []                   # 下一日開盤要買的 (j, rs)
    exits = set()
    for t in range(t0, n):
        # 1) 開盤:先處理昨日收盤決定的出場、再進場
        for j in list(exits):
            if j in pos and not np.isnan(O[t, j]):
                p = pos.pop(j); px = O[t, j]
                cash += p['sh'] * px * (1 - COST)
                trades.append(px / p['entry'] - 1)
        exits = set()
        nav_open = cash + sum(p['sh'] * O[t, j] for j, p in pos.items() if not np.isnan(O[t, j]))
        heat = sum(p['risk'] for p in pos.values())
        for j, rs in sorted(pending, key=lambda x: -x[1]):
            if j in pos or heat >= 0.03 - 1e-9:
                continue
            px = O[t, j]; prev = C[t - 1, j]
            if np.isnan(px) or px > prev * 1.03:
                continue                                  # 開高超過 3% 放棄(live 規則)
            stop = px - 1.5 * atr[t - 1, j]
            sd = (px - stop) / px
            if sd <= 0:
                continue
            w = min(0.01 / sd, 0.25)
            cost_amt = w * nav_open
            if cost_amt > cash:
                cost_amt = cash
            if cost_amt <= 0:
                continue
            sh = cost_amt / px / (1 + COST)
            cash -= cost_amt
            pos[j] = {'sh': sh, 'entry': px, 'stop': stop, 'risk': 0.01 if w < 0.25 else 0.25 * sd}
            heat += pos[j]['risk']
        pending = []
        # 2) 盤中:硬停損
        for j in list(pos):
            p = pos[j]
            if not np.isnan(L[t, j]) and L[t, j] <= p['stop']:
                px = min(O[t, j], p['stop']) if not np.isnan(O[t, j]) else p['stop']
                cash += p['sh'] * px * (1 - COST); trades.append(px / p['entry'] - 1); pos.pop(j)
        # 3) 收盤:評價、決定明天的出場/進場
        cash *= (1 + rf[t])
        nav = cash + sum(p['sh'] * C[t, j] for j, p in pos.items() if not np.isnan(C[t, j]))
        eq[t] = nav
        for j in pos:
            if not np.isnan(C[t, j]) and C[t, j] < ma[50][t, j]:
                exits.add(j)
        cand = np.where(sig[t])[0]
        pending = [(j, rank[t, j] if not np.isnan(rank[t, j]) else 0) for j in cand if j not in pos]
    s = pd.Series(eq[t0:], index=idx[t0:]).pct_change().fillna(0)
    return s, np.array(trades)


def timed(d, tk, idx):
    c = d[tk]['Close'].reindex(idx)
    ma = d[tk]['Close'].rolling(200).mean().reindex(idx)
    on = (c >= ma).astype(float).shift(1).fillna(0)
    rf = (D.load()['^IRX']['Close'].reindex(idx).ffill().fillna(0) / 100 / 252)
    r = c.pct_change().fillna(0)
    return (on * r + (1 - on) * rf - on.diff().abs().fillna(0) * COST)


def main():
    d, idx, tks, M = load()
    print(f'股票池:今天的 S&P500 {len(tks)} 檔(⚠️生存者偏差 → 結果是上限)  回測 {START} → {idx[-1].date()}')
    sat, tr = backtest(d, idx, tks, M, use_rs=True)
    sat_nors, tr2 = backtest(d, idx, tks, M, use_rs=True, gate=True)
    ii = sat.index
    rf = (D.load()['^IRX']['Close'].reindex(ii).ffill().fillna(0) / 100 / 252).values
    series = {'個股衛星(live 規則)': sat, '個股衛星+大盤濾網': sat_nors,
              'SPY 200MA擇時': timed(d, 'SPY', idx).reindex(ii), 'QQQ 200MA擇時': timed(d, 'QQQ', idx).reindex(ii),
              'SPY 買進持有': d['SPY']['Close'].reindex(ii).pct_change().fillna(0)}
    IDX = R.sb_indices(len(ii), B=1000, mean_block=63, seed=0)
    rows = []
    for k_, s in series.items():
        m = R.metrics(s.values, rf)
        row = {'策略': k_, 'CAGR%': round(m['CAGR'], 1), 'Vol%': round(m['Vol'], 1),
               '超額Sharpe': round(m['ShEx'], 2), 'MDD%': round(m['MDD'], 1)}
        if k_ != 'SPY 200MA擇時':
            bs = R.boot_delta_sharpe(s.values, series['SPY 200MA擇時'].values, rf, IDX)
            row.update({'Δ vs SPY擇時': round(bs['d'], 3), 'p': round(bs['p'], 3)})
        for a, b, lab in [('2007-01-01', '2012-12-31', '07-12'), ('2013-01-01', '2019-12-31', '13-19'),
                          ('2020-01-01', None, '20-26')]:
            x = R.sub(s, ii, a, b); rfx = pd.Series(rf, index=ii).reindex(x.index).values
            row[f'Sh {lab}'] = round(R.sharpe_ex(x.values, rfx), 2)
        rows.append(row)
    print(pd.DataFrame(rows).to_string(index=False))
    for lab, t in (('live 規則', tr), ('+大盤濾網', tr2)):
        if len(t):
            print(f'交易統計({lab}):{len(t)} 筆、每年 {len(t) / (len(ii) / 252):.0f} 筆、勝率 {np.mean(t > 0) * 100:.0f}%、'
                  f'平均 {t.mean() * 100:+.2f}%、賺賠比 {t[t > 0].mean() / -t[t <= 0].mean():.2f}')
    yr = pd.DataFrame({k_: s.groupby(ii.year).apply(lambda z: (1 + z).prod() - 1) * 100
                       for k_, s in series.items() if k_ in ('個股衛星(live 規則)', '個股衛星+大盤濾網', 'SPY 200MA擇時', 'SPY 買進持有')}).round(1)
    print('\n逐年報酬(%):'); print(yr.to_string())


if __name__ == '__main__':
    main()
