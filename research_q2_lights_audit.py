"""research_q2_lights_audit.py - 問題2:黃燈「別追高」「空手先別進」到底有沒有數據支撐?
========================================================================
兩種黃燈(live 定義,core_status.py / taiwan_status.py):
  別追高(extended):  站上200MA 且 收盤 ≥ 50MA×entry_thr
  空手先別進(expensive/D3): 沒追高,但 RiskTarget 曝險 < 50%(= 離停損線太遠)
                     live 還會在「信用打折後」再檢查一次(expo×信用健康度 < 50%)
綠燈(can_enter): 站上200MA、沒追高、曝險 ≥ 50%

「到場測試」(對空手的人真正的決策):某天處在黃燈,
  A = 當天收盤就買(1單位),抱到系統出場(跌破200MA×buf)或最多 H 日
  B = 等到燈號第一次轉綠才買,抱到同一個終點;等不到就一直現金
  比較 B−A 的對數報酬差。B 贏 = 「等」有根據。
另外算 21/63 日前瞻報酬的條件平均當輔助(會高度重疊,所以看「逐年一致性」而非只看 t 值)。
用法: python research_q2_lights_audit.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_data_2026q3 as D
import research_engine_2026q3 as E

data = D.load()
H = 252


def credit_health(index):
    h = []
    for tk in ['HYG', 'LQD']:
        s = data[tk]['Close']
        ok = (s >= s.rolling(200).mean()).astype(float)
        ok[s.rolling(200).mean().isna()] = np.nan
        h.append(ok.reindex(index).ffill())
    return (h[0] + h[1]) / 2


def classify(tk, tw=False):
    s = data[tk]['Close'].dropna()
    if tw:
        import taiwan_status as T
        s = T.clean(s)
    p = E.params(tk)
    I = E.indicators(s, p)
    c, ma = I['c'], I['ma']
    on = (c >= ma) & ~np.isnan(ma)
    expo_raw = np.clip(p['budget'] / np.maximum(I['stopd'], 0.02), 0, 1.5)
    ch = credit_health(s.index).values
    ext = on & I['extended']
    exp_raw = on & ~I['extended'] & (expo_raw < 0.5)
    # 只因信用打折才變黃的格(raw≥50% 但 ×信用 <50%)
    exp_credit = on & ~I['extended'] & (expo_raw >= 0.5) & (expo_raw * np.nan_to_num(ch, nan=1.0) < 0.5)
    green = on & ~I['extended'] & (expo_raw >= 0.5)
    green_post_credit = green & ~exp_credit
    return s, I, {'extended': ext, 'expensive_raw': exp_raw, 'expensive_credit_only': exp_credit,
                  'green': green, 'green_post_credit': green_post_credit}, expo_raw, ch


def arrival_test(s, I, state_mask, green_mask):
    c = I['c']; n = len(c); intrend = I['intrend']
    logc = np.log(c)
    # 系統出場日:之後第一個 intrend=False 的日
    next_exit = np.full(n, n - 1)
    nxt = n - 1
    for t in range(n - 1, -1, -1):
        if not intrend[t]: nxt = t
        next_exit[t] = nxt
    # 之後第一個綠燈日
    next_green = np.full(n, -1)
    ng = -1
    for t in range(n - 1, -1, -1):
        next_green[t] = ng
        if green_mask[t]: ng = t
    rows = []
    for t in np.where(state_mask)[0]:
        end = min(next_exit[t], t + H, n - 1)
        if end <= t + 5: continue
        A = logc[end] - logc[t]
        g = next_green[t]
        B = (logc[end] - logc[g]) if (g != -1 and g < end) else 0.0
        waited = (g - t) if (g != -1 and g < end) else None
        rows.append((s.index[t], A, B, waited))
    return pd.DataFrame(rows, columns=['date', 'buy_now', 'wait', 'wait_days'])


def fwd(c, h):
    out = np.full(len(c), np.nan)
    out[:-h] = c[h:] / c[:-h] - 1
    return out


def report(tk, tw=False):
    s, I, M, expo_raw, ch = classify(tk, tw)
    f21, f63 = fwd(I['c'], 21), fwd(I['c'], 63)
    lines = [f'\n### {tk}  ({s.index[199].date()} → {s.index[-1].date()})']
    lines.append(f'  {"狀態":22s} {"天數":>6s} {"21日均%":>8s} {"63日均%":>8s} {"63日勝%":>7s}')
    for k, m in M.items():
        if m.sum() < 20:
            lines.append(f'  {k:22s} {int(m.sum()):6d}  (樣本太少)'); continue
        lines.append(f'  {k:22s} {int(m.sum()):6d} {np.nanmean(f21[m])*100:8.2f} {np.nanmean(f63[m])*100:8.2f} '
                     f'{np.nanmean(f63[m] > 0)*100:7.0f}')
    res = {}
    for k in ['extended', 'expensive_raw', 'expensive_credit_only']:
        green = M['green'] if k != 'expensive_credit_only' else M['green_post_credit']
        if M[k].sum() < 20: continue
        df = arrival_test(s, I, M[k], green)
        if len(df) < 20: continue
        d = (df['wait'] - df['buy_now'])
        yr = df.assign(d=d).groupby(df['date'].dt.year)['d'].mean()
        res[k] = df
        lines.append(f'  到場測試 {k:22s}: n={len(df)}  等待−立即買 平均 {d.mean()*100:+.2f}%  中位 {d.median()*100:+.2f}%  '
                     f'等待勝 {np.mean(d > 0)*100:.0f}%  等不到綠燈比例 {df["wait_days"].isna().mean()*100:.0f}%  '
                     f'逐年等待較好 {int((yr > 0).sum())}/{len(yr)} 年')
    print('\n'.join(lines))
    return M, res


if __name__ == '__main__':
    print('=' * 100); print('Q2 燈號稽核:黃燈兩種 vs 綠燈(美股5檔 + 台股5檔)'); print('=' * 100)
    agg = {}
    for tk in E.US_PARAMS:
        M, res = report(tk)
        for k, df in res.items(): agg.setdefault(('US', k), []).append(df)
    for tk in E.TW_PARAMS:
        M, res = report(tk, tw=True)
        for k, df in res.items(): agg.setdefault(('TW', k), []).append(df)
    print('\n' + '=' * 100 + '\n彙總(等權合併各標的)')
    for (mk, k), dfs in agg.items():
        df = pd.concat(dfs); d = df['wait'] - df['buy_now']
        yr = df.assign(d=d).groupby(df['date'].dt.year)['d'].mean()
        print(f'  {mk} {k:22s}: n={len(df):5d}  等待−立即買 平均 {d.mean()*100:+.2f}%  中位 {d.median()*100:+.2f}%  '
              f'等待勝 {np.mean(d > 0)*100:.0f}%  逐年 {int((yr > 0).sum())}/{len(yr)}')
